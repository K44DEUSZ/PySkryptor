# core/utils/text.py
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from core.config.app_config import AppConfig as Config


_URL_RE = re.compile(r"^(https?://|www\.)", re.IGNORECASE)


def is_url(s: str) -> bool:
    return bool(_URL_RE.match(s.strip()))


def is_supported_file(path: Path | str) -> bool:
    """
    Returns True if path has an audio/video extension supported by the app.
    """
    p = Path(path)
    ext = p.suffix.lower()
    return ext in Config.AUDIO_EXT or ext in Config.VIDEO_EXT


def sanitize_filename(name: str, max_len: int = 200) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("/", "_").replace("\\", "_").strip().strip(".")
    s = re.sub(r"[^A-Za-z0-9 _\-.]", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s or "file"