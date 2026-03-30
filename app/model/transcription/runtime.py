# app/model/transcription/runtime.py
from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any, Callable, Protocol

from app.model.core.config.policy import LanguagePolicy
from app.model.core.domain.errors import OperationCancelled
from app.model.transcription.chunking import estimate_chunks, iter_wav_mono_chunks, normalize_chunk_params
from app.model.transcription.whisper import (
    SIGNAL_WEAK,
    build_whisper_generate_kwargs,
    can_detect_language_from_audio,
    classify_audio_signal,
    debug_source_key,
    detect_language_from_pipe_runtime,
    extract_detected_language_from_result,
    filter_asr_text,
    normalize_detected_language,
    should_accept_detected_language,
    should_use_prompt,
    whisper_prompt_ids_from_text,
)
from app.model.transcription.writer import TextPostprocessor, TranscriptWriter

_LOG = logging.getLogger(__name__)

ProgressFn = Callable[[int], None]
CancelCheckFn = Callable[[], bool]
ErrorFactoryFn = Callable[..., Exception]


class ResultPostprocessorProtocol(Protocol):
    """Result normalizer used to turn raw ASR output into cleaned transcript text."""

    def plain_from_result(self, result: dict[str, Any]) -> str: ...

    def clean(self, text: str) -> str: ...


def pipe_call(
    *,
    pipe: Any,
    audio: Any,
    sr: int,
    ignore_warning: bool,
    require_language: bool,
    source_language: str = "",
    runtime_profile: dict[str, Any] | None = None,
    signal_kind: str = SIGNAL_WEAK,
    previous_text: str = "",
    error_factory: ErrorFactoryFn,
) -> dict[str, Any]:
    """Run a single ASR pipeline call with compatibility fallbacks."""

    profile = dict(runtime_profile or {})
    normalized_lang = str(source_language or "").strip().lower()
    if LanguagePolicy.is_auto(normalized_lang):
        normalized_lang = ""

    out: Any = {"text": ""}
    try:
        payload = {"raw": audio, "sampling_rate": int(sr)}
        prompt_ids = None
        if previous_text and should_use_prompt(signal_kind=signal_kind, profile=profile):
            prompt_ids = whisper_prompt_ids_from_text(pipe=pipe, text=previous_text)
        generate_kwargs = build_whisper_generate_kwargs(
            profile=profile,
            source_language=normalized_lang,
            prompt_ids=prompt_ids,
            signal_kind=signal_kind,
        )

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
                raise error_factory("error.transcription.language_detection_unsupported") from ex
            if "return_timestamps" in msg:
                raise error_factory("error.transcription.timestamps_unsupported") from ex

            fallback_kwargs = dict(generate_kwargs)
            fallback_kwargs.pop("prompt_ids", None)
            for candidate_kwargs in (
                fallback_kwargs,
                {
                    key: value
                    for key, value in fallback_kwargs.items()
                    if key not in (
                        "no_speech_threshold",
                        "logprob_threshold",
                        "compression_ratio_threshold",
                        "temperature",
                    )
                },
                {"task": "transcribe", **({"language": normalized_lang} if normalized_lang else {})},
            ):
                try:
                    out = pipe(
                        payload,
                        return_language=True,
                        return_timestamps=True,
                        generate_kwargs=candidate_kwargs,
                        ignore_warning=bool(ignore_warning),
                    )
                    break
                except TypeError:
                    continue
            else:
                out = pipe(
                    payload,
                    return_timestamps=True,
                    ignore_warning=bool(ignore_warning),
                )
    except Exception as ex:
        _LOG.exception("ASR pipeline call failed.")
        raise error_factory("error.transcription.asr_failed") from ex

    if not isinstance(out, dict):
        out = {"text": str(out)}

    if bool(require_language):
        lang = extract_detected_language_from_result(out)
        if (
            not lang
            and not normalized_lang
            and can_detect_language_from_audio(audio, sr=sr, signal_kind=signal_kind, profile=profile)
        ):
            lang = detect_language_from_pipe_runtime(pipe=pipe, audio=audio, sr=sr)
            if lang:
                out["language"] = lang
        if not lang and not normalized_lang:
            raise error_factory("error.transcription.language_detection_failed")

    return out


def extract_segments(result: dict[str, Any], *, offset_s: float) -> list[dict[str, Any]]:
    """Convert a raw ASR result into offset-adjusted timestamp segments."""

    raw = TextPostprocessor.segments_from_result(result)
    return TranscriptWriter.offset_segments(raw, offset_s=offset_s)


def pick_source_language(*, default_lang: str | None, detected_lang: str) -> str:
    """Resolve the language passed into translation from defaults or ASR detection."""

    source_lang = str(default_lang or "").strip().lower().replace("_", "-")
    source_lang = source_lang.split("-", 1)[0]
    if source_lang and not LanguagePolicy.is_auto(source_lang):
        return source_lang
    return normalize_detected_language(detected_lang)


def transcribe_wav(
    *,
    pipe: Any,
    wav_path: Path,
    key: str,
    chunk_len_s: int,
    stride_len_s: int,
    want_timestamps: bool,
    ignore_warning: bool,
    progress_cb: ProgressFn | None,
    cancel_check: CancelCheckFn,
    require_language: bool,
    source_language: str = "",
    runtime_profile: dict[str, Any] | None = None,
    postprocessor: ResultPostprocessorProtocol,
    error_factory: ErrorFactoryFn,
) -> tuple[str, list[dict[str, Any]], str]:
    """Transcribe a prepared mono 16k WAV file into text, segments, and detected language."""

    with wave.open(str(wav_path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        duration_s = 0.0 if rate <= 0 else float(frames) / float(rate)

    chunk_len_s, stride_len_s, _step_s = normalize_chunk_params(chunk_len_s, stride_len_s)
    chunk_count = estimate_chunks(duration_s, chunk_len_s, stride_len_s)
    profile = dict(runtime_profile or {})

    _LOG.debug(
        (
            "ASR runtime started. source_key=%s duration_s=%s chunks=%s "
            "require_language=%s timestamps=%s"
        ),
        debug_source_key(key),
        round(duration_s, 2),
        chunk_count,
        bool(require_language),
        bool(want_timestamps),
    )

    merged_parts: list[str] = []
    segments: list[dict[str, Any]] = []
    detected_lang = ""
    language_hits: dict[str, int] = {}
    previous_prompt_text = ""
    stable_language_min_hits = int(profile.get("stable_language_min_hits", 2) or 2)

    for idx, chunk in enumerate(
        iter_wav_mono_chunks(
            wav_path,
            chunk_len_s=chunk_len_s,
            stride_len_s=stride_len_s,
        ),
        start=1,
    ):
        if cancel_check():
            raise OperationCancelled()

        if chunk_count <= 1 and idx == 1 and progress_cb is not None:
            progress_cb(5)

        signal_kind = classify_audio_signal(chunk.audio, sr=chunk.sr, profile=profile)
        if signal_kind == "none":
            pct = int(round((idx / float(chunk_count)) * 100))
            if chunk_count <= 1:
                pct = min(95, max(0, pct))
            if progress_cb is not None:
                progress_cb(pct)
            continue

        out = pipe_call(
            pipe=pipe,
            audio=chunk.audio,
            sr=chunk.sr,
            ignore_warning=ignore_warning,
            require_language=require_language,
            source_language=source_language,
            runtime_profile=profile,
            signal_kind=signal_kind,
            previous_text=previous_prompt_text,
            error_factory=error_factory,
        )

        candidate_lang = extract_detected_language_from_result(out)
        if candidate_lang and should_accept_detected_language(signal_kind=signal_kind, profile=profile):
            language_hits[candidate_lang] = int(language_hits.get(candidate_lang, 0)) + 1
            if language_hits[candidate_lang] >= stable_language_min_hits and candidate_lang != detected_lang:
                detected_lang = candidate_lang

        raw_text = postprocessor.plain_from_result(out)
        text = filter_asr_text(
            raw_text,
            clean_fn=postprocessor.clean,
            signal_kind=signal_kind,
            profile=profile,
            reference_texts=merged_parts[-4:],
            from_tail=bool(idx == chunk_count and chunk_count > 1),
        )
        if text:
            merged_parts.append(text)
            if should_use_prompt(signal_kind=signal_kind, profile=profile):
                previous_prompt_text = "\n".join([part for part in merged_parts[-3:] if part]).strip()

            if want_timestamps:
                chunk_segments = extract_segments(out, offset_s=chunk.offset_s)
                if chunk_segments and raw_text and text != raw_text and len(chunk_segments) == 1:
                    chunk_segments[0]["text"] = text
                segments.extend(chunk_segments)

        pct = int(round((idx / float(chunk_count)) * 100))
        if chunk_count <= 1:
            pct = min(95, max(0, pct))
        if progress_cb is not None:
            progress_cb(pct)

    merged_text = TranscriptWriter.stitch_texts([part for part in merged_parts if part]).strip()
    if not merged_text and not bool(ignore_warning):
        raise error_factory("error.transcription.empty_result")

    if progress_cb is not None:
        progress_cb(100)

    _LOG.debug(
        "ASR runtime finished. source_key=%s text_chars=%s segments=%s detected_lang=%s",
        debug_source_key(key),
        len(merged_text),
        len(segments),
        detected_lang or "",
    )
    return merged_text, segments, detected_lang
