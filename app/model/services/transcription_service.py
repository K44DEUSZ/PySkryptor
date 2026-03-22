# app/model/services/transcription_service.py
from __future__ import annotations

import logging
import shutil
import time
import wave

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.model.config.app_config import AppConfig as Config
from app.model.domain.errors import AppError, OperationCancelled
from app.model.domain.entities import TranscriptionSessionRequest
from app.model.domain.results import SessionResult
from app.model.helpers.chunking import (
    estimate_chunks,
    iter_wav_mono_chunks,
    normalize_chunk_params,
)
from app.model.helpers.output_resolver import OutputDirectoryResolution, OutputResolver
from app.model.helpers.string_utils import sanitize_filename, sanitize_url_for_log
from app.model.helpers.transcription_runtime import (
    audio_has_meaningful_signal,
    detect_language_from_pipe_runtime,
    debug_source_key,
    extract_detected_language_from_result,
    normalize_detected_language,
    whisper_prompt_ids_from_text,
)
from app.model.io.audio_extractor import AudioExtractor
from app.model.io.file_manager import FileManager
from app.model.io.media_probe import is_url_source
from app.model.io.transcript_writer import TextPostprocessor, TranscriptWriter
from app.model.services.download_service import DownloadService
from app.model.services.translation_service import TranslationService

_LOG = logging.getLogger(__name__)

class TranscriptionError(AppError):
    """Key-based error used for i18n-friendly transcription failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))

SourceEntry = str | dict[str, Any]

ProgressFn = Callable[[int], None]
CancelCheckFn = Callable[[], bool]
ItemStatusFn = Callable[[str, str], None]
ItemProgressFn = Callable[[str, int], None]
ItemPathUpdateFn = Callable[[str, str], None]
TranscriptReadyFn = Callable[[str, str], None]
ItemErrorFn = Callable[[str, str, dict[str, Any]], None]
ItemOutputDirFn = Callable[[str, str], None]
ConflictResolverFn = Callable[[str, str], tuple[str, str, bool]]

@dataclass(frozen=True)
class _SessionOptions:
    """Resolved session options reused across all work items."""
    output_mode_ids: list[str]
    translate_requested: bool
    want_translate: bool
    want_timestamps: bool
    tgt_lang: str
    default_lang: str
    chunk_len_s: int
    stride_len_s: int
    text_consistency: bool
    ignore_warning: bool
    url_download_kind: str
    url_download_ext: str
    url_keep_download: bool
    url_download_quality: str

@dataclass(frozen=True)
class _EntryRequest:
    """Normalized source entry used before materialization."""
    source_key: str
    forced_stem: str | None
    audio_lang: str | None
    is_url: bool

@dataclass(frozen=True)
class _MaterializedWorkItem:
    """Single source resolved to a concrete local path."""
    source_key: str
    source_path: Path
    forced_stem: str | None

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
    cancel_check: CancelCheckFn

@dataclass(frozen=True)
class _MaterializeBatchResult:
    """Materialized work items and control flags for the current session."""
    work: list[_MaterializedWorkItem]
    had_errors: bool
    was_cancelled: bool

@dataclass(frozen=True)
class _ItemProcessResult:
    """Outcome of processing a single materialized transcription item."""
    processed_any: bool
    had_errors: bool
    was_cancelled: bool
    apply_all: tuple[str, str] | None

@dataclass
class _ItemPlan:
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
            stage_pct={s: 0 for s in self._STAGES},
        )

    def set_weight(self, key: str, *, weight: float) -> None:
        k = str(key)
        if k in self._plans:
            self._plans[k].weight = float(max(0.0001, weight))

    def rename_key(self, old_key: str, new_key: str) -> None:
        """Moves an existing item plan to a new key without changing its progress."""
        old_k = str(old_key)
        new_k = str(new_key)
        if old_k == new_k:
            return
        plan = self._plans.pop(old_k, None)
        if plan is None:
            return
        self._plans[new_k] = plan

    def update(self, key: str, stage: str, pct: int) -> None:
        k = str(key)
        if k not in self._plans:
            return
        stage = str(stage)
        if stage not in self._STAGES:
            return
        self._plans[k].stage_pct[stage] = int(max(0, min(100, pct)))
        self._emit()

    def mark_done(self, key: str) -> None:
        k = str(key)
        if k in self._plans:
            for s in self._STAGES:
                self._plans[k].stage_pct[s] = 100
        self._emit()

    def _emit(self) -> None:
        if not self._plans:
            self._cb(0)
            return

        total_w = 0.0
        total_p = 0.0

        for p in self._plans.values():
            w = dict(self._BASE_WEIGHTS)
            if not p.has_download:
                w["download"] = 0.0
            if not p.has_translate:
                w["translate"] = 0.0

            norm = sum(w.values()) or 1.0
            for k in w:
                w[k] = w[k] / norm

            item_p = 0.0
            for s, ww in w.items():
                item_p += (p.stage_pct.get(s, 0) / 100.0) * ww

            total_w += p.weight
            total_p += item_p * p.weight

        pct = int(round((total_p / max(0.0001, total_w)) * 100))
        pct = max(0, min(100, pct))
        if pct < self._last_pct:
            pct = self._last_pct
        self._last_pct = pct
        self._cb(pct)

class TranscriptionService:
    """Runs transcription sessions using an already-built ASR pipeline."""

    def __init__(self) -> None:
        self._download = DownloadService()
        self._translator = TranslationService()
        self._post = TextPostprocessor()

    @staticmethod
    def _estimate_item_weight(key: str) -> float:
        p = str(key or "")
        if not p or is_url_source(p):
            return 15.0
        try:
            path = Path(p)
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
        model_cfg = Config.transcription_model_raw_cfg_dict()

        output_mode_ids = [str(mode_id or "").strip().lower() for mode_id in session_request.output_formats if str(mode_id or "").strip()]
        if not output_mode_ids:
            output_mode_ids = list(Config.transcription_output_mode_ids())

        output_modes = [Config.get_transcription_output_mode(str(mode_id)) for mode_id in output_mode_ids]
        want_timestamps = any(
            bool(mode.get("timestamps", False)) or str(mode.get("ext", "")).strip().lower() == "srt"
            for mode in output_modes
        )

        translate_requested = bool(session_request.translate_after_transcription)
        tgt_lang = Config.normalize_policy_value(session_request.target_language or "")
        want_translate = bool(translate_requested and tgt_lang and not Config.is_auto_language_value(tgt_lang))

        default_lang = Config.normalize_policy_value(
            session_request.source_language
            or str(model_cfg.get("default_language") or "")
            or Config.LANGUAGE_AUTO_VALUE
        )

        audio_ext = str(session_request.url_audio_ext or Config.transcription_url_audio_ext()).strip().lower()
        video_ext = str(session_request.url_video_ext or Config.transcription_url_video_ext()).strip().lower()
        download_audio_only = bool(session_request.download_audio_only)
        url_download_kind = "audio" if download_audio_only else "video"
        url_download_ext = audio_ext if download_audio_only else video_ext
        url_keep_download = bool(session_request.url_keep_audio if download_audio_only else session_request.url_keep_video)

        preset_id = Config.normalize_transcription_quality_preset(model_cfg.get("quality_preset", "balanced"))
        preset_profile = Config.transcription_quality_profile(preset_id)
        chunk_len_s = int(preset_profile.get("chunk_length_s", 45))
        stride_len_s = int(preset_profile.get("stride_length_s", 5))
        text_consistency = bool(model_cfg.get("text_consistency", True))

        return _SessionOptions(
            output_mode_ids=output_mode_ids,
            translate_requested=translate_requested,
            want_translate=want_translate,
            want_timestamps=want_timestamps,
            tgt_lang=tgt_lang,
            default_lang=default_lang,
            chunk_len_s=chunk_len_s,
            stride_len_s=stride_len_s,
            text_consistency=text_consistency,
            ignore_warning=bool(model_cfg.get("ignore_warning", False)),
            url_download_kind=url_download_kind,
            url_download_ext=url_download_ext,
            url_keep_download=url_keep_download,
            url_download_quality=Config.URL_DOWNLOAD_DEFAULT_QUALITY,
        )

    @staticmethod
    def _build_entry_request(entry: SourceEntry) -> _EntryRequest:
        if isinstance(entry, dict):
            source_key = str(entry.get("src") or "")
            forced_stem = str(entry.get("stem") or "").strip() or None
            audio_lang = str(entry.get("audio_lang") or "").strip() or None
            if (audio_lang or "").lower() in set(Config.DOWNLOAD_AUDIO_LANG_AUTO_VALUES):
                audio_lang = None
        else:
            source_key = str(entry or "")
            forced_stem = None
            audio_lang = None

        return _EntryRequest(
            source_key=source_key,
            forced_stem=forced_stem,
            audio_lang=audio_lang,
            is_url=is_url_source(source_key),
        )

    def _prepare_session_runtime(
        self,
        *,
        entries: list[SourceEntry],
        session_request: TranscriptionSessionRequest,
        progress: ProgressFn,
    ) -> _SessionRuntime:
        session_dir = FileManager.plan_session()
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
            FileManager.rollback_session_if_empty()
        FileManager.end_session()
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
        err_key = getattr(error, "key", None)
        err_params = getattr(error, "params", None)
        if err_key:
            callbacks.item_error(key, str(err_key), dict(err_params or {}))
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
        tmp_wav = FileManager.ensure_tmp_wav(src_path, cancel_check=callbacks.cancel_check)
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
            "Transcription stage finished. session_id=%s source_key=%s stage=preprocess duration_ms=%s tmp_name=%s duration_s=%s",
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
            "Transcription stage finished. session_id=%s source_key=%s stage=save duration_ms=%s output_dir=%s primary_saved=%s",
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

    def _register_session_tracker(self, *, tracker: _ProgressTracker, entries: list[SourceEntry], want_translate: bool) -> None:
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

    def _build_session_callbacks(
        self,
        *,
        item_status: ItemStatusFn,
        item_progress: ItemProgressFn,
        item_path_update: ItemPathUpdateFn,
        transcript_ready: TranscriptReadyFn,
        item_error: ItemErrorFn,
        item_output_dir: ItemOutputDirFn,
        conflict_resolver: ConflictResolverFn,
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
            cancel_check=cancel_check,
        )

    def _log_session_plan(self, *, runtime: _SessionRuntime, entries: list[SourceEntry]) -> None:
        local_count, url_count = self._count_session_sources(entries=entries)
        _LOG.info("Transcription session started. items=%d", len(entries))
        _LOG.debug(
            "Transcription session planned. session_id=%s items=%s local_count=%s url_count=%s translate_requested=%s translate_effective=%s source_language=%s target_language=%s output_modes=%s",
            runtime.session_id,
            len(entries),
            local_count,
            url_count,
            bool(runtime.options.translate_requested),
            bool(runtime.options.want_translate),
            runtime.options.default_lang or Config.LANGUAGE_AUTO_VALUE,
            runtime.options.tgt_lang or Config.LANGUAGE_AUTO_VALUE,
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
            )
            _LOG.debug(
                "Transcription stage finished. session_id=%s source_key=%s stage=transcribe duration_ms=%s text_chars=%s segments=%s detected_lang=%s",
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
        translate_started = time.perf_counter()
        src_lang = self._pick_source_language(default_lang=runtime.options.default_lang, detected_lang=detected_lang)
        _LOG.debug(
            "Translation source language resolved. session_id=%s source_key=%s default_lang=%s detected_lang=%s resolved=%s",
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
            try:
                translated_text = self._translator.translate(
                    merged_text,
                    src_lang=src_lang,
                    tgt_lang=runtime.options.tgt_lang,
                    log=None,
                )
            except AppError as ex:
                had_errors = True
                callbacks.item_error(key, str(getattr(ex, "key", "error.generic")), dict(getattr(ex, "params", {}) or {}))
                translated_text = ""
            else:
                if runtime.options.want_timestamps and segments:
                    translated_segments = self._translate_segments(
                        segments=segments,
                        src_lang=src_lang,
                        tgt_lang=runtime.options.tgt_lang,
                        cancel_check=callbacks.cancel_check,
                        progress_cb=self._build_stage_progress_callback(
                            tracker=runtime.tracker,
                            key=key,
                            stage="translate",
                            item_progress=callbacks.item_progress,
                        ),
                    )

        self._report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="translate",
            pct=100,
            item_progress=callbacks.item_progress,
        )
        _LOG.debug(
            "Transcription stage finished. session_id=%s source_key=%s stage=translate duration_ms=%s text_chars=%s segments=%s",
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

            try:
                parent = path.parent
                tmp_root = Config.DOWNLOADS_TMP_DIR.resolve()
                if parent != tmp_root and tmp_root in parent.resolve().parents:
                    shutil.rmtree(parent, ignore_errors=True)
            except OSError as ex:
                _LOG.debug("Downloaded source directory cleanup skipped. path=%s detail=%s", parent, ex)

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

        p = Path(request.source_key).expanduser()
        if not p.exists():
            raise TranscriptionError("error.input.file_not_found", path=str(p))
        return _MaterializedWorkItem(
            source_key=request.source_key,
            source_path=p,
            forced_stem=request.forced_stem,
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
        meta = self._download.probe(request.source_key)
        dur = meta.get("duration")
        if isinstance(dur, (int, float)) and dur > 0:
            runtime.tracker.set_weight(old_key, weight=max(15.0, min(3600.0, float(dur))))

        title = sanitize_filename(str(meta.get("title") or "").strip())
        stem = request.forced_stem or title or Config.DOWNLOAD_DEFAULT_STEM

        kind = runtime.options.url_download_kind
        ext = runtime.options.url_download_ext
        keep = runtime.options.url_keep_download
        quality = runtime.options.url_download_quality

        _LOG.debug("Transcription URL materialization started. source_key=%s kind=%s ext=%s keep_download=%s audio_lang=%s", safe_url, kind, ext, bool(keep), request.audio_lang or "")

        on_dl = self._build_stage_progress_callback(
            tracker=runtime.tracker,
            key=old_key,
            stage="download",
            item_progress=callbacks.item_progress,
        )

        callbacks.item_status(old_key, "status.downloading")
        out_dir = FileManager.downloads_dir() if keep else FileManager.url_tmp_dir()
        dst = self._download.download(
            url=request.source_key,
            kind=kind,
            quality=quality,
            ext=ext,
            out_dir=out_dir,
            progress_cb=on_dl,
            audio_lang=request.audio_lang,
            file_stem=stem,
            cancel_check=callbacks.cancel_check,
            purpose=Config.DOWNLOAD_PURPOSE_TRANSCRIPTION,
            keep_output=keep,
            meta=meta,
        )
        if not dst:
            raise AppError("error.down.download_failed", {"detail": "download returned no file path"})
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
        _LOG.debug("Transcription URL materialization finished. source_key=%s new_key=%s duration_ms=%s", safe_url, debug_source_key(new_key), int((time.perf_counter() - download_started) * 1000.0))
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
        text_consistency: bool = True,
    ) -> tuple[str, list[dict[str, Any]], str]:
        with wave.open(str(wav_path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            dur_s = 0.0 if rate <= 0 else float(frames) / float(rate)
        chunk_len_s, stride_len_s, step_s = normalize_chunk_params(chunk_len_s, stride_len_s)
        n_chunks = estimate_chunks(dur_s, chunk_len_s, stride_len_s)
        _LOG.debug("Transcription wav started. source_key=%s duration_s=%s chunks=%s require_language=%s timestamps=%s", debug_source_key(key), round(dur_s, 2), n_chunks, bool(require_language), bool(want_timestamps))

        merged_parts: list[str] = []
        segments: list[dict[str, Any]] = []
        detected_lang = ""
        previous_prompt_text = ""

        for i, ch in enumerate(iter_wav_mono_chunks(wav_path, chunk_len_s=chunk_len_s, stride_len_s=stride_len_s), start=1):
            if cancel_check():
                raise OperationCancelled()

            if n_chunks <= 1 and i == 1:
                self._report_item_stage_progress(
                    tracker=tracker,
                    key=key,
                    stage="transcribe",
                    pct=5,
                    item_progress=item_progress,
                )

            if not audio_has_meaningful_signal(
                ch.audio,
                sr=ch.sr,
                rms_min=0.014,
                activity_floor=0.006,
                active_ratio_min=0.02,
                active_ms_min=60.0,
            ):
                pct = int(round((i / float(n_chunks)) * 100))
                if n_chunks <= 1:
                    pct = min(95, max(0, pct))
                self._report_item_stage_progress(
                    tracker=tracker,
                    key=key,
                    stage="transcribe",
                    pct=pct,
                    item_progress=item_progress,
                )
                continue

            out = self._pipe_call(
                pipe=pipe,
                audio=ch.audio,
                sr=ch.sr,
                ignore_warning=ignore_warning,
                require_language=require_language,
                source_language=source_language,
                text_consistency=text_consistency,
                previous_text=previous_prompt_text,
            )

            if not detected_lang:
                detected_lang = extract_detected_language_from_result(out)

            text = self._post.plain_from_result(out)
            if text:
                merged_parts.append(text)
                if text_consistency:
                    previous_prompt_text = "\n".join([p for p in merged_parts[-3:] if p]).strip()

            if want_timestamps:
                segments.extend(self._extract_segments(out, offset_s=ch.offset_s))

            pct = int(round((i / float(n_chunks)) * 100))
            if n_chunks <= 1:
                pct = min(95, max(0, pct))
            self._report_item_stage_progress(
                tracker=tracker,
                key=key,
                stage="transcribe",
                pct=pct,
                item_progress=item_progress,
            )

        merged_text = "\n".join([p for p in merged_parts if p]).strip()
        if not merged_text and not bool(ignore_warning):
            raise TranscriptionError("error.transcription.empty_result")

        self._report_item_stage_progress(
            tracker=tracker,
            key=key,
            stage="transcribe",
            pct=100,
        )
        _LOG.debug("Transcription wav finished. source_key=%s text_chars=%s segments=%s detected_lang=%s", debug_source_key(key), len(merged_text), len(segments), detected_lang or "")
        return merged_text, segments, detected_lang

    @staticmethod
    def _pipe_call(
        *,
        pipe: Any,
        audio: Any,
        sr: int,
        ignore_warning: bool,
        require_language: bool,
        source_language: str = "",
        text_consistency: bool = True,
        previous_text: str = "",
    ) -> dict[str, Any]:
        try:
            payload = {"raw": audio, "sampling_rate": int(sr)}
            normalized_lang = str(source_language or "").strip().lower()
            if Config.is_auto_language_value(normalized_lang):
                normalized_lang = ""
            prompt_ids = whisper_prompt_ids_from_text(pipe=pipe, text=previous_text) if bool(text_consistency) else None
            generate_kwargs: dict[str, Any] = {"task": "transcribe"}
            if normalized_lang:
                generate_kwargs["language"] = normalized_lang
            generate_kwargs["condition_on_prev_tokens"] = bool(text_consistency)
            if prompt_ids is not None:
                generate_kwargs["prompt_ids"] = prompt_ids

            try:
                out = pipe(
                    payload,
                    return_language=True,
                    return_timestamps=True,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=bool(ignore_warning),
                )
            except TypeError as ex:
                msg = str(ex)
                if "return_language" in msg and bool(require_language):
                    raise TranscriptionError("error.transcription.language_detection_unsupported") from ex
                if "return_timestamps" in msg:
                    raise TranscriptionError("error.transcription.timestamps_unsupported") from ex
                fallback_kwargs = dict(generate_kwargs)
                fallback_kwargs.pop("prompt_ids", None)
                try:
                    out = pipe(
                        payload,
                        return_language=True,
                        return_timestamps=True,
                        generate_kwargs=fallback_kwargs,
                        ignore_warning=bool(ignore_warning),
                    )
                except TypeError:
                    out = pipe(
                        payload,
                        return_timestamps=True,
                        ignore_warning=bool(ignore_warning),
                    )
        except Exception as exc:
            _LOG.exception("ASR pipeline call failed.")
            raise TranscriptionError("error.transcription.asr_failed") from exc

        if not isinstance(out, dict):
            out = {"text": str(out)}

        if bool(require_language):
            lang = extract_detected_language_from_result(out)
            if not lang:
                lang = detect_language_from_pipe_runtime(pipe=pipe, audio=audio, sr=sr)
                if lang:
                    out["language"] = lang
            if not lang:
                raise TranscriptionError("error.transcription.language_detection_failed")

        return out

    @staticmethod
    def _pick_source_language(*, default_lang: str | None, detected_lang: str) -> str:
        src = str(default_lang or "").strip().lower().replace("_", "-")
        src = src.split("-", 1)[0]
        if src and not Config.is_auto_language_value(src):
            return src
        return normalize_detected_language(detected_lang)

    @staticmethod
    def _extract_segments(result: dict[str, Any], *, offset_s: float) -> list[dict[str, Any]]:
        raw = TextPostprocessor.segments_from_result(result)
        return TranscriptWriter.offset_segments(raw, offset_s=offset_s)

    def _translate_segments(
        self,
        *,
        segments: list[dict[str, Any]],
        src_lang: str,
        tgt_lang: str,
        cancel_check: CancelCheckFn,
        progress_cb: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]] | None:
        total = max(1, len(segments))
        out: list[dict[str, Any]] = []

        for i, seg in enumerate(segments, start=1):
            if cancel_check():
                raise OperationCancelled()
            text = str(seg.get("text") or "")
            try:
                translated = self._translator.translate(text, src_lang=src_lang, tgt_lang=tgt_lang, log=None)
            except AppError:
                return None
            if not translated:
                return None
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
                mode_resolver=Config.get_transcription_output_mode,
                filename_resolver=FileManager.transcript_filename,
                unique_path_resolver=FileManager.ensure_unique_path,
                merged_text=merged_text,
                translated_text=translated_text,
                translated_segments=translated_segments,
                segments=segments,
            )
        except Exception as e:
            _LOG.error("Transcript save failed. name=%s detail=%s", stem, str(e))
            if item_error_cb is not None:
                item_error_cb(key, "error.transcription.save_failed", {"name": stem, "detail": str(e)})
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
