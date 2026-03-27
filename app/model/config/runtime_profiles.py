# app/model/config/runtime_profiles.py
from __future__ import annotations

from typing import Any


class RuntimeProfiles:
    """Centralized access to transcription, translation, and live runtime profiles."""

    LIVE_OUTPUT_MODE_STREAM = "stream"
    LIVE_OUTPUT_MODE_CUMULATIVE = "cumulative"
    LIVE_OUTPUT_MODES: tuple[str, ...] = (LIVE_OUTPUT_MODE_STREAM, LIVE_OUTPUT_MODE_CUMULATIVE)

    LIVE_UI_MODE_TRANSCRIBE = "transcribe"
    LIVE_UI_MODE_TRANSCRIBE_TRANSLATE = "transcribe_translate"
    LIVE_UI_MODES: tuple[str, ...] = (LIVE_UI_MODE_TRANSCRIBE, LIVE_UI_MODE_TRANSCRIBE_TRANSLATE)
    LIVE_UI_DEFAULT_MODE = LIVE_UI_MODE_TRANSCRIBE

    TRANSCRIPTION_PROFILE_FAST = "fast"
    TRANSCRIPTION_PROFILE_BALANCED = "balanced"
    TRANSCRIPTION_PROFILE_ACCURATE = "accurate"
    TRANSCRIPTION_PROFILE_GUARDED = "guarded"
    TRANSCRIPTION_PROFILE_CUSTOM = "custom"
    TRANSCRIPTION_DEFAULT_PROFILE = TRANSCRIPTION_PROFILE_BALANCED
    TRANSCRIPTION_PROFILE_IDS: tuple[str, ...] = (
        TRANSCRIPTION_PROFILE_FAST,
        TRANSCRIPTION_PROFILE_BALANCED,
        TRANSCRIPTION_PROFILE_ACCURATE,
        TRANSCRIPTION_PROFILE_GUARDED,
        TRANSCRIPTION_PROFILE_CUSTOM,
    )

    TRANSLATION_PROFILE_FAST = "fast"
    TRANSLATION_PROFILE_BALANCED = "balanced"
    TRANSLATION_PROFILE_ACCURATE = "accurate"
    TRANSLATION_PROFILE_CUSTOM = "custom"
    TRANSLATION_DEFAULT_PROFILE = TRANSLATION_PROFILE_BALANCED
    TRANSLATION_PROFILE_IDS: tuple[str, ...] = (
        TRANSLATION_PROFILE_FAST,
        TRANSLATION_PROFILE_BALANCED,
        TRANSLATION_PROFILE_ACCURATE,
        TRANSLATION_PROFILE_CUSTOM,
    )

    LIVE_PROFILE_LOW_LATENCY = "low_latency"
    LIVE_PROFILE_BALANCED = "balanced"
    LIVE_PROFILE_HIGH_CONTEXT = "high_context"
    LIVE_DEFAULT_PROFILE = LIVE_PROFILE_BALANCED
    LIVE_PROFILE_IDS: tuple[str, ...] = (
        LIVE_PROFILE_LOW_LATENCY,
        LIVE_PROFILE_BALANCED,
        LIVE_PROFILE_HIGH_CONTEXT,
    )

    CONTEXT_POLICY_OFF = "off"
    CONTEXT_POLICY_AUTO = "auto"
    CONTEXT_POLICY_AGGRESSIVE = "aggressive"
    CONTEXT_POLICIES: tuple[str, ...] = (
        CONTEXT_POLICY_OFF,
        CONTEXT_POLICY_AUTO,
        CONTEXT_POLICY_AGGRESSIVE,
    )

    SILENCE_GUARD_OFF = "off"
    SILENCE_GUARD_NORMAL = "normal"
    SILENCE_GUARD_STRICT = "strict"
    SILENCE_GUARDS: tuple[str, ...] = (
        SILENCE_GUARD_OFF,
        SILENCE_GUARD_NORMAL,
        SILENCE_GUARD_STRICT,
    )

    LANGUAGE_STABILITY_FAST = "fast"
    LANGUAGE_STABILITY_BALANCED = "balanced"
    LANGUAGE_STABILITY_STRICT = "strict"
    LANGUAGE_STABILITIES: tuple[str, ...] = (
        LANGUAGE_STABILITY_FAST,
        LANGUAGE_STABILITY_BALANCED,
        LANGUAGE_STABILITY_STRICT,
    )

    TRANSLATION_STYLE_LITERAL = "literal"
    TRANSLATION_STYLE_BALANCED = "balanced"
    TRANSLATION_STYLE_FLUENT = "fluent"
    TRANSLATION_STYLES: tuple[str, ...] = (
        TRANSLATION_STYLE_LITERAL,
        TRANSLATION_STYLE_BALANCED,
        TRANSLATION_STYLE_FLUENT,
    )

    _TRANSCRIPTION_BASE_AUDIO: dict[str, Any] = {
        "weak_rms_threshold": 0.0115,
        "weak_activity_floor": 0.0055,
        "weak_active_ratio_threshold": 0.012,
        "weak_active_ms_threshold": 55.0,
        "solid_rms_threshold": 0.0145,
        "solid_activity_floor": 0.0065,
        "solid_active_ratio_threshold": 0.022,
        "solid_active_ms_threshold": 85.0,
        "language_detect_rms_threshold": 0.03,
        "language_detect_activity_floor": 0.009,
        "language_detect_active_ratio_threshold": 0.07,
        "language_detect_active_ms_threshold": 160.0,
        "artifact_min_chars": 3,
        "artifact_min_words": 2,
        "artifact_tail_max_words": 2,
        "artifact_tail_max_chars": 14,
        "stable_language_min_hits": 2,
        "allow_weak_language_detection": False,
        "prompt_on_weak_signal": False,
        "whisper_no_speech_threshold": 0.45,
        "whisper_logprob_threshold": -1.0,
        "whisper_compression_ratio_threshold": 2.2,
        "whisper_temperatures": (0.0, 0.2, 0.4, 0.6),
    }

    _LIVE_AUDIO_SIGNAL_PROFILE: dict[str, Any] = {
        "silence_level_threshold": 0.055,
        "silence_audio_rms_min": 0.007,
        "silence_tail_keep_s": 0.24,
        "tail_flush_min_s": 0.20,
        **_TRANSCRIPTION_BASE_AUDIO,
    }

    _TRANSCRIPTION_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
        TRANSCRIPTION_PROFILE_FAST: {
            "chunk_length_s": 30,
            "stride_length_s": 3,
            "context_policy": CONTEXT_POLICY_AUTO,
            "silence_guard": SILENCE_GUARD_OFF,
            "language_stability": LANGUAGE_STABILITY_FAST,
        },
        TRANSCRIPTION_PROFILE_BALANCED: {
            "chunk_length_s": 45,
            "stride_length_s": 5,
            "context_policy": CONTEXT_POLICY_AUTO,
            "silence_guard": SILENCE_GUARD_NORMAL,
            "language_stability": LANGUAGE_STABILITY_BALANCED,
        },
        TRANSCRIPTION_PROFILE_ACCURATE: {
            "chunk_length_s": 60,
            "stride_length_s": 8,
            "context_policy": CONTEXT_POLICY_AGGRESSIVE,
            "silence_guard": SILENCE_GUARD_NORMAL,
            "language_stability": LANGUAGE_STABILITY_STRICT,
        },
        TRANSCRIPTION_PROFILE_GUARDED: {
            "chunk_length_s": 45,
            "stride_length_s": 5,
            "context_policy": CONTEXT_POLICY_AUTO,
            "silence_guard": SILENCE_GUARD_STRICT,
            "language_stability": LANGUAGE_STABILITY_STRICT,
        },
    }

    _TRANSLATION_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
        TRANSLATION_PROFILE_FAST: {
            "style": TRANSLATION_STYLE_LITERAL,
            "num_beams": 1,
            "no_repeat_ngram_size": 0,
        },
        TRANSLATION_PROFILE_BALANCED: {
            "style": TRANSLATION_STYLE_BALANCED,
            "num_beams": 3,
            "no_repeat_ngram_size": 3,
        },
        TRANSLATION_PROFILE_ACCURATE: {
            "style": TRANSLATION_STYLE_FLUENT,
            "num_beams": 5,
            "no_repeat_ngram_size": 3,
        },
    }

    _LIVE_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
        LIVE_PROFILE_LOW_LATENCY: {
            "chunk_length_s": 3,
            "stride_length_s": 2,
            "stream_commit_silence_s": 0.52,
            "cumulative_commit_silence_s": 0.58,
            "stream_clear_after_s": 1.05,
            "stream_replace_prefix_ratio": 0.58,
            "stream_commit_min_words": 5,
            "cumulative_merge_overlap_min": 2,
            "stream_show_previous_caption": False,
            "stream_max_pending_chunks": 2,
            "cumulative_max_pending_chunks": 3,
            "stream_translation_min_chars": 16,
            "cumulative_translation_min_chars": 18,
            "context_policy": CONTEXT_POLICY_OFF,
            "silence_guard": SILENCE_GUARD_NORMAL,
        },
        LIVE_PROFILE_BALANCED: {
            "chunk_length_s": 5,
            "stride_length_s": 4,
            "stream_commit_silence_s": 0.64,
            "cumulative_commit_silence_s": 0.72,
            "stream_clear_after_s": 1.30,
            "stream_replace_prefix_ratio": 0.64,
            "stream_commit_min_words": 6,
            "cumulative_merge_overlap_min": 2,
            "stream_show_previous_caption": False,
            "stream_max_pending_chunks": 3,
            "cumulative_max_pending_chunks": 4,
            "stream_translation_min_chars": 18,
            "cumulative_translation_min_chars": 20,
            "context_policy": CONTEXT_POLICY_AUTO,
            "silence_guard": SILENCE_GUARD_NORMAL,
        },
        LIVE_PROFILE_HIGH_CONTEXT: {
            "chunk_length_s": 7,
            "stride_length_s": 5,
            "stream_commit_silence_s": 0.78,
            "cumulative_commit_silence_s": 0.86,
            "stream_clear_after_s": 1.55,
            "stream_replace_prefix_ratio": 0.70,
            "stream_commit_min_words": 7,
            "cumulative_merge_overlap_min": 3,
            "stream_show_previous_caption": False,
            "stream_max_pending_chunks": 3,
            "cumulative_max_pending_chunks": 5,
            "stream_translation_min_chars": 22,
            "cumulative_translation_min_chars": 24,
            "context_policy": CONTEXT_POLICY_AGGRESSIVE,
            "silence_guard": SILENCE_GUARD_NORMAL,
        },
    }

    _SILENCE_GUARD_OVERRIDES: dict[str, dict[str, Any]] = {
        SILENCE_GUARD_OFF: {
            "weak_rms_threshold": 0.009,
            "weak_activity_floor": 0.0045,
            "weak_active_ratio_threshold": 0.008,
            "weak_active_ms_threshold": 35.0,
            "solid_rms_threshold": 0.0125,
            "solid_activity_floor": 0.0055,
            "solid_active_ratio_threshold": 0.016,
            "solid_active_ms_threshold": 65.0,
            "language_detect_rms_threshold": 0.02,
            "language_detect_activity_floor": 0.007,
            "language_detect_active_ratio_threshold": 0.045,
            "language_detect_active_ms_threshold": 95.0,
            "artifact_min_chars": 2,
            "artifact_min_words": 1,
            "artifact_tail_max_words": 1,
            "artifact_tail_max_chars": 8,
            "whisper_no_speech_threshold": 0.25,
            "whisper_logprob_threshold": -1.35,
            "whisper_compression_ratio_threshold": 2.6,
        },
        SILENCE_GUARD_NORMAL: {},
        SILENCE_GUARD_STRICT: {
            "weak_rms_threshold": 0.013,
            "weak_activity_floor": 0.006,
            "weak_active_ratio_threshold": 0.018,
            "weak_active_ms_threshold": 75.0,
            "solid_rms_threshold": 0.0165,
            "solid_activity_floor": 0.0075,
            "solid_active_ratio_threshold": 0.028,
            "solid_active_ms_threshold": 115.0,
            "language_detect_rms_threshold": 0.034,
            "language_detect_activity_floor": 0.0105,
            "language_detect_active_ratio_threshold": 0.085,
            "language_detect_active_ms_threshold": 190.0,
            "artifact_min_chars": 4,
            "artifact_min_words": 2,
            "artifact_tail_max_words": 2,
            "artifact_tail_max_chars": 12,
            "whisper_no_speech_threshold": 0.58,
            "whisper_logprob_threshold": -0.75,
            "whisper_compression_ratio_threshold": 2.0,
        },
    }

    _CONTEXT_POLICY_OVERRIDES: dict[str, dict[str, Any]] = {
        CONTEXT_POLICY_OFF: {
            "condition_on_prev_tokens": False,
            "use_prompt": False,
            "prompt_on_weak_signal": False,
        },
        CONTEXT_POLICY_AUTO: {
            "condition_on_prev_tokens": True,
            "use_prompt": True,
            "prompt_on_weak_signal": False,
        },
        CONTEXT_POLICY_AGGRESSIVE: {
            "condition_on_prev_tokens": True,
            "use_prompt": True,
            "prompt_on_weak_signal": True,
        },
    }

    _LANGUAGE_STABILITY_OVERRIDES: dict[str, dict[str, Any]] = {
        LANGUAGE_STABILITY_FAST: {
            "stable_language_min_hits": 1,
            "allow_weak_language_detection": True,
        },
        LANGUAGE_STABILITY_BALANCED: {
            "stable_language_min_hits": 2,
            "allow_weak_language_detection": False,
        },
        LANGUAGE_STABILITY_STRICT: {
            "stable_language_min_hits": 3,
            "allow_weak_language_detection": False,
        },
    }

    _TRANSLATION_STYLE_OVERRIDES: dict[str, dict[str, Any]] = {
        TRANSLATION_STYLE_LITERAL: {
            "num_beams": 1,
            "no_repeat_ngram_size": 0,
        },
        TRANSLATION_STYLE_BALANCED: {
            "num_beams": 3,
            "no_repeat_ngram_size": 3,
        },
        TRANSLATION_STYLE_FLUENT: {
            "num_beams": 5,
            "no_repeat_ngram_size": 3,
        },
    }

    @staticmethod
    def _normalized_token(value: Any) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def normalize_live_ui_mode(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.LIVE_UI_MODES):
            return cls.LIVE_UI_DEFAULT_MODE
        return token

    @classmethod
    def normalize_live_output_mode(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.LIVE_OUTPUT_MODES):
            return cls.LIVE_OUTPUT_MODE_CUMULATIVE
        return token

    @classmethod
    def normalize_transcription_profile(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.TRANSCRIPTION_PROFILE_IDS):
            return cls.TRANSCRIPTION_DEFAULT_PROFILE
        return token

    @classmethod
    def normalize_translation_profile(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.TRANSLATION_PROFILE_IDS):
            return cls.TRANSLATION_DEFAULT_PROFILE
        return token

    @classmethod
    def normalize_live_profile(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.LIVE_PROFILE_IDS):
            return cls.LIVE_DEFAULT_PROFILE
        return token

    @classmethod
    def normalize_context_policy(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.CONTEXT_POLICIES):
            return cls.CONTEXT_POLICY_AUTO
        return token

    @classmethod
    def normalize_silence_guard(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.SILENCE_GUARDS):
            return cls.SILENCE_GUARD_NORMAL
        return token

    @classmethod
    def normalize_language_stability(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.LANGUAGE_STABILITIES):
            return cls.LANGUAGE_STABILITY_BALANCED
        return token

    @classmethod
    def normalize_translation_style(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        if token not in set(cls.TRANSLATION_STYLES):
            return cls.TRANSLATION_STYLE_BALANCED
        return token

    @staticmethod
    def _merge_profile(base: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
        out = dict(base)
        if isinstance(extra, dict):
            out.update({k: v for k, v in extra.items() if v not in (None, "")})
        return out

    @classmethod
    def _resolve_transcription_semantics(cls, *, profile: dict[str, Any]) -> dict[str, Any]:
        context_policy = cls.normalize_context_policy(profile.get("context_policy"))
        silence_guard = cls.normalize_silence_guard(profile.get("silence_guard"))
        language_stability = cls.normalize_language_stability(profile.get("language_stability"))
        resolved = cls._merge_profile(cls._TRANSCRIPTION_BASE_AUDIO, {})
        resolved.update(cls._SILENCE_GUARD_OVERRIDES.get(silence_guard, {}))
        resolved.update(cls._CONTEXT_POLICY_OVERRIDES.get(context_policy, {}))
        resolved.update(cls._LANGUAGE_STABILITY_OVERRIDES.get(language_stability, {}))
        resolved["context_policy"] = context_policy
        resolved["silence_guard"] = silence_guard
        resolved["language_stability"] = language_stability
        return resolved

    @classmethod
    def resolve_transcription_runtime(cls, *, profile: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_profile = cls.normalize_transcription_profile(profile)
        base_profile = (
            cls.TRANSCRIPTION_DEFAULT_PROFILE
            if normalized_profile == cls.TRANSCRIPTION_PROFILE_CUSTOM
            else normalized_profile
        )
        base = cls._merge_profile(
            cls._TRANSCRIPTION_PROFILE_DEFAULTS.get(
                base_profile,
                cls._TRANSCRIPTION_PROFILE_DEFAULTS[cls.TRANSCRIPTION_DEFAULT_PROFILE],
            ),
            overrides if normalized_profile == cls.TRANSCRIPTION_PROFILE_CUSTOM else None,
        )
        runtime = cls._resolve_transcription_semantics(profile=base)
        runtime.update(base)
        runtime["profile"] = normalized_profile
        runtime["chunk_length_s"] = int(max(5, min(int(runtime.get("chunk_length_s", 45) or 45), 120)))
        stride_raw = int(runtime.get("stride_length_s", 5) or 5)
        runtime["stride_length_s"] = int(max(0, min(stride_raw, max(0, runtime["chunk_length_s"] - 1))))
        return runtime

    @classmethod
    def resolve_translation_runtime(cls, *, profile: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_profile = cls.normalize_translation_profile(profile)
        base_profile = (
            cls.TRANSLATION_DEFAULT_PROFILE
            if normalized_profile == cls.TRANSLATION_PROFILE_CUSTOM
            else normalized_profile
        )
        base = cls._merge_profile(
            cls._TRANSLATION_PROFILE_DEFAULTS.get(
                base_profile,
                cls._TRANSLATION_PROFILE_DEFAULTS[cls.TRANSLATION_DEFAULT_PROFILE],
            ),
            overrides if normalized_profile == cls.TRANSLATION_PROFILE_CUSTOM else None,
        )
        style = cls.normalize_translation_style(base.get("style"))
        runtime = dict(base)
        runtime.update(cls._TRANSLATION_STYLE_OVERRIDES.get(style, {}))
        runtime.update({k: v for k, v in dict(base).items() if v not in (None, "")})
        runtime["profile"] = normalized_profile
        runtime["style"] = style
        runtime["num_beams"] = int(max(1, min(int(runtime.get("num_beams", 3) or 3), 8)))
        runtime["no_repeat_ngram_size"] = int(max(0, min(int(runtime.get("no_repeat_ngram_size", 0) or 0), 8)))
        return runtime

    @classmethod
    def resolve_live_runtime(
        cls,
        *,
        output_mode: Any,
        profile: Any,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output_mode_id = cls.normalize_live_output_mode(output_mode)
        normalized_profile = cls.normalize_live_profile(profile)
        base = cls._merge_profile(
            cls._LIVE_AUDIO_SIGNAL_PROFILE,
            cls._LIVE_PROFILE_DEFAULTS.get(normalized_profile, {}),
        )
        base = cls._merge_profile(base, overrides)
        context_policy = cls.normalize_context_policy(base.get("context_policy"))
        silence_guard = cls.normalize_silence_guard(base.get("silence_guard"))
        runtime = dict(base)
        runtime.update(cls._SILENCE_GUARD_OVERRIDES.get(silence_guard, {}))
        runtime.update(cls._CONTEXT_POLICY_OVERRIDES.get(context_policy, {}))
        runtime["context_policy"] = context_policy
        runtime["silence_guard"] = silence_guard
        runtime["output_mode"] = output_mode_id
        runtime["profile"] = normalized_profile
        runtime["stream_mode"] = output_mode_id == cls.LIVE_OUTPUT_MODE_STREAM
        runtime["commit_silence_s"] = float(
            runtime["stream_commit_silence_s"]
            if runtime["stream_mode"]
            else runtime["cumulative_commit_silence_s"]
        )
        runtime["max_pending_chunks"] = int(
            runtime["stream_max_pending_chunks"]
            if runtime["stream_mode"]
            else runtime["cumulative_max_pending_chunks"]
        )
        return runtime
