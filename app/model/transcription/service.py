# app/model/transcription/service.py
from __future__ import annotations

import logging
import shutil
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeAlias, TypeVar

from app.model.core.config.config import AppConfig
from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.entities import TranscriptionSessionRequest
from app.model.core.domain.errors import AppError, OperationCancelled
from app.model.core.domain.results import SessionResult
from app.model.core.utils.path_utils import ensure_unique_path
from app.model.core.utils.string_utils import sanitize_filename, sanitize_url_for_log
from app.model.download.domain import SourceAccessInterventionRequest, DownloadError, SourceAccessInterventionRequired
from app.model.download.policy import DownloadPolicy
from app.model.download.service import DownloadService
from app.model.sources.probe import is_url_source
from app.model.transcription import workspace
from app.model.transcription.runtime import pick_source_language, transcribe_wav
from app.model.transcription.io import AudioExtractor
from app.model.transcription.policy import TranscriptionOutputPolicy
from app.model.transcription.workspace import OutputDirectoryResolution, OutputResolver
from app.model.transcription.whisper import debug_source_key
from app.model.transcription.writer import TextPostprocessor, TranscriptWriter
from app.model.translation.service import TranslationError, TranslationService

_LOG = logging.getLogger(__name__)

ProgressFn = Callable[[int], None]
CancelCheckFn = Callable[[], bool]
ItemStatusFn = Callable[[str, str], None]
ItemProgressFn = Callable[[str, int], None]
ItemPathUpdateFn = Callable[[str, str], None]
TranscriptReadyFn = Callable[[str, str], None]
ItemErrorFn = Callable[[str, str, dict[str, Any]], None]
ItemOutputDirFn = Callable[[str, str], None]
ConflictResolverFn = Callable[[str, str], tuple[str, str, bool]]
AccessInterventionResolverFn = Callable[
    [str, SourceAccessInterventionRequest, str | None, str | None, str | None, str | None],
    tuple[str | None, str | None, str | None, str | None],
]


SourceEntry: TypeAlias = str | dict[str, Any]
_DownloadResult = TypeVar("_DownloadResult")


@dataclass(frozen=True)
class _EntryRequest:
    """Normalized source entry used before materialization."""

    source_key: str
    forced_stem: str | None
    audio_track_id: str | None
    is_url: bool


@dataclass(frozen=True)
class _MaterializedWorkItem:
    """Single source resolved to a concrete local path."""

    source_key: str
    source_path: Path
    forced_stem: str | None


@dataclass(frozen=True)
class _MaterializeBatchResult:
    """Materialized work items and control flags for the current session."""

    work: list[_MaterializedWorkItem]
    had_errors: bool
    was_cancelled: bool


def build_entry_request(entry: SourceEntry) -> _EntryRequest:
    if isinstance(entry, dict):
        source_key = str(entry.get("src") or "")
        forced_stem = str(entry.get("stem") or "").strip() or None
        audio_track_id = str(entry.get("audio_track_id") or "").strip() or None
    else:
        source_key = str(entry or "")
        forced_stem = None
        audio_track_id = None
    return _EntryRequest(
        source_key=source_key,
        forced_stem=forced_stem,
        audio_track_id=audio_track_id,
        is_url=is_url_source(source_key),
    )


def materialize_local_entry(
    request: _EntryRequest,
    *,
    missing_error_factory: Callable[[Path], Exception],
) -> _MaterializedWorkItem:
    path = Path(request.source_key).expanduser()
    if not path.exists():
        raise missing_error_factory(path)
    return _MaterializedWorkItem(
        source_key=request.source_key,
        source_path=path,
        forced_stem=request.forced_stem,
    )


@dataclass
class _ItemPlan:
    """Per-item progress weighting used by the session progress tracker."""

    has_download: bool
    has_translate: bool
    weight: float
    stage_pct: dict[str, int]


class _ProgressTracker:
    """Tracks global progress across multiple items and stages."""

    _STAGES = ("download", "preprocess", "transcribe", "translate", "save")
    _BASE_WEIGHTS = {"download": 0.10, "preprocess": 0.05, "transcribe": 0.60, "translate": 0.20, "save": 0.05}

    def __init__(self, progress_cb: ProgressFn) -> None:
        self._cb = progress_cb
        self._plans: dict[str, _ItemPlan] = {}
        self._last_pct = 0

    def register(self, key: str, *, has_download: bool, has_translate: bool, weight: float = 1.0) -> None:
        self._plans[str(key)] = _ItemPlan(
            has_download=bool(has_download),
            has_translate=bool(has_translate),
            weight=float(max(0.0001, weight)),
            stage_pct={stage: 0 for stage in self._STAGES},
        )

    def set_weight(self, key: str, *, weight: float) -> None:
        plan_key = str(key)
        if plan_key in self._plans:
            self._plans[plan_key].weight = float(max(0.0001, weight))

    def rename_key(self, old_key: str, new_key: str) -> None:
        old_plan_key = str(old_key)
        new_plan_key = str(new_key)
        if old_plan_key == new_plan_key:
            return
        plan = self._plans.pop(old_plan_key, None)
        if plan is None:
            return
        self._plans[new_plan_key] = plan

    def update(self, key: str, stage: str, pct: int) -> None:
        plan_key = str(key)
        if plan_key not in self._plans:
            return
        stage_name = str(stage)
        if stage_name not in self._STAGES:
            return
        self._plans[plan_key].stage_pct[stage_name] = int(max(0, min(100, pct)))
        self._emit()

    def mark_done(self, key: str) -> None:
        plan_key = str(key)
        if plan_key in self._plans:
            for stage in self._STAGES:
                self._plans[plan_key].stage_pct[stage] = 100
        self._emit()

    def _emit(self) -> None:
        if not self._plans:
            self._cb(0)
            return

        total_weight = 0.0
        total_progress = 0.0
        for plan in self._plans.values():
            weights = dict(self._BASE_WEIGHTS)
            if not plan.has_download:
                weights["download"] = 0.0
            if not plan.has_translate:
                weights["translate"] = 0.0

            norm = sum(weights.values()) or 1.0
            for stage in weights:
                weights[stage] = weights[stage] / norm

            item_progress = 0.0
            for stage, weight in weights.items():
                item_progress += (plan.stage_pct.get(stage, 0) / 100.0) * weight

            total_weight += plan.weight
            total_progress += item_progress * plan.weight

        pct = int(round((total_progress / max(0.0001, total_weight)) * 100))
        pct = max(0, min(100, pct))
        if pct < self._last_pct:
            pct = self._last_pct
        self._last_pct = pct
        self._cb(pct)


class TranscriptionError(AppError):
    """Key-based error used for i18n-friendly transcription failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


@dataclass(frozen=True)
class _SessionOptions:
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
class _SessionRuntime:
    """Session-scoped runtime state shared across all items."""

    session_dir: str
    session_id: str
    options: _SessionOptions
    tracker: "_ProgressTracker"
    downloaded_to_delete: set[Path]


@dataclass(frozen=True)
class _SessionCallbacks:
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
class _ItemProcessResult:
    """Outcome of processing a single materialized transcription item."""

    processed_any: bool
    had_errors: bool
    was_cancelled: bool
    apply_all: tuple[str, str] | None


class TranscriptionService:
    """Runs transcription sessions using an already-built ASR pipeline."""

    @staticmethod
    def _transcription_error(key: str, **params: Any) -> TranscriptionError:
        return TranscriptionError(key, **params)

    def __init__(self) -> None:
        self._download = DownloadService()
        self._translator = TranslationService()
        self._post = TextPostprocessor()

    @staticmethod
    def _estimate_item_weight(key: str) -> float:
        source_key = str(key or "")
        if not source_key or is_url_source(source_key):
            return 15.0
        try:
            path = Path(source_key)
        except (OSError, RuntimeError, TypeError, ValueError):
            return 1.0
        if not path.exists() or not path.is_file():
            return 1.0
        dur = AudioExtractor.probe_duration(path)
        if isinstance(dur, (int, float)) and dur > 0:
            return float(max(15.0, min(3600.0, float(dur))))
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        mb = float(size) / (1024.0 * 1024.0) if size else 0.0
        if mb > 0:
            return float(max(15.0, min(3600.0, mb * 10.0)))
        return 1.0

    @staticmethod
    def _entry_source_key(entry: SourceEntry) -> str:
        return str(entry.get("src") if isinstance(entry, dict) else entry)

    @staticmethod
    def _build_session_options(*, session_request: TranscriptionSessionRequest) -> _SessionOptions:
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
        return _SessionOptions(
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

    @staticmethod
    def _build_entry_request(entry: SourceEntry) -> _EntryRequest:
        return build_entry_request(entry)

    def _prepare_session_runtime(
        self,
        *,
        entries: list[SourceEntry],
        session_request: TranscriptionSessionRequest,
        progress: ProgressFn,
    ) -> _SessionRuntime:
        session_dir = workspace.plan_session()
        session_id = Path(session_dir).name
        options = self._build_session_options(session_request=session_request)
        tracker = _ProgressTracker(progress)
        self._register_session_tracker(
            tracker=tracker,
            entries=entries,
            want_translate=options.want_translate,
        )
        return _SessionRuntime(
            session_dir=str(session_dir),
            session_id=session_id,
            options=options,
            tracker=tracker,
            downloaded_to_delete=set(),
        )

    def _finish_session(
        self,
        *,
        runtime: _SessionRuntime,
        processed_any: bool,
        had_errors: bool,
        was_cancelled: bool,
    ) -> SessionResult:
        self._cleanup_downloaded_sources(downloaded_to_delete=runtime.downloaded_to_delete)
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

    @staticmethod
    def _emit_materialize_error(
        *,
        request: _EntryRequest,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
        error: Exception,
    ) -> None:
        _LOG.debug(
            "Transcription materialize failed. session_id=%s source_key=%s detail=%s",
            runtime.session_id,
            debug_source_key(request.source_key),
            str(error),
        )
        TranscriptionService._emit_item_error(
            callbacks=callbacks,
            key=request.source_key,
            error=error,
        )

    @staticmethod
    def _emit_item_error(
        *,
        callbacks: _SessionCallbacks,
        key: str,
        error: Exception,
    ) -> None:
        if isinstance(error, AppError):
            callbacks.item_error(key, str(error.key), dict(error.params or {}))
            return
        callbacks.item_error(key, "error.generic", {"detail": str(error)})

    @staticmethod
    def _report_item_stage_progress(
        *,
        tracker: _ProgressTracker,
        key: str,
        stage: str,
        pct: int,
        item_progress: ItemProgressFn | None = None,
    ) -> None:
        pct = int(pct)
        tracker.update(key, stage, pct)
        if item_progress is not None:
            item_progress(key, pct)

    @staticmethod
    def _build_stage_progress_callback(
        *,
        tracker: _ProgressTracker,
        key: str,
        stage: str,
        item_progress: ItemProgressFn | None = None,
    ) -> Callable[..., None]:
        def _report(pct: int, *_args: Any) -> None:
            TranscriptionService._report_item_stage_progress(
                tracker=tracker,
                key=key,
                stage=stage,
                pct=pct,
                item_progress=item_progress,
            )

        return _report

    @staticmethod
    def _resolve_item_output_dir(
        *,
        key: str,
        src_path: Path,
        forced_stem: str | None,
        apply_all: tuple[str, str] | None,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> OutputDirectoryResolution:
        stem = sanitize_filename(forced_stem or src_path.stem)
        resolution = OutputResolver.resolve_directory(
            stem=stem,
            conflict_resolver=callbacks.conflict_resolver,
            apply_all=apply_all,
        )
        if resolution.skipped or resolution.output_dir is None:
            _LOG.debug(
                "Transcription output conflict resolved. session_id=%s source_key=%s action=skip",
                runtime.session_id,
                debug_source_key(key),
            )
            callbacks.item_status(key, "status.skipped")
            runtime.tracker.mark_done(key)
            return resolution

        _LOG.debug(
            "Transcription output directory resolved. session_id=%s source_key=%s out_dir=%s stem=%s",
            runtime.session_id,
            debug_source_key(key),
            Path(resolution.output_dir).name,
            resolution.stem,
        )
        callbacks.item_output_dir(key, str(resolution.output_dir))
        return resolution

    def _prepare_item_audio(
        self,
        *,
        key: str,
        src_path: Path,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> tuple[Path, float]:
        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="preprocess",
            pct=0,
        )
        preprocess_started = time.perf_counter()
        tmp_wav = workspace.ensure_tmp_wav(src_path, cancel_check=callbacks.cancel_check)
        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="preprocess",
            pct=100,
        )

        with wave.open(str(tmp_wav), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            dur_s = (float(frames) / float(rate)) if rate > 0 else 0.0

        runtime.tracker.set_weight(key, weight=float(max(15.0, min(3600.0, dur_s))))
        _LOG.debug(
            (
                "Transcription stage finished. session_id=%s source_key=%s stage=preprocess "
                "duration_ms=%s tmp_name=%s duration_s=%s"
            ),
            runtime.session_id,
            debug_source_key(key),
            int((time.perf_counter() - preprocess_started) * 1000.0),
            tmp_wav.name,
            round(dur_s, 2),
        )
        return tmp_wav, dur_s

    def _save_item_outputs(
        self,
        *,
        key: str,
        stem: str,
        out_dir: Path,
        merged_text: str,
        translated_text: str,
        translated_segments: list[dict[str, Any]] | None,
        segments: list[dict[str, Any]],
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> Path | None:
        callbacks.item_status(key, "status.saving")
        save_started = time.perf_counter()
        primary = self._write_outputs(
            key=key,
            stem=stem,
            out_dir=out_dir,
            merged_text=merged_text,
            translated_text=translated_text,
            translated_segments=translated_segments,
            segments=segments,
            output_mode_ids=runtime.options.output_mode_ids,
            transcript_ready=callbacks.transcript_ready,
            item_output_dir_cb=callbacks.item_output_dir,
            item_error_cb=callbacks.item_error,
            cancel_check=callbacks.cancel_check,
        )
        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="save",
            pct=100,
        )
        runtime.tracker.mark_done(key)
        _LOG.debug(
            (
                "Transcription stage finished. session_id=%s source_key=%s stage=save "
                "duration_ms=%s output_dir=%s primary_saved=%s"
            ),
            runtime.session_id,
            debug_source_key(key),
            int((time.perf_counter() - save_started) * 1000.0),
            out_dir.name,
            bool(primary is not None),
        )
        return primary

    def _count_session_sources(self, *, entries: list[SourceEntry]) -> tuple[int, int]:
        local_count = 0
        url_count = 0
        for entry in entries:
            key = self._entry_source_key(entry)
            if is_url_source(key):
                url_count += 1
            else:
                local_count += 1
        return local_count, url_count

    def _register_session_tracker(
        self,
        *,
        tracker: _ProgressTracker,
        entries: list[SourceEntry],
        want_translate: bool,
    ) -> None:
        for entry in entries:
            key = self._entry_source_key(entry)
            tracker.register(
                key,
                has_download=is_url_source(key),
                has_translate=want_translate,
                weight=self._estimate_item_weight(key),
            )

    def _materialize_work_items(
        self,
        *,
        entries: list[SourceEntry],
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> _MaterializeBatchResult:
        work: list[_MaterializedWorkItem] = []
        had_errors = False
        was_cancelled = False

        for entry in entries:
            request = self._build_entry_request(entry)
            if callbacks.cancel_check():
                was_cancelled = True
                break
            try:
                work.append(
                    self._materialize_entry(
                        request=request,
                        runtime=runtime,
                        callbacks=callbacks,
                    )
                )
            except OperationCancelled:
                was_cancelled = True
                break
            except Exception as ex:
                had_errors = True
                self._emit_materialize_error(
                    request=request,
                    runtime=runtime,
                    callbacks=callbacks,
                    error=ex,
                )

        return _MaterializeBatchResult(work=work, had_errors=had_errors, was_cancelled=was_cancelled)

    @staticmethod
    def _build_session_callbacks(
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
    ) -> _SessionCallbacks:
        return _SessionCallbacks(
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

    def _log_session_plan(self, *, runtime: _SessionRuntime, entries: list[SourceEntry]) -> None:
        local_count, url_count = self._count_session_sources(entries=entries)
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

    def _finish_session_if_no_work(
        self,
        *,
        runtime: _SessionRuntime,
        materialized: _MaterializeBatchResult,
    ) -> SessionResult | None:
        if not materialized.was_cancelled and materialized.work:
            return None
        _LOG.debug(
            "Transcription session ended early. session_id=%s cancelled=%s work_items=%s errors=%s",
            runtime.session_id,
            bool(materialized.was_cancelled),
            len(materialized.work),
            bool(materialized.had_errors),
        )
        return self._finish_session(
            runtime=runtime,
            processed_any=False,
            had_errors=bool(materialized.had_errors),
            was_cancelled=bool(materialized.was_cancelled),
        )

    def run_session(
        self,
        *,
        pipe: Any,
        entries: list[SourceEntry],
        session_request: TranscriptionSessionRequest,
        progress: ProgressFn,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        transcript_ready: TranscriptReadyFn,
        item_error: ItemErrorFn,
        item_output_dir: ItemOutputDirFn,
        conflict_resolver: ConflictResolverFn,
        access_intervention_resolver: AccessInterventionResolverFn | None,
        cancel_check: CancelCheckFn,
    ) -> SessionResult:
        entries = list(entries or [])
        callbacks = self._build_session_callbacks(
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
        runtime = self._prepare_session_runtime(
            entries=entries,
            session_request=session_request,
            progress=progress,
        )

        self._log_session_plan(runtime=runtime, entries=entries)
        materialized = self._materialize_work_items(
            entries=entries,
            runtime=runtime,
            callbacks=callbacks,
        )
        processed_any = False
        had_errors = bool(materialized.had_errors)
        was_cancelled = bool(materialized.was_cancelled)

        early_finish = self._finish_session_if_no_work(runtime=runtime, materialized=materialized)
        if early_finish is not None:
            return early_finish

        processed_any, had_errors, was_cancelled = self._process_materialized_work_items(
            pipe=pipe,
            work_items=materialized.work,
            runtime=runtime,
            callbacks=callbacks,
            processed_any=processed_any,
            had_errors=had_errors,
            was_cancelled=was_cancelled,
        )
        return self._finish_session(
            runtime=runtime,
            processed_any=processed_any,
            had_errors=had_errors,
            was_cancelled=was_cancelled,
        )

    def _process_materialized_work_items(
        self,
        *,
        pipe: Any,
        work_items: list[_MaterializedWorkItem],
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
        processed_any: bool,
        had_errors: bool,
        was_cancelled: bool,
    ) -> tuple[bool, bool, bool]:
        apply_all: tuple[str, str] | None = None
        for work_item in work_items:
            item_result = self._process_materialized_work_item(
                pipe=pipe,
                work_item=work_item,
                apply_all=apply_all,
                runtime=runtime,
                callbacks=callbacks,
            )
            processed_any = bool(processed_any or item_result.processed_any)
            had_errors = bool(had_errors or item_result.had_errors)
            apply_all = item_result.apply_all
            if item_result.was_cancelled:
                was_cancelled = True
                break
        return processed_any, had_errors, was_cancelled

    def _process_materialized_work_item(
        self,
        *,
        pipe: Any,
        work_item: _MaterializedWorkItem,
        apply_all: tuple[str, str] | None,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> _ItemProcessResult:
        key = work_item.source_key
        src_path = work_item.source_path
        forced_stem = work_item.forced_stem

        if callbacks.cancel_check():
            return _ItemProcessResult(False, False, True, apply_all)

        resolution = self._resolve_item_output_dir(
            key=key,
            src_path=src_path,
            forced_stem=forced_stem,
            apply_all=apply_all,
            runtime=runtime,
            callbacks=callbacks,
        )
        if resolution.skipped or resolution.output_dir is None:
            return _ItemProcessResult(False, False, False, apply_all)

        out_dir = resolution.output_dir
        stem = resolution.stem
        apply_all = resolution.apply_all

        tmp_wav: Path | None = None
        try:
            callbacks.item_status(key, "status.processing")
            tmp_wav, _ = self._prepare_item_audio(
                key=key,
                src_path=src_path,
                runtime=runtime,
                callbacks=callbacks,
            )

            callbacks.item_status(key, "status.transcribing")
            transcribe_started = time.perf_counter()
            merged_text, segments, detected_lang = self._transcribe_wav(
                pipe=pipe,
                wav_path=tmp_wav,
                key=key,
                chunk_len_s=runtime.options.chunk_len_s,
                stride_len_s=runtime.options.stride_len_s,
                want_timestamps=runtime.options.want_timestamps,
                ignore_warning=runtime.options.ignore_warning,
                tracker=runtime.tracker,
                item_progress=callbacks.item_progress,
                cancel_check=callbacks.cancel_check,
                require_language=runtime.options.want_translate,
                source_language=runtime.options.default_lang,
                runtime_profile=runtime.options.runtime_profile,
            )
            _LOG.debug(
                (
                    "Transcription stage finished. session_id=%s source_key=%s stage=transcribe "
                    "duration_ms=%s text_chars=%s segments=%s detected_lang=%s"
                ),
                runtime.session_id,
                debug_source_key(key),
                int((time.perf_counter() - transcribe_started) * 1000.0),
                len(merged_text),
                len(segments),
                detected_lang or "",
            )

            translated_text, translated_segments, translate_had_errors = self._translate_item_if_needed(
                key=key,
                merged_text=merged_text,
                segments=segments,
                detected_lang=detected_lang,
                runtime=runtime,
                callbacks=callbacks,
            )

            primary = self._save_item_outputs(
                key=key,
                stem=stem,
                out_dir=out_dir,
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
                runtime=runtime,
                callbacks=callbacks,
            )
            if primary is not None:
                callbacks.item_status(key, "status.done")
                return _ItemProcessResult(True, bool(translate_had_errors), False, apply_all)

            callbacks.item_status(key, "status.error")
            return _ItemProcessResult(False, True, False, apply_all)
        except OperationCancelled:
            return _ItemProcessResult(False, False, True, apply_all)
        except Exception as ex:
            self._emit_item_error(callbacks=callbacks, key=key, error=ex)
            callbacks.item_status(key, "status.error")
            return _ItemProcessResult(False, True, False, apply_all)
        finally:
            self._cleanup_tmp_wav(tmp_wav=tmp_wav, src_path=src_path)

    def _translate_item_if_needed(
        self,
        *,
        key: str,
        merged_text: str,
        segments: list[dict[str, Any]],
        detected_lang: str,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> tuple[str, list[dict[str, Any]] | None, bool]:
        translated_text = ""
        translated_segments: list[dict[str, Any]] | None = None
        had_errors = False

        if not runtime.options.want_translate:
            self._report_item_stage_progress(
                tracker=runtime.tracker,
                key=key,
                stage="translate",
                pct=100,
                item_progress=callbacks.item_progress,
            )
            return translated_text, translated_segments, had_errors

        callbacks.item_status(key, "status.translating")
        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="translate",
            pct=0,
            item_progress=callbacks.item_progress,
        )
        translate_started = time.perf_counter()
        src_lang = pick_source_language(default_lang=runtime.options.default_lang, detected_lang=detected_lang)
        _LOG.debug(
            (
                "Translation source language resolved. session_id=%s source_key=%s default_lang=%s "
                "detected_lang=%s resolved=%s"
            ),
            runtime.session_id,
            debug_source_key(key),
            runtime.options.default_lang or "",
            detected_lang or "",
            src_lang or "",
        )

        if not src_lang:
            had_errors = True
            callbacks.item_error(key, "error.translation.missing_source_language", {})
        else:
            has_segment_translation = bool(runtime.options.want_timestamps and segments)
            text_share = 70 if has_segment_translation else 100
            segments_share = max(0, 100 - text_share)

            def _translate_stage_progress_for_text(pct: int) -> None:
                mapped = int(round((max(0, min(100, int(pct))) / 100.0) * float(text_share)))
                self._report_item_stage_progress(
                    tracker=runtime.tracker,
                    key=key,
                    stage="translate",
                    pct=mapped,
                    item_progress=callbacks.item_progress,
                )

            def _translate_stage_progress_for_segments(pct: int) -> None:
                mapped = text_share + int(round((max(0, min(100, int(pct))) / 100.0) * float(segments_share)))
                self._report_item_stage_progress(
                    tracker=runtime.tracker,
                    key=key,
                    stage="translate",
                    pct=min(100, mapped),
                    item_progress=callbacks.item_progress,
                )

            try:
                translated_text = self._translator.translate(
                    merged_text,
                    src_lang=src_lang,
                    tgt_lang=runtime.options.tgt_lang,
                    log=None,
                    progress_cb=_translate_stage_progress_for_text,
                    cancel_check=callbacks.cancel_check,
                )
            except AppError as ex:
                had_errors = True
                callbacks.item_error(
                    key,
                    str(ex.key or "error.generic"),
                    dict(ex.params or {}),
                )
                translated_text = ""
            else:
                if has_segment_translation:
                    try:
                        translated_segments = self._translate_segments(
                            segments=segments,
                            src_lang=src_lang,
                            tgt_lang=runtime.options.tgt_lang,
                            cancel_check=callbacks.cancel_check,
                            progress_cb=_translate_stage_progress_for_segments,
                        )
                    except AppError as ex:
                        had_errors = True
                        callbacks.item_error(
                            key,
                            str(ex.key or "error.generic"),
                            dict(ex.params or {}),
                        )
                        translated_text = ""
                        translated_segments = None

        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="translate",
            pct=100,
            item_progress=callbacks.item_progress,
        )
        _LOG.debug(
            (
                "Transcription stage finished. session_id=%s source_key=%s stage=translate "
                "duration_ms=%s text_chars=%s segments=%s"
            ),
            runtime.session_id,
            debug_source_key(key),
            int((time.perf_counter() - translate_started) * 1000.0),
            len(translated_text),
            len(translated_segments or []),
        )
        return translated_text, translated_segments, had_errors

    @staticmethod
    def _cleanup_tmp_wav(*, tmp_wav: Path | None, src_path: Path) -> None:
        try:
            if tmp_wav is not None and tmp_wav != src_path and tmp_wav.suffix.lower() == ".wav":
                tmp_wav.unlink(missing_ok=True)
        except OSError as ex:
            _LOG.debug("Temp WAV cleanup skipped. path=%s detail=%s", tmp_wav, ex)

    @staticmethod
    def _cleanup_downloaded_sources(*, downloaded_to_delete: set[Path]) -> None:
        for path in downloaded_to_delete:
            try:
                path.unlink(missing_ok=True)
            except OSError as ex:
                _LOG.debug("Downloaded source cleanup skipped. path=%s detail=%s", path, ex)

            parent = path.parent
            try:
                tmp_root = AppConfig.PATHS.DOWNLOADS_TMP_DIR.resolve()
                if parent != tmp_root and tmp_root in parent.resolve().parents:
                    shutil.rmtree(parent, ignore_errors=True)
            except OSError as ex:
                _LOG.debug("Downloaded source directory cleanup skipped. path=%s detail=%s", parent, ex)

    @staticmethod
    def _call_download_service_with_intervention(
        *,
        source_key: str,
        source_url: str,
        operation_name: str,
        callbacks: _SessionCallbacks,
        browser_cookies_mode_override: str | None,
        cookie_file_override: str | None,
        browser_policy_override: str | None,
        access_mode_override: str | None,
        operation: Callable[[str | None, str | None, str | None, str | None], _DownloadResult],
    ) -> tuple[_DownloadResult, str | None, str | None, str | None, str | None]:
        resolver = callbacks.access_intervention_resolver
        while True:
            try:
                result = operation(
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                )
                return (
                    result,
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                )
            except SourceAccessInterventionRequired as ex:
                if resolver is None:
                    raise
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = resolver(
                    source_key,
                    ex.request,
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                )
            except DownloadError as ex:
                if resolver is None:
                    raise
                intervention = DownloadService.intervention_request_from_error(
                    ex,
                    url=source_url,
                    operation=operation_name,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                if intervention is None:
                    raise
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = resolver(
                    source_key,
                    intervention.request,
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                )

    def _materialize_entry(
        self,
        *,
        request: _EntryRequest,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> _MaterializedWorkItem:
        if request.is_url:
            return self._materialize_url(
                request=request,
                runtime=runtime,
                callbacks=callbacks,
            )
        return materialize_local_entry(
            request,
            missing_error_factory=lambda path: self._transcription_error(
                "error.input.file_not_found",
                path=str(path),
            ),
        )

    def _materialize_url(
        self,
        *,
        request: _EntryRequest,
        runtime: _SessionRuntime,
        callbacks: _SessionCallbacks,
    ) -> _MaterializedWorkItem:
        old_key = str(request.source_key)
        safe_url = sanitize_url_for_log(request.source_key)
        callbacks.item_status(old_key, "status.processing")
        download_started = time.perf_counter()
        browser_cookies_mode_override: str | None = None
        cookie_file_override: str | None = None
        browser_policy_override: str | None = None
        access_mode_override: str | None = None
        (
            meta,
            browser_cookies_mode_override,
            cookie_file_override,
            browser_policy_override,
            access_mode_override,
        ) = self._call_download_service_with_intervention(
            source_key=old_key,
            source_url=request.source_key,
            operation_name=DownloadPolicy.DOWNLOAD_OPERATION_PROBE,
            callbacks=callbacks,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            operation=lambda mode_override, file_override, browser_override, access_override: self._download.probe(
                request.source_key,
                browser_cookies_mode_override=mode_override,
                cookie_file_override=file_override,
                browser_policy_override=browser_override,
                access_mode_override=access_override,
                interactive=True,
            ),
        )
        dur = meta.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            runtime.tracker.set_weight(old_key, weight=max(15.0, min(3600.0, float(dur))))

        title = sanitize_filename(str(meta.get("title") or "").strip())
        stem = request.forced_stem or title or DownloadPolicy.DOWNLOAD_DEFAULT_STEM
        kind = runtime.options.url_download_kind
        ext = runtime.options.url_download_ext
        keep = runtime.options.url_keep_download
        quality = runtime.options.url_download_quality

        _LOG.debug(
            (
                "Transcription URL materialization started. source_key=%s kind=%s ext=%s "
                "keep_download=%s audio_track_id=%s"
            ),
            safe_url,
            kind,
            ext,
            bool(keep),
            request.audio_track_id or "",
        )

        on_dl = self._build_stage_progress_callback(
            tracker=runtime.tracker,
            key=old_key,
            stage="download",
            item_progress=callbacks.item_progress,
        )

        callbacks.item_status(old_key, "status.downloading")
        out_dir = workspace.downloads_dir() if keep else workspace.url_tmp_dir()
        (
            dst,
            browser_cookies_mode_override,
            cookie_file_override,
            browser_policy_override,
            access_mode_override,
        ) = self._call_download_service_with_intervention(
            source_key=old_key,
            source_url=request.source_key,
            operation_name=DownloadPolicy.DOWNLOAD_OPERATION_DOWNLOAD,
            callbacks=callbacks,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            operation=lambda mode_override, file_override, browser_override, access_override: self._download.download(
                url=request.source_key,
                kind=kind,
                quality=quality,
                ext=ext,
                out_dir=out_dir,
                progress_cb=on_dl,
                audio_track_id=request.audio_track_id,
                file_stem=stem,
                cancel_check=callbacks.cancel_check,
                purpose=DownloadPolicy.DOWNLOAD_PURPOSE_TRANSCRIPTION,
                keep_output=keep,
                meta=meta,
                browser_cookies_mode_override=mode_override,
                cookie_file_override=file_override,
                browser_policy_override=browser_override,
                access_mode_override=access_override,
            ),
        )
        if not dst:
            raise AppError(key="error.down.download_failed", params={"detail": "download returned no file path"})
        if not keep:
            runtime.downloaded_to_delete.add(dst)

        new_key = str(dst)
        callbacks.item_path_update(old_key, new_key)
        runtime.tracker.rename_key(old_key, new_key)
        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=new_key,
            stage="download",
            pct=100,
            item_progress=callbacks.item_progress,
        )
        _LOG.debug(
            "Transcription URL materialization finished. source_key=%s new_key=%s duration_ms=%s",
            safe_url,
            debug_source_key(new_key),
            int((time.perf_counter() - download_started) * 1000.0),
        )
        return _MaterializedWorkItem(
            source_key=new_key,
            source_path=dst,
            forced_stem=stem,
        )

    def _transcribe_wav(
        self,
        *,
        pipe: Any,
        wav_path: Path,
        key: str,
        chunk_len_s: int,
        stride_len_s: int,
        want_timestamps: bool,
        ignore_warning: bool,
        tracker: _ProgressTracker,
        item_progress: ItemProgressFn,
        cancel_check: CancelCheckFn,
        require_language: bool,
        source_language: str = "",
        runtime_profile: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]], str]:
        return transcribe_wav(
            pipe=pipe,
            wav_path=wav_path,
            key=key,
            chunk_len_s=chunk_len_s,
            stride_len_s=stride_len_s,
            want_timestamps=want_timestamps,
            ignore_warning=ignore_warning,
            progress_cb=lambda pct: self._report_item_stage_progress(
                tracker=tracker,
                key=key,
                stage="transcribe",
                pct=pct,
                item_progress=item_progress,
            ),
            cancel_check=cancel_check,
            require_language=require_language,
            source_language=source_language,
            runtime_profile=runtime_profile,
            postprocessor=self._post,
            error_factory=self._transcription_error,
        )

    def _translate_segments(
        self,
        *,
        segments: list[dict[str, Any]],
        src_lang: str,
        tgt_lang: str,
        cancel_check: CancelCheckFn,
        progress_cb: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        total = max(1, len(segments))
        out: list[dict[str, Any]] = []
        for i, seg in enumerate(segments, start=1):
            if cancel_check():
                raise OperationCancelled()
            text = str(seg.get("text") or "")
            translated = self._translator.translate(
                text,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                log=None,
                cancel_check=cancel_check,
            )
            if not translated:
                raise TranslationError("error.translation.empty_result")
            out.append({"start": seg["start"], "end": seg["end"], "text": translated})
            if progress_cb is not None:
                progress_cb(int(round((i / float(total)) * 100)))
        return out

    @staticmethod
    def _write_outputs(
        *,
        key: str,
        stem: str,
        out_dir: Path,
        merged_text: str,
        translated_text: str,
        translated_segments: list[dict[str, Any]] | None,
        segments: list[dict[str, Any]],
        output_mode_ids: list[str],
        transcript_ready: TranscriptReadyFn,
        item_output_dir_cb: ItemOutputDirFn | None,
        item_error_cb: ItemErrorFn | None,
        cancel_check: CancelCheckFn,
    ) -> Path | None:
        if cancel_check():
            raise OperationCancelled()

        try:
            written_paths = TranscriptWriter.write_mode_outputs(
                out_dir=out_dir,
                output_mode_ids=output_mode_ids,
                mode_resolver=TranscriptionOutputPolicy.get_transcription_output_mode,
                filename_resolver=TranscriptionOutputPolicy.transcript_filename,
                unique_path_resolver=ensure_unique_path,
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
            )
        except Exception as ex:
            _LOG.error("Transcript save failed. name=%s detail=%s", stem, str(ex))
            if item_error_cb is not None:
                item_error_cb(key, "error.transcription.save_failed", {"name": stem, "detail": str(ex)})
            return None

        primary_path: Path | None = written_paths[0] if written_paths else None
        if primary_path is not None:
            transcript_ready(key, str(primary_path))
            if item_output_dir_cb is not None:
                item_output_dir_cb(key, str(out_dir))

        for mode_id, out_path in zip(output_mode_ids, written_paths):
            _LOG.debug(
                "Transcript output saved. source_key=%s mode=%s file_name=%s",
                debug_source_key(key),
                mode_id,
                out_path.name,
            )
        return primary_path


__all__ = [
    "TranscriptionError",
    "TranscriptionService",
    "_ItemProcessResult",
    "_ProgressTracker",
    "_SessionOptions",
    "_SessionRuntime",
]
