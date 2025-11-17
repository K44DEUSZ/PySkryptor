#core/utils/text.py
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional


__all__ = [
    "is_url",
    "sanitize_filename",
    "format_bytes",
    "format_hms",
    "clean_text",
    "TextPostprocessor",
]

_URL_RE = re.compile(r"^(?:https?://|ftp://)", re.IGNORECASE)


def is_url(value: str) -> bool:
    """Return True if value looks like an URL."""
    return bool(value) and bool(_URL_RE.match(value.strip()))


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Normalize and make a filename safe for most filesystems."""
    if not name:
        return "file"
    n = unicodedata.normalize("NFKC", name)
    n = n.replace("/", "_").replace("\\", "_").strip()
    n = re.sub(r"[\r\n\t\b\f]", "", n)
    n = re.sub(r'[:*?"<>|]', "_", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"_+", "_", n)
    if len(n) > max_len:
        stem, ext = Path(n).stem, Path(n).suffix
        allowed = max(1, max_len - len(ext) - 1)
        n = stem[:allowed] + ext
    return n or "file"


def format_bytes(num: Optional[int]) -> str:
    """Human-readable file size."""
    if not num or num <= 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for u in units:
        if value < 1024 or u == units[-1]:
            return f"{value:.0f} {u}"
        value /= 1024.0


def format_hms(seconds: Optional[float]) -> str:
    """Format seconds → HH:MM:SS."""
    if seconds is None:
        return "-"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_text(text: str) -> str:
    """Light cleanup for ASR output (newlines/whitespace)."""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


class TextPostprocessor:
    """Backwards-compatible wrapper around clean_text()."""

    @staticmethod
    def clean(text: str) -> str:
        return clean_text(text)
