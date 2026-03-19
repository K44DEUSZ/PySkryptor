# app/controller/tasks/live_transcription_task.py
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np
import re
from dataclasses import dataclass
from PyQt5 import QtCore

from app.controller.platform.microphone import (
    ensure_supported_format,
    make_pcm16_mono_format,
    resolve_input_device,
)
from app.controller.support.cancellation import CancellationToken
from app.model.helpers.errors import AppError
from app.model.helpers.chunking import pcm16le_bytes_to_float32, seconds_to_frames
from app.model.io.transcript_writer import TextPostprocessor
from app.model.services.translation_service import TranslationService
from app.model.services.transcription_service import (
    TranscriptionError,
    audio_has_meaningful_signal,
    audio_rms_level,
    detect_language_from_pipe_runtime,
    extract_detected_language_from_result,
)
from app.model.config.app_config import AppConfig as Config

_LOG = logging.getLogger(__name__)


# ----- Errors -----
class LiveError(AppError):
    """Key-based error used for i18n-friendly live task failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


LiveCancelCheckFn = Callable[[], bool]

# ----- Live session helpers -----
@dataclass(frozen=True)
class LiveUpdate:
    """Incremental update produced by live transcription."""

    detected_language: str
    display_source_text: str
    display_target_text: str
    archive_source_text: str
    archive_target_text: str

_MERGE_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class LiveSession:
    """Stateful live transcription session fed with PCM16 audio bytes."""

    OUTPUT_MODE_STREAM = Config.LIVE_OUTPUT_MODE_STREAM
    OUTPUT_MODE_CUMULATIVE = Config.LIVE_OUTPUT_MODE_CUMULATIVE

    SIGNAL_NONE = "none"
    SIGNAL_WEAK = "weak"
    SIGNAL_SOLID = "solid"

    def __init__(
        self,
        *,
        pipe: Any,
        source_language: str,
        target_language: str,
        translate_enabled: bool,
        cancel_check: LiveCancelCheckFn,
        preset_id: str = Config.LIVE_DEFAULT_PRESET,
        output_mode: str = OUTPUT_MODE_CUMULATIVE,
    ) -> None:
        self._pipe = pipe
        self._cancel_check = cancel_check

        self._src_lang = Config.normalize_policy_value(source_language)
        if Config.is_auto_language_value(self._src_lang):
            self._src_lang = ""

        self._tgt_lang = Config.normalize_policy_value(target_language)
        self._translate = bool(translate_enabled) and bool(self._tgt_lang) and not Config.is_auto_language_value(self._tgt_lang)

        model_cfg = Config.transcription_model_raw_cfg_dict()
        self._ignore_warning = bool(model_cfg.get("ignore_warning", False))
        self._text_consistency = bool(model_cfg.get("text_consistency", True))

        self._sr = Config.ASR_SAMPLE_RATE
        self._output_mode = Config.normalize_live_output_mode(output_mode)
        self._stream_only = self._output_mode == self.OUTPUT_MODE_STREAM
        self._preset_id = Config.normalize_live_preset(preset_id)
        self._profile = Config.live_runtime_profile(output_mode=self._output_mode, preset=self._preset_id)

        chunk_len_s = int(self._profile.get("chunk_length_s", 4) or 4)
        stride_len_s = int(self._profile.get("stride_length_s", 3) or 3)
        chunk_len_s = max(1, min(chunk_len_s, 30))
        stride_len_s = max(0, min(stride_len_s, max(0, chunk_len_s - 1)))
        self._chunk_f, self._stride_f, self._step_f = seconds_to_frames(self._sr, chunk_len_s, stride_len_s)

        self._buf = bytearray()
        self._silent_run_s = 0.0
        self._silence_gap_flushed = False
        self._stream_gap_cleared = False

        self._silence_level_threshold = float(self._profile.get("silence_level_threshold", 0.055))
        self._silence_audio_rms_min = float(self._profile.get("silence_audio_rms_min", 0.007))
        self._silence_tail_keep_s = float(self._profile.get("silence_tail_keep_s", 0.24))
        self._tail_flush_min_s = float(self._profile.get("tail_flush_min_s", 0.20))

        self._weak_rms_threshold = float(self._profile.get("weak_rms_threshold", 0.0115))
        self._weak_activity_floor = float(self._profile.get("weak_activity_floor", 0.0055))
        self._weak_active_ratio_threshold = float(self._profile.get("weak_active_ratio_threshold", 0.012))
        self._weak_active_ms_threshold = float(self._profile.get("weak_active_ms_threshold", 55.0))

        self._solid_rms_threshold = float(self._profile.get("solid_rms_threshold", 0.0145))
        self._solid_activity_floor = float(self._profile.get("solid_activity_floor", 0.0065))
        self._solid_active_ratio_threshold = float(self._profile.get("solid_active_ratio_threshold", 0.022))
        self._solid_active_ms_threshold = float(self._profile.get("solid_active_ms_threshold", 85.0))

        self._language_detect_rms_threshold = float(self._profile.get("language_detect_rms_threshold", 0.03))
        self._language_detect_activity_floor = float(self._profile.get("language_detect_activity_floor", 0.009))
        self._language_detect_active_ratio_threshold = float(self._profile.get("language_detect_active_ratio_threshold", 0.07))
        self._language_detect_active_ms_threshold = float(self._profile.get("language_detect_active_ms_threshold", 160.0))

        self._artifact_min_chars = int(self._profile.get("artifact_min_chars", 3) or 3)
        self._artifact_min_words = int(self._profile.get("artifact_min_words", 2) or 2)
        self._artifact_tail_max_words = int(self._profile.get("artifact_tail_max_words", 2) or 2)
        self._artifact_tail_max_chars = int(self._profile.get("artifact_tail_max_chars", 14) or 14)

        self._commit_silence_s = float(self._profile.get("commit_silence_s", 0.62))
        self._stream_clear_after_s = float(self._profile.get("stream_clear_after_s", 1.15))
        self._stream_show_previous_caption = bool(self._profile.get("stream_show_previous_caption", False))
        self._stream_replace_prefix_ratio = float(self._profile.get("stream_replace_prefix_ratio", 0.62))
        self._stream_commit_min_words = int(self._profile.get("stream_commit_min_words", 6) or 6)
        self._cumulative_merge_overlap_min = int(self._profile.get("cumulative_merge_overlap_min", 2) or 2)
        self._stream_translation_min_chars = int(self._profile.get("stream_translation_min_chars", 18) or 18)
        self._cumulative_translation_min_chars = int(self._profile.get("cumulative_translation_min_chars", 20) or 20)

        if not self._text_consistency:
            self._commit_silence_s = max(0.38, self._commit_silence_s - 0.10)
            self._stream_clear_after_s = max(self._commit_silence_s + 0.20, self._stream_clear_after_s - 0.15)
            self._stream_replace_prefix_ratio = max(0.52, self._stream_replace_prefix_ratio - 0.08)
            self._cumulative_merge_overlap_min = max(1, self._cumulative_merge_overlap_min - 1)

        self._post = TextPostprocessor()
        self._translator = TranslationService()

        self._detected_lang = ""
        self._archive_source = ""
        self._archive_target = ""

        self._draft_source = ""
        self._draft_target = ""

        self._previous_source = ""
        self._previous_target = ""
        self._current_source = ""
        self._current_target = ""
        self._stream_source = ""
        self._stream_target = ""

    @staticmethod
    def _normalized_merge_tokens(text: str) -> list[str]:
        return [tok for tok in _MERGE_TOKEN_RE.findall(str(text or "").lower()) if tok]

    @classmethod
    def _shared_prefix_token_count(cls, left: str, right: str) -> int:
        left_tokens = cls._normalized_merge_tokens(left)
        right_tokens = cls._normalized_merge_tokens(right)
        limit = min(len(left_tokens), len(right_tokens))
        idx = 0
        while idx < limit and left_tokens[idx] == right_tokens[idx]:
            idx += 1
        return idx

    @classmethod
    def _word_count(cls, text: str) -> int:
        return len(cls._normalized_merge_tokens(text))

    @staticmethod
    def _has_terminal_punctuation(text: str) -> bool:
        return str(text or "").rstrip().endswith((".", "!", "?", "…", ";", ":"))

    @classmethod
    def _is_revision(cls, existing: str, incoming: str, *, prefix_ratio: float = 0.62) -> bool:
        existing_tokens = cls._normalized_merge_tokens(existing)
        incoming_tokens = cls._normalized_merge_tokens(incoming)
        if not existing_tokens or not incoming_tokens:
            return False

        shorter_len = min(len(existing_tokens), len(incoming_tokens))
        shared_prefix = cls._shared_prefix_token_count(existing, incoming)
        if shared_prefix == shorter_len:
            return True

        min_prefix = 3 if shorter_len >= 4 else max(1, shorter_len - 1)
        return shared_prefix >= min_prefix and shared_prefix >= int(shorter_len * max(0.5, float(prefix_ratio)))

    @classmethod
    def _choose_more_complete_text(cls, existing: str, incoming: str, *, prefix_ratio: float = 0.62) -> str:
        existing = str(existing or "").strip()
        incoming = str(incoming or "").strip()
        if not existing:
            return incoming
        if not incoming:
            return existing
        if cls._is_revision(existing, incoming, prefix_ratio=prefix_ratio):
            return incoming if len(incoming) >= len(existing) else existing
        if cls._is_revision(incoming, existing, prefix_ratio=prefix_ratio):
            return existing
        return incoming

    @classmethod
    def _merge_text(cls, previous: str, current: str, *, min_overlap_words: int = 2) -> str:
        previous = str(previous or "").strip()
        current = str(current or "").strip()
        if not previous:
            return current
        if not current:
            return previous

        previous_words = previous.split()
        current_words = current.split()
        previous_tokens = cls._normalized_merge_tokens(previous)
        current_tokens = cls._normalized_merge_tokens(current)

        if not previous_tokens:
            return current
        if not current_tokens:
            return previous
        if previous_tokens == current_tokens:
            return previous if len(previous) >= len(current) else current

        max_overlap = min(18, len(previous_tokens), len(current_tokens), len(previous_words), len(current_words))
        for overlap in range(max_overlap, 0, -1):
            if previous_tokens[-overlap:] == current_tokens[:overlap]:
                if overlap < int(max(1, min_overlap_words)) and overlap != max_overlap:
                    continue
                tail_words = current_words[overlap:]
                if not tail_words:
                    return previous
                return (previous + " " + " ".join(tail_words)).strip()

        if len(current_tokens) <= len(previous_tokens) and previous_tokens[-len(current_tokens):] == current_tokens:
            return previous

        return (previous + " " + current).strip()

    def _trim_buffer_to_seconds(self, seconds: float) -> None:
        keep_bytes = int(max(0.0, float(seconds)) * float(max(1, self._sr)) * 2.0)
        if keep_bytes <= 0:
            self._buf.clear()
            return
        if len(self._buf) > keep_bytes:
            del self._buf[:-keep_bytes]

    def _resolved_translation_source_language(self) -> str:
        src_lang = self._detected_lang or self._src_lang
        if not src_lang:
            try:
                cfg_src = str(Config.translation_source_language() or "").strip().lower()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                cfg_src = ""
            if cfg_src and not Config.is_translation_source_deferred_value(cfg_src):
                src_lang = cfg_src
        src_lang = str(src_lang or "").strip().replace("_", "-")
        if "-" in src_lang:
            src_lang = src_lang.split("-", 1)[0]
        return src_lang

    def _translate_text(self, source_text: str, *, min_chars: int) -> str:
        source_text = str(source_text or "").strip()
        if not self._translate or not source_text or len(source_text) < int(max(1, min_chars)):
            return ""

        src_lang = self._resolved_translation_source_language()
        if not src_lang:
            return ""

        try:
            return self._translator.translate(
                source_text,
                src_lang=src_lang,
                tgt_lang=self._tgt_lang,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""

    def _translated_stream_text(self, source_text: str) -> str:
        return self._translate_text(source_text, min_chars=self._stream_translation_min_chars)

    def _active_reference_texts(self) -> list[str]:
        refs = [
            self._current_source,
            self._previous_source,
            self._draft_source,
            self._archive_source,
        ]
        return [str(item or "").strip() for item in refs if str(item or "").strip()]

    def _relates_to_active_text(self, text: str) -> bool:
        current = str(text or "").strip()
        if not current:
            return False
        for ref in self._active_reference_texts():
            if self._is_revision(ref, current, prefix_ratio=self._stream_replace_prefix_ratio):
                return True
            if self._is_revision(current, ref, prefix_ratio=self._stream_replace_prefix_ratio):
                return True
        return False

    def _classify_audio_signal(self, audio: Any) -> str:
        if not audio_has_meaningful_signal(
            audio,
            sr=self._sr,
            rms_min=self._weak_rms_threshold,
            activity_floor=self._weak_activity_floor,
            active_ratio_min=self._weak_active_ratio_threshold,
            active_ms_min=self._weak_active_ms_threshold,
        ):
            return self.SIGNAL_NONE

        if audio_has_meaningful_signal(
            audio,
            sr=self._sr,
            rms_min=self._solid_rms_threshold,
            activity_floor=self._solid_activity_floor,
            active_ratio_min=self._solid_active_ratio_threshold,
            active_ms_min=self._solid_active_ms_threshold,
        ):
            return self.SIGNAL_SOLID
        return self.SIGNAL_WEAK

    def _can_detect_language_from_audio(self, audio: Any, *, signal_kind: str) -> bool:
        if signal_kind == self.SIGNAL_SOLID:
            return True
        return audio_has_meaningful_signal(
            audio,
            sr=self._sr,
            rms_min=self._language_detect_rms_threshold,
            activity_floor=self._language_detect_activity_floor,
            active_ratio_min=self._language_detect_active_ratio_threshold,
            active_ms_min=self._language_detect_active_ms_threshold,
        )

    def _transcribe_audio(self, audio: Any, *, signal_kind: str) -> tuple[str, bool]:
        payload = {"array": audio, "sampling_rate": self._sr}

        generate_kwargs: dict[str, Any] = {"task": "transcribe"}
        if self._src_lang:
            generate_kwargs["language"] = self._src_lang

        try:
            try:
                result = self._pipe(
                    payload,
                    return_language=True,
                    return_timestamps=False,
                    generate_kwargs=generate_kwargs,
                    ignore_warning=self._ignore_warning,
                )
            except TypeError:
                result = self._pipe(
                    payload,
                    return_language=True,
                    return_timestamps=False,
                    generate_kwargs=generate_kwargs,
                )
        except Exception as exc:
            _LOG.exception("ASR pipeline call failed.")
            raise TranscriptionError("error.transcription.asr_failed") from exc

        if not isinstance(result, dict):
            result = {"text": str(result)}

        language_changed = False
        detected = extract_detected_language_from_result(result)
        if not detected and (self._translate or not self._src_lang) and self._can_detect_language_from_audio(audio, signal_kind=signal_kind):
            detected = detect_language_from_pipe_runtime(
                pipe=self._pipe,
                audio=audio,
                sr=self._sr,
            )
            if detected:
                result["language"] = detected

        if detected and detected != self._detected_lang and (signal_kind == self.SIGNAL_SOLID or not self._detected_lang):
            self._detected_lang = detected
            language_changed = True

        return str(result.get("text") or ""), language_changed

    def _filter_result_text(self, text: str, *, signal_kind: str, from_tail: bool) -> str:
        text = self._post.clean(str(text or ""))
        if not text:
            return ""

        words = self._word_count(text)
        if words <= 0:
            return ""

        if len(text) < self._artifact_min_chars and not self._relates_to_active_text(text):
            return ""

        if signal_kind == self.SIGNAL_WEAK or from_tail:
            if words <= 1 and not self._relates_to_active_text(text):
                return ""
            if (
                words <= self._artifact_tail_max_words
                and len(text) <= self._artifact_tail_max_chars
                and (not self._has_terminal_punctuation(text))
                and (not self._relates_to_active_text(text))
            ):
                return ""

        return text

    def _refresh_stream_text(self) -> None:
        previous_source = str(self._previous_source or "").strip()
        current_source = str(self._current_source or "").strip()
        previous_target = str(self._previous_target or "").strip()
        current_target = str(self._current_target or "").strip()

        display_source = current_source
        if not display_source and self._stream_show_previous_caption:
            display_source = previous_source
        elif not display_source and previous_source:
            display_source = previous_source

        display_target = current_target if self._translate else ""
        if self._translate and (not display_target) and self._stream_show_previous_caption:
            display_target = previous_target
        elif self._translate and (not display_target) and previous_target:
            display_target = previous_target

        self._stream_source = display_source
        self._stream_target = display_target

    def _clear_stream_text(self) -> bool:
        had_stream = bool(self._previous_source or self._current_source or self._stream_source or self._stream_target)
        self._previous_source = ""
        self._previous_target = ""
        self._clear_current_stream_caption(refresh=False)
        self._stream_source = ""
        self._stream_target = ""
        return had_stream

    def _clear_current_stream_caption(self, *, refresh: bool = True) -> None:
        self._current_source = ""
        self._current_target = ""
        if refresh:
            self._refresh_stream_text()

    def _set_stream_caption(self, *, current: bool, source_text: str) -> bool:
        source_text = str(source_text or "").strip()
        if not source_text:
            return False

        target_text = self._translated_stream_text(source_text)
        current_source = self._current_source if current else self._previous_source
        current_target = self._current_target if current else self._previous_target
        changed = source_text != current_source or target_text != current_target

        if current:
            self._current_source = source_text
            self._current_target = target_text
        else:
            self._previous_source = source_text
            self._previous_target = target_text

        self._refresh_stream_text()
        return changed

    def _set_current_stream_caption(self, source_text: str) -> bool:
        return self._set_stream_caption(current=True, source_text=source_text)

    def _set_previous_stream_caption(self, source_text: str) -> bool:
        return self._set_stream_caption(current=False, source_text=source_text)

    def _commit_stream_caption(self) -> bool:
        source_text = str(self._current_source or "").strip()
        target_text = str(self._current_target or "").strip()
        if not source_text and not target_text:
            return False

        changed = source_text != self._previous_source or target_text != self._previous_target or bool(self._current_source or self._current_target)
        self._previous_source = source_text
        self._previous_target = target_text
        self._clear_current_stream_caption(refresh=False)
        self._refresh_stream_text()
        return changed

    def _should_commit_stream_caption(self, current_text: str) -> bool:
        current_text = str(current_text or "").strip()
        if not current_text:
            return False
        if self._has_terminal_punctuation(current_text):
            return True
        return self._word_count(current_text) >= int(max(1, self._stream_commit_min_words))

    def _refresh_cumulative_draft_target(self) -> None:
        self._draft_target = self._translate_text(self._draft_source, min_chars=self._cumulative_translation_min_chars)

    def _set_cumulative_draft(self, source_text: str) -> bool:
        next_draft = str(source_text or "").strip()
        if next_draft == self._draft_source:
            return False
        self._draft_source = next_draft
        self._refresh_cumulative_draft_target()
        return True

    def _clear_cumulative_draft(self) -> None:
        self._draft_source = ""
        self._draft_target = ""

    def _commit_cumulative_draft(self) -> bool:
        source_text = str(self._draft_source or "").strip()
        if not source_text:
            return False

        if self._translate and not self._draft_target:
            self._refresh_cumulative_draft_target()

        target_text = str(self._draft_target or "").strip()
        merged_source = self._merge_text(
            self._archive_source,
            source_text,
            min_overlap_words=self._cumulative_merge_overlap_min,
        )
        merged_target = self._merge_text(
            self._archive_target,
            target_text,
            min_overlap_words=1,
        ) if target_text else self._archive_target

        changed = merged_source != self._archive_source or merged_target != self._archive_target
        self._archive_source = merged_source
        self._archive_target = merged_target
        self._clear_cumulative_draft()
        return changed

    def _commit_draft(self) -> bool:
        if self._stream_only:
            return self._commit_stream_caption()
        return self._commit_cumulative_draft()

    def _build_update(self) -> LiveUpdate:
        return LiveUpdate(
            detected_language=self._detected_lang,
            display_source_text=self._stream_source,
            display_target_text=self._stream_target,
            archive_source_text=self._archive_source,
            archive_target_text=self._archive_target,
        )

    def _update_stream_text(self, source_text: str) -> tuple[bool, bool]:
        source_text = str(source_text or "").strip()
        if not source_text:
            return False, False

        if not self._current_source:
            if self._previous_source and self._is_revision(self._previous_source, source_text, prefix_ratio=self._stream_replace_prefix_ratio):
                next_previous = self._choose_more_complete_text(
                    self._previous_source,
                    source_text,
                    prefix_ratio=self._stream_replace_prefix_ratio,
                )
                if next_previous == self._previous_source:
                    return False, False
                return False, self._set_previous_stream_caption(next_previous)
            changed = self._set_current_stream_caption(source_text)
            return False, changed

        if self._is_revision(self._current_source, source_text, prefix_ratio=self._stream_replace_prefix_ratio):
            next_current = self._choose_more_complete_text(
                self._current_source,
                source_text,
                prefix_ratio=self._stream_replace_prefix_ratio,
            )
            changed = self._set_current_stream_caption(next_current)
            return False, changed

        merged_current = self._merge_text(
            self._current_source,
            source_text,
            min_overlap_words=1,
        )
        if merged_current != self._current_source and merged_current != (str(self._current_source or "").strip() + " " + source_text).strip():
            changed = self._set_current_stream_caption(merged_current)
            return False, changed

        if not self._should_commit_stream_caption(self._current_source):
            next_current = self._choose_more_complete_text(
                self._current_source,
                source_text,
                prefix_ratio=self._stream_replace_prefix_ratio,
            )
            changed = self._set_current_stream_caption(next_current)
            return False, changed

        committed = self._commit_stream_caption()
        changed = self._set_current_stream_caption(source_text)
        return committed, committed or changed

    def _update_cumulative_text(self, source_text: str) -> tuple[bool, bool]:
        source_text = str(source_text or "").strip()
        if not source_text:
            return False, False

        if not self._draft_source:
            return False, self._set_cumulative_draft(source_text)

        if self._is_revision(self._draft_source, source_text, prefix_ratio=self._stream_replace_prefix_ratio):
            next_draft = self._choose_more_complete_text(
                self._draft_source,
                source_text,
                prefix_ratio=self._stream_replace_prefix_ratio,
            )
            return False, self._set_cumulative_draft(next_draft)

        committed = self._commit_cumulative_draft()
        changed = self._set_cumulative_draft(source_text)
        return committed, changed

    def _has_active_text(self) -> bool:
        if self._stream_only:
            return bool(str(self._current_source or "").strip() or str(self._previous_source or "").strip())
        return bool(str(self._draft_source or "").strip() or str(self._archive_source or "").strip())

    def _run_pipeline_on_pcm16(self, data: bytes, *, ignore_cancel: bool = False, from_tail: bool = False) -> tuple[bool, bool]:
        if not data or ((not ignore_cancel) and self._cancel_check()):
            return False, False

        audio = pcm16le_bytes_to_float32(data)
        if audio.size == 0:
            return False, False

        signal_kind = self._classify_audio_signal(audio)
        if signal_kind == self.SIGNAL_NONE:
            return False, False
        if signal_kind == self.SIGNAL_WEAK and not self._has_active_text():
            return False, False

        current_source, language_changed = self._transcribe_audio(audio, signal_kind=signal_kind)
        current_source = self._filter_result_text(current_source, signal_kind=signal_kind, from_tail=from_tail)
        if not current_source:
            return False, language_changed

        if self._stream_only:
            committed, changed = self._update_stream_text(current_source)
        else:
            committed, changed = self._update_cumulative_text(current_source)

        if (not ignore_cancel) and self._cancel_check():
            return committed, changed or language_changed
        return committed, changed or language_changed

    def _flush_tail_buffer(self, *, ignore_cancel: bool = False, min_seconds: float | None = None) -> tuple[bool, bool]:
        if self._stream_only or not self._buf:
            return False, False

        min_s = self._tail_flush_min_s if min_seconds is None else max(0.0, float(min_seconds))
        buffered_seconds = float(len(self._buf)) / float(max(1, self._sr * 2))
        if buffered_seconds < min_s:
            return False, False

        data = bytes(self._buf)
        self._buf.clear()
        return self._run_pipeline_on_pcm16(data, ignore_cancel=ignore_cancel, from_tail=True)

    def _flush_on_silence(self, *, ignore_cancel: bool = False) -> list[LiveUpdate]:
        out: list[LiveUpdate] = []

        committed_from_tail = False
        changed_from_tail = False
        if not self._stream_only:
            committed_from_tail, changed_from_tail = self._flush_tail_buffer(
                ignore_cancel=ignore_cancel,
                min_seconds=self._tail_flush_min_s,
            )

        committed_from_draft = self._commit_draft()
        if committed_from_tail or changed_from_tail or committed_from_draft:
            out.append(self._build_update())

        self._buf.clear()
        return out

    def finalize(self, *, ignore_cancel: bool = False) -> list[LiveUpdate]:
        out: list[LiveUpdate] = []

        if self._buf and (ignore_cancel or not self._cancel_check()) and (not self._stream_only):
            committed_from_tail, changed_from_tail = self._flush_tail_buffer(ignore_cancel=ignore_cancel, min_seconds=0.0)
            if committed_from_tail or changed_from_tail:
                out.append(self._build_update())

        if self._commit_draft():
            out.append(self._build_update())
        elif out or self._detected_lang:
            out.append(self._build_update())

        self._silent_run_s = 0.0
        self._silence_gap_flushed = False
        self._stream_gap_cleared = False
        return out

    def push_pcm16(self, data: bytes, *, level: float | None = None, ignore_cancel: bool = False) -> List[LiveUpdate]:
        if not data or ((not ignore_cancel) and self._cancel_check()):
            return []

        out: List[LiveUpdate] = []

        chunk_seconds = float(len(data)) / float(max(1, self._sr * 2))
        level_f = max(0.0, float(level or 0.0))
        is_silence = level_f <= self._silence_level_threshold
        if is_silence:
            audio = pcm16le_bytes_to_float32(data)
            is_silence = audio_rms_level(audio) <= self._silence_audio_rms_min

        if is_silence:
            self._buf.extend(data)
            self._silent_run_s += chunk_seconds

            if self._silent_run_s >= self._commit_silence_s and not self._silence_gap_flushed:
                out.extend(self._flush_on_silence(ignore_cancel=ignore_cancel))
                self._silence_gap_flushed = True

            if self._stream_only and self._silent_run_s >= self._stream_clear_after_s and not self._stream_gap_cleared:
                if self._clear_stream_text():
                    out.append(self._build_update())
                self._stream_gap_cleared = True

            if self._silence_gap_flushed:
                self._trim_buffer_to_seconds(self._silence_tail_keep_s)
            return out

        if self._silent_run_s >= self._commit_silence_s and not self._silence_gap_flushed:
            out.extend(self._flush_on_silence(ignore_cancel=ignore_cancel))

        self._buf.extend(data)
        self._silent_run_s = 0.0
        self._silence_gap_flushed = False
        self._stream_gap_cleared = False

        bytes_per_frame = 2
        while len(self._buf) >= self._chunk_f * bytes_per_frame:
            if (not ignore_cancel) and self._cancel_check():
                break

            chunk = bytes(self._buf[: self._chunk_f * bytes_per_frame])
            del self._buf[: self._step_f * bytes_per_frame]

            committed, partial_changed = self._run_pipeline_on_pcm16(chunk, ignore_cancel=ignore_cancel)
            if committed or partial_changed:
                out.append(self._build_update())

        return out


class LiveTranscriptionWorker(QtCore.QObject):
    """Captures audio from an input device and performs live transcription."""

    status = QtCore.pyqtSignal(str)
    detected_language = QtCore.pyqtSignal(str)
    source_text = QtCore.pyqtSignal(str)
    target_text = QtCore.pyqtSignal(str)
    archive_source_text = QtCore.pyqtSignal(str)
    archive_target_text = QtCore.pyqtSignal(str)
    spectrum = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str, dict)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        pipe: Any,
        device_name: str = "",
        source_language: str = "",
        target_language: str = "",
        translate_enabled: bool = False,
        preset_id: str = Config.LIVE_DEFAULT_PRESET,
        output_mode: str = LiveSession.OUTPUT_MODE_CUMULATIVE,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__()
        self._pipe = pipe
        self._device_name = str(device_name or "").strip()

        self._src_lang = str(source_language or "").strip()
        self._tgt_lang = str(target_language or "").strip()
        self._translate_enabled = bool(translate_enabled)
        self._preset_id = Config.normalize_live_preset(preset_id)
        self._output_mode = Config.normalize_live_output_mode(output_mode)

        self._cancel = cancel_token or CancellationToken()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._run_finished = False

        self._session: LiveSession | None = None
        self._qtmm: Any = None
        self._fmt: Any = None
        self._device_info: Any = None
        self._audio_in: Any = None
        self._io: Any = None
        self._timer: QtCore.QTimer | None = None
        self._status_key: str = ""
        self._last_emitted_language: str = ""
        self._last_emitted_source: str = ""
        self._last_emitted_target: str = ""
        self._last_emitted_archive_source: str = ""
        self._last_emitted_archive_target: str = ""
        self._last_spectrum_emit_s: float = 0.0
        self._spectrum_emit_interval_s: float = 0.08
        self._last_backlog_debug_s: float = 0.0
        self._backlog_debug_interval_s: float = 0.6
        self._backlog_compactions: int = 0
        self._pending_chunks: deque[tuple[bytes, float]] = deque()
        self._max_pending_chunks: int = int(Config.live_runtime_profile(output_mode=self._output_mode, preset=self._preset_id).get("max_pending_chunks", 4))
        self._pending_chunks_lock = threading.Lock()
        self._ready_updates: deque[LiveUpdate] = deque()
        self._ready_updates_lock = threading.Lock()
        self._inference_wakeup = threading.Event()
        self._inference_stop = threading.Event()
        self._inference_thread: threading.Thread | None = None
        self._inference_error: Exception | None = None

    # ----- External controls -----

    def cancel(self) -> None:
        _LOG.debug("Live worker cancel requested. worker=live_transcription")
        self._cancel.cancel()

    def stop(self) -> None:
        _LOG.debug("Live worker stop requested. worker=live_transcription")
        self._stop.set()

    def pause(self) -> None:
        _LOG.debug("Live worker pause requested. worker=live_transcription")
        self._pause.set()

    def resume(self) -> None:
        _LOG.debug("Live worker resume requested. worker=live_transcription")
        self._pause.clear()

    # ----- Internals -----

    def _is_cancelled(self) -> bool:
        if self._cancel.is_cancelled:
            return True
        try:
            th = QtCore.QThread.currentThread()
            if th is not None and th.isInterruptionRequested():
                return True
        except (AttributeError, RuntimeError):
            pass
        return False

    def _set_status(self, key: str) -> None:
        key = str(key or "").strip()
        if not key or key == self._status_key:
            return
        self._status_key = key
        self.status.emit(key)

    def _emit_text_update(self, u: LiveUpdate, *, force: bool = False) -> None:
        source_text = str(u.display_source_text or "")
        if force or source_text != self._last_emitted_source:
            self._last_emitted_source = source_text
            self.source_text.emit(source_text)

        target_text = str(u.display_target_text or "")
        if force or target_text != self._last_emitted_target:
            self._last_emitted_target = target_text
            self.target_text.emit(target_text)

        archive_source_text = str(u.archive_source_text or "")
        if force or archive_source_text != self._last_emitted_archive_source:
            self._last_emitted_archive_source = archive_source_text
            self.archive_source_text.emit(archive_source_text)

        archive_target_text = str(u.archive_target_text or "")
        if force or archive_target_text != self._last_emitted_archive_target:
            self._last_emitted_archive_target = archive_target_text
            self.archive_target_text.emit(archive_target_text)

    def _emit_updates(self, updates: list[LiveUpdate], *, force: bool = False) -> None:
        if not updates:
            return

        u = updates[-1]

        detected_language = str(u.detected_language or "")
        if detected_language and (force or detected_language != self._last_emitted_language):
            self._last_emitted_language = detected_language
            _LOG.debug("Live worker detected language updated. lang=%s", detected_language)
            self.detected_language.emit(detected_language)

        self._emit_text_update(u, force=force)

    @staticmethod
    def _level_from_audio(audio: np.ndarray) -> float:
        if audio.size == 0:
            return 0.0

        audio = audio - float(audio.mean())

        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        if rms <= 0.0:
            return 0.0

        db = 20.0 * float(np.log10(rms))
        db_min = -60.0
        if db <= db_min:
            return 0.0
        if db >= 0.0:
            return 1.0
        return (db - db_min) / (0.0 - db_min)

    def _chunk_level(self, chunk: bytes) -> float:
        if not chunk:
            return 0.0
        audio = pcm16le_bytes_to_float32(chunk)
        if audio.size == 0:
            return 0.0
        return self._level_from_audio(audio)

    @staticmethod
    def _meter_from_level(level: float) -> list[float]:
        bars = 18
        lvl = max(0.0, min(1.0, float(level or 0.0)))
        filled = lvl * float(bars)
        full = int(filled)
        frac = float(filled - full)

        out = [0.0] * bars
        for idx in range(bars):
            if idx < full:
                out[idx] = 1.0
            elif idx == full and frac > 0.0:
                out[idx] = min(1.0, frac)
        return out

    @staticmethod
    def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        if audio.size == 0:
            return np.zeros((0,), dtype=np.float32)
        src = int(src_sr)
        dst = int(dst_sr)
        if src <= 0 or dst <= 0 or src == dst:
            return audio.astype(np.float32, copy=False)
        n = int(round(float(audio.size) * float(dst) / float(src)))
        if n <= 0:
            return np.zeros((0,), dtype=np.float32)
        x_old = np.arange(int(audio.size), dtype=np.float32)
        x_new = np.linspace(0.0, float(audio.size - 1), n, dtype=np.float32)
        return np.interp(x_new, x_old, audio.astype(np.float32, copy=False)).astype(np.float32)

    @staticmethod
    def _normalize_pcm16(chunk: bytes, fmt: Any, qt_multimedia: Any) -> bytes:
        if not chunk:
            return b""

        try:
            ch = int(fmt.channelCount() or 1)
        except Exception:
            ch = 1
        if ch <= 0:
            ch = 1

        try:
            sr = int(fmt.sampleRate() or 16000)
        except Exception:
            sr = 16000
        if sr <= 0:
            sr = 16000

        try:
            sample_type = fmt.sampleType()
        except Exception:
            sample_type = qt_multimedia.QAudioFormat.SignedInt

        try:
            byte_order = fmt.byteOrder()
        except Exception:
            byte_order = qt_multimedia.QAudioFormat.LittleEndian

        frame_bytes = int(ch) * 2
        if frame_bytes <= 0:
            frame_bytes = 2
        if len(chunk) % frame_bytes != 0:
            chunk = chunk[: len(chunk) - (len(chunk) % frame_bytes)]
        if not chunk:
            return b""

        if (
            ch == 1
            and sr == 16000
            and sample_type == qt_multimedia.QAudioFormat.SignedInt
            and byte_order == qt_multimedia.QAudioFormat.LittleEndian
        ):
            return chunk

        endian = "<" if byte_order == qt_multimedia.QAudioFormat.LittleEndian else ">"

        if sample_type == qt_multimedia.QAudioFormat.UnSignedInt:
            a = np.frombuffer(chunk, dtype=np.dtype(endian + "u2")).astype(np.int32)
            a = (a - 32768).astype(np.int16)
        else:
            a = np.frombuffer(chunk, dtype=np.dtype(endian + "i2")).astype(np.int16)

        audio = a.astype(np.float32) / 32768.0
        if ch > 1:
            audio = audio.reshape(-1, ch).mean(axis=1)

        if sr != 16000:
            audio = LiveTranscriptionWorker._resample(audio, sr, 16000)

        if audio.size == 0:
            return b""

        audio = np.clip(audio, -1.0, 1.0)
        out = (audio * 32767.0).astype(np.int16)
        return out.astype("<i2", copy=False).tobytes()

    def _audio_error_detail(self, err: Any) -> str:
        qt_multimedia = self._qtmm
        if qt_multimedia is None:
            return str(err)
        try:
            error_names = {
                qt_multimedia.QAudio.NoError: "no_error",
                qt_multimedia.QAudio.OpenError: "open_error",
                qt_multimedia.QAudio.IOError: "io_error",
                qt_multimedia.QAudio.UnderrunError: "underrun_error",
                qt_multimedia.QAudio.FatalError: "fatal_error",
            }
            name = error_names.get(err, "unknown")
            return f"audio_error:{name}"
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return str(err)

    @staticmethod
    def _validate_audio_format(*, fmt: Any, qt_multimedia: Any) -> None:
        try:
            if int(fmt.sampleSize() or 0) != 16:
                raise LiveError("error.live.microphone_format_unsupported")
            sample_type = fmt.sampleType()
            if sample_type not in (qt_multimedia.QAudioFormat.SignedInt, qt_multimedia.QAudioFormat.UnSignedInt):
                raise LiveError("error.live.microphone_format_unsupported")
            codec = str(fmt.codec() or "").strip().lower()
            if codec and codec != "audio/pcm":
                raise LiveError("error.live.microphone_format_unsupported")
        except LiveError:
            raise
        except Exception as exc:
            raise LiveError("error.live.microphone_format_unsupported") from exc

    def _resolve_audio_runtime(self) -> tuple[Any, Any, Any]:
        qt_multimedia, dev = resolve_input_device(self._device_name)
        fmt = make_pcm16_mono_format()

        if dev is None:
            try:
                dev = qt_multimedia.QAudioDeviceInfo.defaultInputDevice()
            except (AttributeError, RuntimeError):
                dev = None
            _LOG.debug("Live worker using default input device. requested_device=%s", self._device_name)

        if dev is not None:
            _, fmt = ensure_supported_format(dev, fmt)

        self._validate_audio_format(fmt=fmt, qt_multimedia=qt_multimedia)
        return qt_multimedia, dev, fmt

    @staticmethod
    def _create_audio_input(*, qt_multimedia: Any, dev: Any, fmt: Any) -> Any:
        if dev is None:
            return qt_multimedia.QAudioInput(fmt)
        return qt_multimedia.QAudioInput(dev, fmt)

    def _start_audio_input(self) -> None:
        if self._qtmm is None or self._fmt is None:
            raise LiveError("error.live.audio_input_start_failed")

        self._audio_in = self._create_audio_input(
            qt_multimedia=self._qtmm,
            dev=self._device_info,
            fmt=self._fmt,
        )

        try:
            self._audio_in.stateChanged.connect(self._on_audio_state_changed)
        except Exception:
            pass

        self._io = self._audio_in.start()
        if self._io is None:
            raise LiveError("error.live.audio_input_start_failed")

        try:
            self._io.readyRead.connect(self._on_ready_read)
        except Exception:
            pass

    def _create_live_session(self) -> LiveSession:
        return LiveSession(
            pipe=self._pipe,
            source_language=self._src_lang,
            target_language=self._tgt_lang,
            translate_enabled=self._translate_enabled,
            cancel_check=self._is_cancelled,
            preset_id=self._preset_id,
            output_mode=self._output_mode,
        )

    def _start_tick_timer(self) -> None:
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        _LOG.debug("Live worker timer started. interval_ms=%s", self._timer.interval())

    def _read_available_audio_chunk(self) -> bytes:
        if self._io is None or self._qtmm is None or self._fmt is None:
            return b""
        try:
            chunk = bytes(self._io.readAll())
        except Exception:
            return b""
        if not chunk:
            return b""
        return self._normalize_pcm16(chunk, self._fmt, self._qtmm)

    def _emit_spectrum_if_due(self, *, level: float) -> None:
        meter = self._meter_from_level(level)
        now_s = time.monotonic()
        if (now_s - self._last_spectrum_emit_s) < self._spectrum_emit_interval_s:
            return
        self._last_spectrum_emit_s = now_s
        try:
            self.spectrum.emit(meter)
        except Exception:
            pass

    def _update_audio_capture_state(self) -> None:
        if self._audio_in is None or self._qtmm is None:
            return
        if self._stop.is_set():
            return
        if self._pause.is_set():
            if self._audio_in.state() == self._qtmm.QAudio.ActiveState:
                try:
                    self._audio_in.suspend()
                except Exception:
                    pass
            self._set_status("status.paused")
            return

        if self._audio_in.state() == self._qtmm.QAudio.SuspendedState:
            try:
                self._audio_in.resume()
            except Exception:
                pass
        self._set_status("status.listening")

    def _finalize_worker(self) -> bool:
        if self._run_finished:
            return False
        self._run_finished = True
        self._cleanup()
        self.finished.emit()
        return True

    def _finish_requested_run(self) -> bool:
        if not self._flush_live_session():
            return False
        return self._finalize_worker()

    def _flush_pending_audio_input(self) -> None:
        tail_chunk = self._read_available_audio_chunk()
        if not tail_chunk:
            return
        self._queue_chunk(tail_chunk, self._chunk_level(tail_chunk))

    def _flush_live_session(self) -> bool:
        if self._session is None:
            return True
        try:
            self._flush_pending_audio_input()
            self._stop_inference_thread()
            self._emit_updates(self._drain_ready_updates(), force=True)
            if self._inference_error is not None:
                raise self._inference_error
            return True
        except Exception as ex:
            with self._pending_chunks_lock:
                self._pending_chunks.clear()
            self._fail(ex)
            return False

    def _cleanup(self) -> None:
        try:
            if self._timer is not None:
                self._timer.stop()
                self._timer.deleteLater()
        except Exception:
            pass
        self._timer = None
        self._stop_inference_thread()
        with self._pending_chunks_lock:
            self._pending_chunks.clear()
        with self._ready_updates_lock:
            self._ready_updates.clear()

        try:
            if self._io is not None:
                try:
                    self._io.readyRead.disconnect(self._on_ready_read)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if self._audio_in is not None:
                try:
                    self._audio_in.stateChanged.disconnect(self._on_audio_state_changed)
                except Exception:
                    pass
                self._audio_in.stop()
        except Exception:
            pass

        self._io = None
        self._audio_in = None
        self._device_info = None
        self._fmt = None
        self._qtmm = None
        self._session = None

    def _fail(self, err: Any) -> None:
        if self._run_finished:
            return

        err_key = getattr(err, "key", None)
        err_params = getattr(err, "params", None)

        if err_key:
            _LOG.error("Live transcription failed. key=%s", err_key)
            self.error.emit(str(err_key), dict(err_params or {}))
        else:
            detail = str(err)
            _LOG.error("Live transcription failed. detail=%s", detail)
            self.error.emit("error.live.failed", {"detail": detail})

        self._set_status("status.error")
        self._finalize_worker()

    # ----- Qt slots -----

    def _on_audio_state_changed(self, state: int) -> None:
        if self._audio_in is None or self._qtmm is None:
            return
        if self._is_cancelled():
            return
        try:
            if state == self._qtmm.QAudio.StoppedState:
                err = self._audio_in.error()
                if err != self._qtmm.QAudio.NoError:
                    self._fail(self._audio_error_detail(err))
        except Exception as ex:
            self._fail(ex)

    def _start_inference_thread(self) -> None:
        self._inference_stop.clear()
        self._inference_error = None
        self._inference_thread = threading.Thread(
            target=self._inference_loop,
            name="live_transcription_inference",
            daemon=True,
        )
        self._inference_thread.start()

    @staticmethod
    def _merge_pending_items(first: tuple[bytes, float], second: tuple[bytes, float]) -> tuple[bytes, float]:
        first_chunk, first_level = first
        second_chunk, second_level = second
        merged_chunk = bytes(first_chunk or b"") + bytes(second_chunk or b"")
        merged_level = max(float(first_level or 0.0), float(second_level or 0.0))
        return merged_chunk, merged_level

    def _compact_pending_backlog_locked(self) -> int:
        compactions = 0
        limit = max(1, int(self._max_pending_chunks))
        while len(self._pending_chunks) > limit and len(self._pending_chunks) >= 2:
            first = self._pending_chunks.popleft()
            second = self._pending_chunks.popleft()
            self._pending_chunks.appendleft(self._merge_pending_items(first, second))
            compactions += 1
        self._backlog_compactions += compactions
        return compactions

    def _queue_chunk(self, chunk: bytes, level: float) -> None:
        with self._pending_chunks_lock:
            self._pending_chunks.append((chunk, level))
            compactions = self._compact_pending_backlog_locked()
            backlog = len(self._pending_chunks)
        self._inference_wakeup.set()

        if _LOG.isEnabledFor(logging.DEBUG):
            now_s = time.monotonic()
            should_log = bool(compactions) or backlog >= self._max_pending_chunks
            if should_log and (now_s - self._last_backlog_debug_s) >= self._backlog_debug_interval_s:
                self._last_backlog_debug_s = now_s
                _LOG.debug(
                    "Live audio backlog updated. worker=live_transcription backlog=%s compacted=%s total_compactions=%s",
                    backlog,
                    int(compactions),
                    int(self._backlog_compactions),
                )

    def _pop_chunk(self) -> tuple[bytes, float] | None:
        with self._pending_chunks_lock:
            if not self._pending_chunks:
                self._inference_wakeup.clear()
                return None
            chunk = self._pending_chunks.popleft()
            if not self._pending_chunks:
                self._inference_wakeup.clear()
            return chunk

    def _push_ready_updates(self, updates: list[LiveUpdate]) -> None:
        if not updates:
            return
        with self._ready_updates_lock:
            self._ready_updates.extend(updates)

    def _drain_ready_updates(self) -> list[LiveUpdate]:
        with self._ready_updates_lock:
            if not self._ready_updates:
                return []
            updates = list(self._ready_updates)
            self._ready_updates.clear()
            return updates

    def _inference_loop(self) -> None:
        try:
            while True:
                item = self._pop_chunk()
                if item is None:
                    if self._inference_stop.is_set():
                        break
                    self._inference_wakeup.wait(0.05)
                    continue

                if self._session is None:
                    continue

                chunk, level = item
                updates = self._session.push_pcm16(
                    chunk,
                    level=level,
                    ignore_cancel=self._inference_stop.is_set(),
                )
                self._push_ready_updates(updates)
        except Exception as ex:
            self._inference_error = ex
        finally:
            try:
                if self._session is not None and self._inference_error is None:
                    self._push_ready_updates(self._session.finalize(ignore_cancel=True))
            except Exception as ex:
                self._inference_error = ex

    def _stop_inference_thread(self) -> None:
        self._inference_stop.set()
        self._inference_wakeup.set()
        th = self._inference_thread
        if th is not None and th.is_alive():
            th.join()
        self._inference_thread = None

    @QtCore.pyqtSlot()
    def _on_ready_read(self) -> None:
        if self._audio_in is None:
            return

        chunk = self._read_available_audio_chunk()
        if not chunk:
            return

        level = self._chunk_level(chunk)
        self._emit_spectrum_if_due(level=level)

        if self._pause.is_set() or self._stop.is_set() or self._is_cancelled() or self._session is None:
            return

        self._queue_chunk(chunk, level)

    @QtCore.pyqtSlot()
    def _tick(self) -> None:
        if self._audio_in is None or self._qtmm is None:
            return

        if self._inference_error is not None:
            self._fail(self._inference_error)
            return

        ready_updates = self._drain_ready_updates()
        if ready_updates:
            self._emit_updates(ready_updates)

        if self._stop.is_set() or self._is_cancelled():
            self._finish_requested_run()
            return

        try:
            self._update_audio_capture_state()
        except Exception:
            pass

    # ----- Run -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self._set_status("status.initializing")
        self._stop.clear()

        try:
            _LOG.debug(
                "Live worker starting. worker=live_transcription device=%s source_language=%s target_language=%s translate_enabled=%s preset=%s output_mode=%s",
                self._device_name,
                self._src_lang,
                self._tgt_lang,
                bool(self._translate_enabled),
                self._preset_id,
                self._output_mode,
            )

            self._qtmm, self._device_info, self._fmt = self._resolve_audio_runtime()
            _LOG.debug(
                "Live worker audio format resolved. sample_rate=%s channels=%s sample_size=%s codec=%s",
                int(self._fmt.sampleRate() or 0),
                int(self._fmt.channelCount() or 0),
                int(self._fmt.sampleSize() or 0),
                str(self._fmt.codec() or ""),
            )

            self._start_audio_input()
            self._session = self._create_live_session()
            self._start_inference_thread()
            self._set_status("status.listening")
            _LOG.debug("Live worker session initialized. worker=live_transcription")

            self._start_tick_timer()
        except Exception as ex:
            _LOG.exception("Live transcription failed.")
            self._fail(ex)
