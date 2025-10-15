# pyskryptor/core/io/audio_extractor.py
from __future__ import annotations

from pathlib import Path
from typing import Optional


class AudioExtractor:
    """Placeholder for future audio extraction helpers (ffmpeg invocations)."""

    @staticmethod
    def ensure_wav_16k_mono(src: Path, dst: Optional[Path] = None) -> Path:
        return src
