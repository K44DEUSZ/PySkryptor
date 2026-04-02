# app/model/transcription/processing.py
from __future__ import annotations

import logging
import time
import wave
from pathlib import Path
from typing import Any, Callable

from app.model.core.domain.errors import AppError, OperationCancelled
from app.model.core.utils.path_utils import ensure_unique_path
from app.model.core.utils.string_utils import sanitize_filename
from app.model.engines.contracts import TranscriptionEngineProtocol
from app.model.engines.types import TranscribeWavRequest
from app.model.transcription import workspace
from app.model.transcription.policy import TranscriptionOutputPolicy
from app.model.transcription.runtime import pick_source_language
from app.model.transcription.whisper import debug_source_key
from app.model.transcription.workspace import OutputDirectoryResolution, OutputResolver
from app.model.transcription.writer import TranscriptWriter
from app.model.translation.service import TranslationService

from .progress import SessionProgressTracker
from .session import (
    ItemOutputDirFn,
    ItemProcessResult,
    ItemProgressFn,
    MaterializedWorkItem,
    SessionCallbacks,
    SessionRuntime,
)

_LOG = logging.getLogger(__name__)


def emit_item_error(
    *,
    callbacks: SessionCallbacks,
    key: str,
    error: Exception,
) -> None:
    """Emit one normalized item error to the worker/controller layer."""

    if isinstance(error, AppError):
        callbacks.item_error(key, str(error.key), dict(error.params or {}))
        return
    callbacks.item_error(key, "error.generic", {"detail": str(error)})


def report_item_stage_progress(
    *,
    tracker: SessionProgressTracker,
    key: str,
    stage: str,
    pct: int,
    item_progress: ItemProgressFn | None = None,
) -> None:
    """Update item-local and session-global progress for one stage."""

    normalized_pct = int(pct)
    tracker.update(key, stage, normalized_pct)
    if item_progress is not None:
        item_progress(key, normalized_pct)


def build_stage_progress_callback(
    *,
    tracker: SessionProgressTracker,
    key: str,
    stage: str,
    item_progress: ItemProgressFn | None = None,
) -> Callable[..., None]:
    """Build one canonical callback adapter for one stage-aware progress hook."""

    def _report(pct: int, *_args: Any) -> None:
        report_item_stage_progress(
            tracker=tracker,
            key=key,
            stage=stage,
            pct=pct,
            item_progress=item_progress,
        )

    return _report


def resolve_item_output_dir(
    *,
    key: str,
    src_path: Path,
    forced_stem: str | None,
    apply_all: tuple[str, str] | None,
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
) -> OutputDirectoryResolution:
    """Resolve the output directory for one materialized source item."""

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


def prepare_item_audio(
    *,
    key: str,
    src_path: Path,
    runtime: SessionRuntime,
    cancel_check: Callable[[], bool],
) -> tuple[Path, float]:
    """Extract a source into a temporary mono WAV and update its progress weight."""

    report_item_stage_progress(
        tracker=runtime.tracker,
        key=key,
        stage="preprocess",
        pct=0,
    )
    preprocess_started = time.perf_counter()
    tmp_wav = workspace.ensure_tmp_wav(src_path, cancel_check=cancel_check)
    report_item_stage_progress(
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


def transcribe_wav(
    *,
    transcription_engine: TranscriptionEngineProtocol,
    wav_path: Path,
    key: str,
    chunk_len_s: int,
    stride_len_s: int,
    want_timestamps: bool,
    ignore_warning: bool,
    tracker: SessionProgressTracker,
    item_progress: ItemProgressFn,
    cancel_check: Callable[[], bool],
    require_language: bool,
    source_language: str = "",
    runtime_profile: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Run ASR for one prepared WAV file and return text, segments and detected language."""

    stage_progress = build_stage_progress_callback(
        tracker=tracker,
        key=key,
        stage="transcribe",
        item_progress=item_progress,
    )
    result = transcription_engine.transcribe_wav(
        TranscribeWavRequest(
            wav_path=str(wav_path),
            key=key,
            chunk_length_s=chunk_len_s,
            stride_length_s=stride_len_s,
            want_timestamps=want_timestamps,
            ignore_warning=ignore_warning,
            require_language=require_language,
            source_language=source_language,
            runtime_profile=dict(runtime_profile or {}),
        ),
        cancel_check=cancel_check,
        progress_cb=stage_progress,
    )
    return result.merged_text, list(result.segments), result.detected_language


def translate_item_if_needed(
    *,
    translation_service: TranslationService,
    key: str,
    merged_text: str,
    segments: list[dict[str, Any]],
    detected_lang: str,
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
) -> tuple[str, list[dict[str, Any]] | None, bool]:
    """Translate one transcript item when the current session requires it."""

    translated_text = ""
    translated_segments: list[dict[str, Any]] | None = None
    had_errors = False

    if not runtime.options.want_translate:
        report_item_stage_progress(
            tracker=runtime.tracker,
            key=key,
            stage="translate",
            pct=100,
            item_progress=callbacks.item_progress,
        )
        return translated_text, translated_segments, had_errors

    callbacks.item_status(key, "status.translating")
    report_item_stage_progress(
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
    stage_progress = build_stage_progress_callback(
        tracker=runtime.tracker,
        key=key,
        stage="translate",
        item_progress=callbacks.item_progress,
    )

    if not src_lang:
        had_errors = True
        callbacks.item_error(key, "error.translation.missing_source_language", {})
    else:
        try:
            if runtime.options.want_timestamps and segments:
                translated = translation_service.translate_segments(
                    segments=segments,
                    src_lang=src_lang,
                    tgt_lang=runtime.options.tgt_lang,
                    cancel_check=callbacks.cancel_check,
                    progress_cb=stage_progress,
                )
                translated_text = translated.plain_text
                translated_segments = translated.segments
            else:
                translated_text = translation_service.translate_text(
                    text=merged_text,
                    src_lang=src_lang,
                    tgt_lang=runtime.options.tgt_lang,
                    cancel_check=callbacks.cancel_check,
                    progress_cb=stage_progress,
                )
        except AppError as ex:
            had_errors = True
            callbacks.item_error(
                key,
                str(ex.key or "error.generic"),
                dict(ex.params or {}),
            )
            translated_text = ""
    report_item_stage_progress(
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


def write_outputs(
    *,
    key: str,
    stem: str,
    out_dir: Path,
    merged_text: str,
    translated_text: str,
    translated_segments: list[dict[str, Any]] | None,
    segments: list[dict[str, Any]],
    output_mode_ids: list[str],
    transcript_ready: Callable[[str, str], None],
    item_output_dir_cb: ItemOutputDirFn | None,
    item_error_cb: Callable[[str, str, dict[str, Any]], None] | None,
    cancel_check: Callable[[], bool],
) -> Path | None:
    """Write transcript outputs for one processed item."""

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


def save_item_outputs(
    *,
    key: str,
    stem: str,
    out_dir: Path,
    merged_text: str,
    translated_text: str,
    translated_segments: list[dict[str, Any]] | None,
    segments: list[dict[str, Any]],
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
) -> Path | None:
    """Persist all requested outputs for one processed source item."""

    callbacks.item_status(key, "status.saving")
    save_started = time.perf_counter()
    primary = write_outputs(
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
    report_item_stage_progress(
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


def cleanup_tmp_wav(*, tmp_wav: Path | None, src_path: Path) -> None:
    """Remove a temporary WAV created for one item when it is safe to do so."""

    try:
        if tmp_wav is not None and tmp_wav != src_path and tmp_wav.suffix.lower() == ".wav":
            tmp_wav.unlink(missing_ok=True)
    except OSError as ex:
        _LOG.debug("Temp WAV cleanup skipped. path=%s detail=%s", tmp_wav, ex)


def process_materialized_work_item(
    *,
    transcription_engine: TranscriptionEngineProtocol,
    translation_service: TranslationService,
    work_item: MaterializedWorkItem,
    apply_all: tuple[str, str] | None,
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
) -> ItemProcessResult:
    """Process one materialized work item from WAV preparation to output saving."""

    key = work_item.source_key
    src_path = work_item.source_path
    forced_stem = work_item.forced_stem

    if callbacks.cancel_check():
        return ItemProcessResult(False, False, True, apply_all)

    resolution = resolve_item_output_dir(
        key=key,
        src_path=src_path,
        forced_stem=forced_stem,
        apply_all=apply_all,
        runtime=runtime,
        callbacks=callbacks,
    )
    if resolution.skipped or resolution.output_dir is None:
        return ItemProcessResult(False, False, False, apply_all)

    out_dir = resolution.output_dir
    stem = resolution.stem
    apply_all = resolution.apply_all

    tmp_wav: Path | None = None
    try:
        callbacks.item_status(key, "status.processing")
        tmp_wav, _ = prepare_item_audio(
            key=key,
            src_path=src_path,
            runtime=runtime,
            cancel_check=callbacks.cancel_check,
        )

        callbacks.item_status(key, "status.transcribing")
        transcribe_started = time.perf_counter()
        merged_text, segments, detected_lang = transcribe_wav(
            transcription_engine=transcription_engine,
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

        translated_text, translated_segments, translate_had_errors = translate_item_if_needed(
            translation_service=translation_service,
            key=key,
            merged_text=merged_text,
            segments=segments,
            detected_lang=detected_lang,
            runtime=runtime,
            callbacks=callbacks,
        )

        primary = save_item_outputs(
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
            return ItemProcessResult(True, bool(translate_had_errors), False, apply_all)

        callbacks.item_status(key, "status.error")
        return ItemProcessResult(False, True, False, apply_all)
    except OperationCancelled:
        callbacks.item_status(key, "status.cancelled")
        workspace.delete_output_dir_if_empty(out_dir)
        return ItemProcessResult(False, False, True, apply_all)
    except Exception as ex:
        emit_item_error(callbacks=callbacks, key=key, error=ex)
        callbacks.item_status(key, "status.error")
        return ItemProcessResult(False, True, False, apply_all)
    finally:
        cleanup_tmp_wav(tmp_wav=tmp_wav, src_path=src_path)
