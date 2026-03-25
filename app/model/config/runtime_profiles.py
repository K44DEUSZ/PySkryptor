# app/model/config/runtime_profiles.py
from __future__ import annotations

from typing import Any

class RuntimeProfiles:
    """Centralized access to live transcription runtime presets."""

    LIVE_OUTPUT_MODE_STREAM = "stream"
    LIVE_OUTPUT_MODE_CUMULATIVE = "cumulative"
    LIVE_OUTPUT_MODES: tuple[str, ...] = (LIVE_OUTPUT_MODE_STREAM, LIVE_OUTPUT_MODE_CUMULATIVE)

    LIVE_UI_MODE_TRANSCRIBE = "transcribe"
    LIVE_UI_MODE_TRANSCRIBE_TRANSLATE = "transcribe_translate"
    LIVE_UI_MODES: tuple[str, ...] = (LIVE_UI_MODE_TRANSCRIBE, LIVE_UI_MODE_TRANSCRIBE_TRANSLATE)
    LIVE_UI_DEFAULT_MODE = LIVE_UI_MODE_TRANSCRIBE

    LIVE_PRESET_LOW_LATENCY = "low_latency"
    LIVE_PRESET_BALANCED = "balanced"
    LIVE_PRESET_HIGH_CONTEXT = "high_context"
    LIVE_DEFAULT_PRESET = LIVE_PRESET_BALANCED

    TRANSCRIPTION_PRESET_FAST = "fast"
    TRANSCRIPTION_PRESET_BALANCED = "balanced"
    TRANSCRIPTION_PRESET_ACCURATE = "accurate"
    TRANSCRIPTION_DEFAULT_PRESET = TRANSCRIPTION_PRESET_BALANCED
    TRANSCRIPTION_PRESET_IDS: tuple[str, ...] = (
        TRANSCRIPTION_PRESET_FAST,
        TRANSCRIPTION_PRESET_BALANCED,
        TRANSCRIPTION_PRESET_ACCURATE,
    )
    TRANSCRIPTION_PRESET_PROFILES: dict[str, dict[str, Any]] = {
        "fast": {
            "chunk_length_s": 30,
            "stride_length_s": 3,
        },
        "balanced": {
            "chunk_length_s": 45,
            "stride_length_s": 5,
        },
        "accurate": {
            "chunk_length_s": 60,
            "stride_length_s": 8,
        },
    }
    LIVE_PRESET_IDS: tuple[str, ...] = (LIVE_PRESET_LOW_LATENCY, LIVE_PRESET_BALANCED, LIVE_PRESET_HIGH_CONTEXT)

    LIVE_AUDIO_SIGNAL_PROFILE: dict[str, Any] = {
        "silence_level_threshold": 0.055,
        "silence_audio_rms_min": 0.007,
        "silence_tail_keep_s": 0.24,
        "tail_flush_min_s": 0.20,
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
    }

    LIVE_PRESET_PROFILES: dict[str, dict[str, Any]] = {
        "low_latency": {
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
        },
        "balanced": {
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
        },
        "high_context": {
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
        },
    }

    @classmethod
    def normalize_live_ui_mode(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token not in set(cls.LIVE_UI_MODES):
            return cls.LIVE_UI_DEFAULT_MODE
        return token

    @classmethod
    def normalize_live_output_mode(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token not in set(cls.LIVE_OUTPUT_MODES):
            return cls.LIVE_OUTPUT_MODE_CUMULATIVE
        return token

    @classmethod
    def normalize_live_preset(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token not in set(cls.LIVE_PRESET_IDS):
            return cls.LIVE_DEFAULT_PRESET
        return token

    @classmethod
    def normalize_transcription_preset(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token not in set(cls.TRANSCRIPTION_PRESET_IDS):
            return cls.TRANSCRIPTION_DEFAULT_PRESET
        return token

    @classmethod
    def transcription_preset_profile(cls, preset: Any) -> dict[str, Any]:
        preset_id = cls.normalize_transcription_preset(preset)
        profile = cls.TRANSCRIPTION_PRESET_PROFILES.get(preset_id) or cls.TRANSCRIPTION_PRESET_PROFILES[cls.TRANSCRIPTION_DEFAULT_PRESET]
        return dict(profile)

    @classmethod
    def live_audio_profile(cls) -> dict[str, Any]:
        return dict(cls.LIVE_AUDIO_SIGNAL_PROFILE)

    @classmethod
    def live_preset_profile(cls, preset: Any) -> dict[str, Any]:
        preset_id = cls.normalize_live_preset(preset)
        profile = cls.LIVE_PRESET_PROFILES.get(preset_id) or cls.LIVE_PRESET_PROFILES[cls.LIVE_DEFAULT_PRESET]
        return dict(profile)

    @classmethod
    def live_runtime_profile(cls, *, output_mode: Any, preset: Any) -> dict[str, Any]:
        output_mode_id = cls.normalize_live_output_mode(output_mode)
        preset_id = cls.normalize_live_preset(preset)
        profile = cls.live_audio_profile()
        profile.update(cls.live_preset_profile(preset_id))
        profile["output_mode"] = output_mode_id
        profile["preset_id"] = preset_id
        profile["stream_mode"] = output_mode_id == cls.LIVE_OUTPUT_MODE_STREAM
        profile["commit_silence_s"] = float(
            profile["stream_commit_silence_s"]
            if profile["stream_mode"]
            else profile["cumulative_commit_silence_s"]
        )
        profile["max_pending_chunks"] = int(
            profile["stream_max_pending_chunks"]
            if profile["stream_mode"]
            else profile["cumulative_max_pending_chunks"]
        )
        return profile
