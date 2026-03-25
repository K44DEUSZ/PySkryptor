# app/model/config/transcription_output_policy.py
from __future__ import annotations

from typing import Any

from app.model.helpers.string_utils import sanitize_filename


class TranscriptionOutputPolicy:
    """Static transcription output modes and naming rules."""

    OUTPUT_DEFAULT_STEM: str = "item"
    TRANSCRIPT_DEFAULT_BASENAME: str = "transcript"
    TMP_AUDIO_DEFAULT_STEM: str = "audio"
    AUDIO_OUTPUT_DEFAULT_FILENAME: str = "Audio.wav"
    AUDIO_OUTPUT_DEFAULT_BASENAME: str = "Audio"
    SOURCE_MEDIA_DEFAULT_BASENAME: str = "Source"
    SOURCE_MEDIA_DEFAULT_EXT: str = "bin"

    TRANSCRIPTION_OUTPUT_MODES: tuple[dict[str, Any], ...] = (
        {"id": "txt", "ext": "txt", "timestamps": False, "tr_key": "transcription.output_mode.plain_txt.label"},
        {"id": "txt_ts", "ext": "txt", "timestamps": True, "tr_key": "transcription.output_mode.txt_timestamps.label"},
        {"id": "srt", "ext": "srt", "timestamps": True, "tr_key": "transcription.output_mode.srt.label"},
    )

    _TRANSCRIPT_FILENAMES: dict[str, str] = {
        "txt": "transcript.txt",
        "txt_ts": "transcript_ts.txt",
        "srt": "transcript.srt",
    }

    @classmethod
    def transcript_filename(cls, mode_id: str) -> str:
        mid = str(mode_id or "txt").strip().lower()
        if mid in cls._TRANSCRIPT_FILENAMES:
            return cls._TRANSCRIPT_FILENAMES[mid]

        mode = cls.get_transcription_output_mode(mid)
        ext = str(mode.get("ext", "txt") or "txt").strip().lower().lstrip(".") or "txt"
        safe_mid = sanitize_filename(mid) or "mode"
        return f"transcript_{safe_mid}.{ext}"

    @classmethod
    def get_transcription_output_modes(cls) -> tuple[dict[str, Any], ...]:
        return cls.TRANSCRIPTION_OUTPUT_MODES

    @classmethod
    def get_transcription_output_mode(cls, mode_id: str) -> dict[str, Any]:
        mid = str(mode_id or "txt").strip().lower()
        for mode in cls.TRANSCRIPTION_OUTPUT_MODES:
            if str(mode.get("id", "")).lower() == mid:
                return mode
        return cls.TRANSCRIPTION_OUTPUT_MODES[0]

    @classmethod
    def valid_mode_ids(cls) -> tuple[str, ...]:
        return tuple(
            str(mode.get("id", "")).strip().lower()
            for mode in cls.TRANSCRIPTION_OUTPUT_MODES
            if str(mode.get("id", "")).strip()
        )
