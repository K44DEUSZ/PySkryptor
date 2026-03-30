# app/model/transcription/live.py
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from app.model.core.config.config import AppConfig
from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.errors import AppError
from app.model.core.domain.results import LiveUpdate
from app.model.transcription.chunking import pcm16le_bytes_to_float32, seconds_to_frames
from app.model.transcription.service import TranscriptionError
from app.model.transcription.whisper import (
    audio_rms_level,
    build_whisper_generate_kwargs,
    can_detect_language_from_audio,
    classify_audio_signal,
    detect_language_from_pipe_runtime,
    extract_detected_language_from_result,
    filter_asr_text,
    should_use_prompt,
    whisper_prompt_ids_from_text,
)
from app.model.transcription.writer import TextPostprocessor
from app.model.translation.service import TranslationService

_LOG = logging.getLogger(__name__)

LiveCancelCheckFn = Callable[[], bool]

_MERGE_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class LiveTranscriptionService:
    """Stateful live transcription session fed with PCM16 audio bytes."""

    OUTPUT_MODE_STREAM = RuntimeProfiles.LIVE_OUTPUT_MODE_STREAM
    OUTPUT_MODE_CUMULATIVE = RuntimeProfiles.LIVE_OUTPUT_MODE_CUMULATIVE

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
        profile: str = RuntimeProfiles.LIVE_DEFAULT_PROFILE,
        output_mode: str = OUTPUT_MODE_CUMULATIVE,
        runtime_profile: dict[str, Any] | None = None,
    ) -> None:
        self._pipe = pipe
        self._cancel_check = cancel_check

        self._src_lang = LanguagePolicy.normalize_policy_value(source_language)
        if LanguagePolicy.is_auto(self._src_lang):
            self._src_lang = ""
        self._source_language_forced = bool(self._src_lang)

        self._tgt_lang = LanguagePolicy.normalize_policy_value(target_language)
        self._translate = bool(translate_enabled) and bool(self._tgt_lang) and not LanguagePolicy.is_auto(
            self._tgt_lang
        )

        model_cfg = AppConfig.transcription_model_raw_cfg_dict()
        self._ignore_warning = bool(model_cfg.get("ignore_warning", False))

        self._sr = AppConfig.ASR_SAMPLE_RATE
        self._output_mode = RuntimeProfiles.normalize_live_output_mode(output_mode)
        self._stream_only = self._output_mode == self.OUTPUT_MODE_STREAM
        resolved_profile = RuntimeProfiles.normalize_live_profile(profile)
        if runtime_profile is None:
            runtime_profile = RuntimeProfiles.resolve_live_runtime(
                output_mode=self._output_mode,
                profile=resolved_profile,
            )
        self._profile = dict(runtime_profile)

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

        self._commit_silence_s = float(self._profile.get("commit_silence_s", 0.62))
        self._stream_clear_after_s = float(self._profile.get("stream_clear_after_s", 1.15))
        self._stream_show_previous_caption = bool(self._profile.get("stream_show_previous_caption", False))
        self._stream_replace_prefix_ratio = float(self._profile.get("stream_replace_prefix_ratio", 0.62))
        self._stream_commit_min_words = int(self._profile.get("stream_commit_min_words", 6) or 6)
        self._cumulative_merge_overlap_min = int(self._profile.get("cumulative_merge_overlap_min", 2) or 2)
        self._stream_translation_min_chars = int(self._profile.get("stream_translation_min_chars", 18) or 18)
        self._cumulative_translation_min_chars = int(
            self._profile.get("cumulative_translation_min_chars", 20) or 20
        )

        self._post = TextPostprocessor()
        self._translator = TranslationService()

        self._stable_language_min_hits = int(self._profile.get("stable_language_min_hits", 2) or 2)
        self._language_hits: dict[str, int] = {}

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
        return str(text or "").rstrip().endswith((".", "!", "?", ";", ":", "...", "\N{HORIZONTAL ELLIPSIS}"))

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

        if len(current_tokens) <= len(previous_tokens) and previous_tokens[-len(current_tokens) :] == current_tokens:
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
        src_lang = self._src_lang if self._source_language_forced else (self._detected_lang or self._src_lang)
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
                cancel_check=self._cancel_check,
            )
        except AppError:
            return ""
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

    def _transcribe_audio(self, audio: Any, *, signal_kind: str) -> tuple[str, bool]:
        payload = {"raw": audio, "sampling_rate": self._sr}

        prompt_source = self._draft_source or self._archive_source or self._previous_source or self._current_source
        prompt_ids = None
        if prompt_source and should_use_prompt(signal_kind=signal_kind, profile=self._profile):
            prompt_ids = whisper_prompt_ids_from_text(pipe=self._pipe, text=prompt_source)
        generate_kwargs = build_whisper_generate_kwargs(
            profile=self._profile,
            source_language=self._src_lang,
            prompt_ids=prompt_ids,
            signal_kind=signal_kind,
        )

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
                fallback_kwargs = dict(generate_kwargs)
                fallback_kwargs.pop("prompt_ids", None)
                try:
                    result = self._pipe(
                        payload,
                        return_language=True,
                        return_timestamps=False,
                        generate_kwargs=fallback_kwargs,
                    )
                except TypeError:
                    candidate_kwargs = {
                        k: v
                        for k, v in fallback_kwargs.items()
                        if k
                        not in (
                            "no_speech_threshold",
                            "logprob_threshold",
                            "compression_ratio_threshold",
                            "temperature",
                        )
                    }
                    try:
                        result = self._pipe(
                            payload,
                            return_language=True,
                            return_timestamps=False,
                            generate_kwargs=candidate_kwargs,
                        )
                    except TypeError:
                        result = self._pipe(
                            payload,
                            return_language=True,
                            return_timestamps=False,
                        )
        except Exception as exc:
            _LOG.exception("ASR pipeline call failed.")
            raise TranscriptionError("error.transcription.asr_failed") from exc

        if not isinstance(result, dict):
            result = {"text": str(result)}

        language_changed = False
        detected = "" if self._source_language_forced else extract_detected_language_from_result(result)
        if (
            not detected
            and not self._source_language_forced
            and (self._translate or not self._src_lang)
            and can_detect_language_from_audio(
                audio,
                sr=self._sr,
                signal_kind=signal_kind,
                profile=self._profile,
            )
        ):
            detected = detect_language_from_pipe_runtime(
                pipe=self._pipe,
                audio=audio,
                sr=self._sr,
            )
            if detected:
                result["language"] = detected

        if detected and not self._source_language_forced:
            normalized_detected = str(detected or "").strip().lower()
            if normalized_detected and (
                signal_kind == self.SIGNAL_SOLID
                or can_detect_language_from_audio(audio, sr=self._sr, signal_kind=signal_kind, profile=self._profile)
            ):
                self._language_hits[normalized_detected] = int(self._language_hits.get(normalized_detected, 0)) + 1
                if (
                    self._language_hits[normalized_detected] >= max(1, self._stable_language_min_hits)
                    and normalized_detected != self._detected_lang
                ):
                    self._detected_lang = normalized_detected
                    language_changed = True

        return str(result.get("text") or ""), language_changed

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
        had_stream = bool(
            self._previous_source or self._current_source or self._stream_source or self._stream_target
        )
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

        changed = (
            source_text != self._previous_source
            or target_text != self._previous_target
            or bool(self._current_source or self._current_target)
        )
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
        merged_target = (
            self._merge_text(
                self._archive_target,
                target_text,
                min_overlap_words=1,
            )
            if target_text
            else self._archive_target
        )

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
            if self._previous_source and self._is_revision(
                self._previous_source,
                source_text,
                prefix_ratio=self._stream_replace_prefix_ratio,
            ):
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
        if merged_current != self._current_source and merged_current != (
            str(self._current_source or "").strip() + " " + source_text
        ).strip():
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

    def _run_pipeline_on_pcm16(
        self,
        data: bytes,
        *,
        ignore_cancel: bool = False,
        from_tail: bool = False,
    ) -> tuple[bool, bool]:
        if not data or ((not ignore_cancel) and self._cancel_check()):
            return False, False

        audio = pcm16le_bytes_to_float32(data)
        if audio.size == 0:
            return False, False

        signal_kind = classify_audio_signal(audio, sr=self._sr, profile=self._profile)
        if signal_kind == self.SIGNAL_NONE:
            return False, False
        if signal_kind == self.SIGNAL_WEAK and not self._has_active_text():
            return False, False

        current_source, language_changed = self._transcribe_audio(audio, signal_kind=signal_kind)
        current_source = filter_asr_text(
            current_source,
            clean_fn=self._post.clean,
            signal_kind=signal_kind,
            profile=self._profile,
            reference_texts=self._active_reference_texts(),
            from_tail=from_tail,
        )
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

    def _append_update_if_needed(self, updates: list[LiveUpdate], *flags: bool) -> None:
        if any(bool(flag) for flag in flags):
            updates.append(self._build_update())

    def _is_silence_chunk(self, data: bytes, *, level: float | None) -> bool:
        level_f = max(0.0, float(level or 0.0))
        if level_f > self._silence_level_threshold:
            return False

        audio = pcm16le_bytes_to_float32(data)
        return audio_rms_level(audio) <= self._silence_audio_rms_min

    def _handle_silence_chunk(
        self,
        data: bytes,
        *,
        chunk_seconds: float,
        ignore_cancel: bool,
    ) -> list[LiveUpdate]:
        updates: list[LiveUpdate] = []
        self._buf.extend(data)
        self._silent_run_s += chunk_seconds

        if self._silent_run_s >= self._commit_silence_s and not self._silence_gap_flushed:
            updates.extend(self._flush_on_silence(ignore_cancel=ignore_cancel))
            self._silence_gap_flushed = True

        if self._stream_only and self._silent_run_s >= self._stream_clear_after_s and not self._stream_gap_cleared:
            self._append_update_if_needed(updates, self._clear_stream_text())
            self._stream_gap_cleared = True

        if self._silence_gap_flushed:
            self._trim_buffer_to_seconds(self._silence_tail_keep_s)

        return updates

    def _drain_buffered_chunks(self, *, ignore_cancel: bool) -> list[LiveUpdate]:
        updates: list[LiveUpdate] = []
        bytes_per_frame = 2
        while len(self._buf) >= self._chunk_f * bytes_per_frame:
            if (not ignore_cancel) and self._cancel_check():
                break

            chunk = bytes(self._buf[: self._chunk_f * bytes_per_frame])
            del self._buf[: self._step_f * bytes_per_frame]

            committed, partial_changed = self._run_pipeline_on_pcm16(
                chunk,
                ignore_cancel=ignore_cancel,
            )
            self._append_update_if_needed(updates, committed, partial_changed)

        return updates

    def finalize(self, *, ignore_cancel: bool = False) -> list[LiveUpdate]:
        out: list[LiveUpdate] = []

        if self._buf and (ignore_cancel or not self._cancel_check()) and (not self._stream_only):
            committed_from_tail, changed_from_tail = self._flush_tail_buffer(
                ignore_cancel=ignore_cancel,
                min_seconds=0.0,
            )
            self._append_update_if_needed(out, committed_from_tail, changed_from_tail)

        if self._commit_draft():
            out.append(self._build_update())
        elif out or self._detected_lang:
            out.append(self._build_update())

        self._silent_run_s = 0.0
        self._silence_gap_flushed = False
        self._stream_gap_cleared = False
        return out

    def push_pcm16(self, data: bytes, *, level: float | None = None, ignore_cancel: bool = False) -> list[LiveUpdate]:
        if not data or ((not ignore_cancel) and self._cancel_check()):
            return []

        chunk_seconds = float(len(data)) / float(max(1, self._sr * 2))
        if self._is_silence_chunk(data, level=level):
            return self._handle_silence_chunk(
                data,
                chunk_seconds=chunk_seconds,
                ignore_cancel=ignore_cancel,
            )

        out: list[LiveUpdate] = []

        if self._silent_run_s >= self._commit_silence_s and not self._silence_gap_flushed:
            out.extend(self._flush_on_silence(ignore_cancel=ignore_cancel))

        self._buf.extend(data)
        self._silent_run_s = 0.0
        self._silence_gap_flushed = False
        self._stream_gap_cleared = False

        out.extend(self._drain_buffered_chunks(ignore_cancel=ignore_cancel))
        return out
