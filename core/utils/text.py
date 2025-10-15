# pyskryptor/core/utils/text.py
from __future__ import annotations

from pathlib import Path


SUPPORTED_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm", ".aac", ".ogg"}


def is_supported_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
