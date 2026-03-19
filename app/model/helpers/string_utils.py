# app/model/helpers/string_utils.py
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

__all__ = [
    "format_bytes",
    "format_hms",
    "normalize_lang_code",
    "sanitize_filename",
    "is_youtube_url",
]


def normalize_lang_code(code: str | None, *, drop_region: bool = True) -> str:
    """Normalize language codes (e.g. 'EN_us' -> 'en')."""
    s = str(code or "").strip().lower().replace("_", "-")
    if not s:
        return ""
    if drop_region:
        s = s.split("-", 1)[0]
    return s


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
    n = n.strip(" .")

    reserved = {
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    }
    if n.lower() in reserved:
        n = f"_{n}"

    if len(n) > max_len:
        stem, ext = Path(n).stem, Path(n).suffix
        allowed = max(1, max_len - len(ext) - 1)
        n = (stem[:allowed] + ext).strip(" .")

    return n or "file"


def is_youtube_url(url: str | None) -> bool:
    s = str(url or "").lower()
    return "youtube.com" in s or "youtu.be" in s


def format_bytes(num: int | None) -> str:
    """Return human-readable file size."""
    if not num or num <= 0:
        return "-"

    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for u in units:
        if value < 1024 or u == units[-1]:
            return f"{value:.0f} {u}"
        value /= 1024.0

    return f"{value:.0f} {units[-1]}"


def format_hms(
    seconds: float | None,
    *,
    blank_for_none: bool = False,
    always_hours: bool = True,
    rounding: str = "round",
) -> str:
    """Format seconds as HH:MM:SS."""
    if seconds is None:
        return "" if blank_for_none else "-"

    try:
        sec_f = float(seconds)
    except (TypeError, ValueError):
        return "" if blank_for_none else "-"

    if sec_f < 0:
        return "" if blank_for_none else "-"

    if rounding == "floor":
        total = int(sec_f)
    elif rounding == "ceil":
        total = int(sec_f) if sec_f.is_integer() else int(sec_f) + 1
    else:
        total = int(round(sec_f))

    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)

    if not always_hours and h == 0:
        return f"{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"
