# app/model/transcription/session.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeAlias

from app.model.core.config.config import AppConfig
from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.entities import TranscriptionSessionRequest
from app.model.core.domain.results import SessionResult
from app.model.download.domain import SourceAccessInterventionRequest
from app.model.download.policy import DownloadPolicy
from app.model.sources.probe import is_url_source
from app.model.transcription import workspace
from app.model.transcription.policy import TranscriptionOutputPolicy

from .progress import SessionProgressTracker, entry_source_key, register_session_entries

_LOG = logging.getLogger(__name__)

ProgressFn: TypeAlias = Callable[[int], None]
CancelCheckFn: TypeAlias = Callable[[], bool]
ItemStatusFn: TypeAlias = Callable[[str, str], None]
ItemProgressFn: TypeAlias = Callable[[str, int], None]
ItemPathUpdateFn: TypeAlias = Callable[[str, str], None]
TranscriptReadyFn: TypeAlias = Callable[[str, str], None]
ItemErrorFn: TypeAlias = Callable[[str, str, dict[str, Any]], None]
ItemOutputDirFn: TypeAlias = Callable[[str, str], None]
ConflictResolverFn: TypeAlias = Callable[[str, str], tuple[str, str, bool]]
AccessInterventionResolverFn: TypeAlias = Callable[
    [str, SourceAccessInterventionRequest, str | None, str | None, str | None, str | None],
    tuple[str | None, str | None, str | None, str | None],
]

SourceEntry: TypeAlias = str | dict[str, Any]


@dataclass(frozen=True)
class EntryRequest:
    """Normalized source entry used before materialization."""

    source_key: str
    forced_stem: str | None
    audio_track_id: str | None
    is_url: bool


@dataclass(frozen=True)
class MaterializedWorkItem:
    """Single source resolved to a concrete local path."""

    source_key: str
    source_path: Path
    forced_stem: str | None


@dataclass(frozen=True)
class MaterializeBatchResult:
    """Materialized work items and control flags for the current session."""

    work: list[MaterializedWorkItem]
    had_errors: bool
    was_cancelled: bool


@dataclass(frozen=True)
class SessionOptions:
    """Resolved session options reused across all work items."""

    output_mode_ids: list[str]
    translate_requested: bool
    want_translate: bool
    want_timestamps: bool
    tgt_lang: str
    default_lang: str
    profile: str
    runtime_profile: dict[str, Any]
    chunk_len_s: int
    stride_len_s: int
    ignore_warning: bool
    url_download_kind: str
    url_download_ext: str
    url_keep_download: bool
    url_download_quality: str


@dataclass
class SessionRuntime:
    """Session-scoped runtime state shared across all items."""

    session_dir: str
    session_id: str
    options: SessionOptions
    tracker: SessionProgressTracker
    downloaded_to_delete: set[Path]


@dataclass(frozen=True)
class SessionCallbacks:
    """UI and worker callbacks used throughout the session."""

    item_status: ItemStatusFn
    item_progress: ItemProgressFn
    item_path_update: ItemPathUpdateFn
    transcript_ready: TranscriptReadyFn
    item_error: ItemErrorFn
    item_output_dir: ItemOutputDirFn
    conflict_resolver: ConflictResolverFn
    access_intervention_resolver: AccessInterventionResolverFn | None
    cancel_check: CancelCheckFn


@dataclass(frozen=True)
class ItemProcessResult:
    """Outcome of processing a single materialized transcription item."""

    processed_any: bool
    had_errors: bool
    was_cancelled: bool
    apply_all: tuple[str, str] | None


def build_session_options(*, session_request: TranscriptionSessionRequest) -> SessionOptions:
    """Resolve and normalize session-level transcription options."""

    model_cfg = AppConfig.transcription_model_raw_cfg_dict()
    output_mode_ids = [
        str(mode_id or "").strip().lower()
        for mode_id in session_request.output_formats
        if str(mode_id or "").strip()
    ]
    if not output_mode_ids:
        output_mode_ids = list(AppConfig.transcription_output_mode_ids())

    output_modes = [
        TranscriptionOutputPolicy.get_transcription_output_mode(str(mode_id))
        for mode_id in output_mode_ids
    ]
    want_timestamps = any(
        bool(mode.get("timestamps", False)) or str(mode.get("ext", "")).strip().lower() == "srt"
        for mode in output_modes
    )

    translate_requested = bool(session_request.translate_after_transcription)
    tgt_lang = LanguagePolicy.normalize_policy_value(session_request.target_language or "")
    want_translate = bool(translate_requested and tgt_lang and not LanguagePolicy.is_auto(tgt_lang))
    default_lang = LanguagePolicy.normalize_policy_value(session_request.source_language or LanguagePolicy.AUTO)

    audio_ext = str(session_request.url_audio_ext or AppConfig.transcription_url_audio_ext()).strip().lower()
    video_ext = str(session_request.url_video_ext or AppConfig.transcription_url_video_ext()).strip().lower()
    download_audio_only = bool(session_request.download_audio_only)
    url_download_kind = "audio" if download_audio_only else "video"
    url_download_ext = audio_ext if download_audio_only else video_ext
    url_keep_download = bool(
        session_request.url_keep_audio if download_audio_only else session_request.url_keep_video
    )

    advanced_cfg = model_cfg.get("advanced") if isinstance(model_cfg.get("advanced"), dict) else {}
    profile = RuntimeProfiles.normalize_transcription_profile(
        model_cfg.get("profile", RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE)
    )
    runtime_profile = RuntimeProfiles.resolve_transcription_runtime(profile=profile, overrides=advanced_cfg)
    return SessionOptions(
        output_mode_ids=output_mode_ids,
        translate_requested=translate_requested,
        want_translate=want_translate,
        want_timestamps=want_timestamps,
        tgt_lang=tgt_lang,
        default_lang=default_lang,
        profile=profile,
        runtime_profile=runtime_profile,
        chunk_len_s=int(runtime_profile.get("chunk_length_s", 45) or 45),
        stride_len_s=int(runtime_profile.get("stride_length_s", 5) or 5),
        ignore_warning=bool(model_cfg.get("ignore_warning", False)),
        url_download_kind=url_download_kind,
        url_download_ext=url_download_ext,
        url_keep_download=url_keep_download,
        url_download_quality=DownloadPolicy.URL_DOWNLOAD_DEFAULT_QUALITY,
    )


def prepare_session_runtime(
    *,
    entries: list[SourceEntry],
    session_request: TranscriptionSessionRequest,
    progress: ProgressFn,
) -> SessionRuntime:
    """Create runtime state shared by one batch transcription session."""

    session_dir = workspace.plan_session()
    options = build_session_options(session_request=session_request)
    tracker = SessionProgressTracker(progress)
    register_session_entries(
        tracker=tracker,
        entries=entries,
        want_translate=options.want_translate,
    )
    return SessionRuntime(
        session_dir=str(session_dir),
        session_id=Path(session_dir).name,
        options=options,
        tracker=tracker,
        downloaded_to_delete=set(),
    )


def build_session_callbacks(
    *,
    item_status: ItemStatusFn,
    item_progress: ItemProgressFn,
    item_path_update: ItemPathUpdateFn,
    transcript_ready: TranscriptReadyFn,
    item_error: ItemErrorFn,
    item_output_dir: ItemOutputDirFn,
    conflict_resolver: ConflictResolverFn,
    access_intervention_resolver: AccessInterventionResolverFn | None,
    cancel_check: CancelCheckFn,
) -> SessionCallbacks:
    """Bundle worker callbacks used throughout one batch session."""

    return SessionCallbacks(
        item_status=item_status,
        item_progress=item_progress,
        item_path_update=item_path_update,
        transcript_ready=transcript_ready,
        item_error=item_error,
        item_output_dir=item_output_dir,
        conflict_resolver=conflict_resolver,
        access_intervention_resolver=access_intervention_resolver,
        cancel_check=cancel_check,
    )


def count_session_sources(*, entries: list[SourceEntry]) -> tuple[int, int]:
    """Count local and remote sources queued for one session."""

    local_count = 0
    url_count = 0
    for entry in entries:
        key = entry_source_key(entry)
        if is_url_source(key):
            url_count += 1
        else:
            local_count += 1
    return local_count, url_count


def log_session_plan(*, runtime: SessionRuntime, entries: list[SourceEntry]) -> None:
    """Log the planned shape of one transcription session."""

    local_count, url_count = count_session_sources(entries=entries)
    _LOG.info("Transcription session started. items=%d", len(entries))
    _LOG.debug(
        (
            "Transcription session planned. session_id=%s items=%s local_count=%s url_count=%s "
            "translate_requested=%s translate_effective=%s source_language=%s "
            "target_language=%s output_modes=%s"
        ),
        runtime.session_id,
        len(entries),
        local_count,
        url_count,
        bool(runtime.options.translate_requested),
        bool(runtime.options.want_translate),
        runtime.options.default_lang or LanguagePolicy.AUTO,
        runtime.options.tgt_lang or LanguagePolicy.AUTO,
        ",".join(runtime.options.output_mode_ids),
    )


def finish_session(
    *,
    runtime: SessionRuntime,
    processed_any: bool,
    had_errors: bool,
    was_cancelled: bool,
    cleanup_downloads: Callable[[set[Path]], None],
) -> SessionResult:
    """Finalize one transcription session and return its result."""

    cleanup_downloads(runtime.downloaded_to_delete)
    if not processed_any:
        workspace.rollback_session_if_empty()
    workspace.end_session()
    _LOG.info(
        "Transcription session finished. processed=%s errors=%s cancelled=%s",
        processed_any,
        had_errors,
        was_cancelled,
    )
    _LOG.debug(
        "Transcription session summary. session_id=%s processed=%s errors=%s cancelled=%s cleanup_downloads=%s",
        runtime.session_id,
        bool(processed_any),
        bool(had_errors),
        bool(was_cancelled),
        len(runtime.downloaded_to_delete),
    )
    return SessionResult(str(runtime.session_dir), processed_any, had_errors, was_cancelled)


def finish_session_if_no_work(
    *,
    runtime: SessionRuntime,
    materialized: MaterializeBatchResult,
    cleanup_downloads: Callable[[set[Path]], None],
) -> SessionResult | None:
    """Finish early when materialization produced no remaining work."""

    if not materialized.was_cancelled and materialized.work:
        return None
    _LOG.info(
        "Transcription session ended early. session_id=%s cancelled=%s work_items=%s errors=%s",
        runtime.session_id,
        bool(materialized.was_cancelled),
        len(materialized.work),
        bool(materialized.had_errors),
    )
    return finish_session(
        runtime=runtime,
        processed_any=False,
        had_errors=bool(materialized.had_errors),
        was_cancelled=bool(materialized.was_cancelled),
        cleanup_downloads=cleanup_downloads,
    )
