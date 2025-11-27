from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional, Any, Dict, List


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
    """Return human-readable file size."""
    if not num or num <= 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for u in units:
        if value < 1024 or u == units[-1]:
            return f"{value:.0f} {u}"
        value /= 1024.0


def format_hms(seconds: Optional[float]) -> str:
    """Format seconds as HH:MM:SS."""
    if seconds is None:
        return "-"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_text(text: str) -> str:
    """Light cleanup for ASR output (newlines and whitespace)."""
    t = text.replace("\r\n", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _format_ts_srt(seconds: float) -> str:
    """Format seconds into SRT timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    s, ms = divmod(ms_total, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_ts_plain(seconds: float) -> str:
    """Format seconds into HH:MM:SS for plain timestamped text."""
    if seconds < 0:
        seconds = 0.0
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class TextPostprocessor:
    """
    Helpers for post-processing ASR output.
    Backwards compatible with the original .clean(text) API.
    """

    # ----- Legacy / simple text -----

    @staticmethod
    def clean(text: str) -> str:
        """Clean arbitrary text."""
        return clean_text(text)

    @staticmethod
    def plain_from_result(result: Any) -> str:
        """
        Extract plain text from pipeline result and clean it.
        Works both for dicts (with 'text') and raw strings.
        """
        if isinstance(result, dict):
            text = result.get("text", "")
        else:
            text = result
        return clean_text(str(text))

    # ----- Segments (timestamps) -----

    @staticmethod
    def segments_from_result(result: Any) -> List[Dict[str, Any]]:
        """
        Build a normalized list of segments from pipeline result.

        Expected common shapes:
          - {"text": "...", "chunks": [{"text": "...", "timestamp": (start, end)}, ...]}
          - {"text": "...", "segments": [{"text": "...", "start": s, "end": e}, ...]}
        Fallback: single segment with the whole text.
        """
        raw_segments: List[Any] = []

        if isinstance(result, dict):
            if isinstance(result.get("chunks"), list):
                raw_segments = result["chunks"]
            elif isinstance(result.get("segments"), list):
                raw_segments = result["segments"]

        segments: List[Dict[str, Any]] = []

        for ch in raw_segments:
            if not isinstance(ch, dict):
                continue

            text = clean_text(str(ch.get("text", "")))
            if not text:
                continue

            # Try 'timestamp': (start, end) first.
            start = None
            end = None
            ts = ch.get("timestamp")
            if isinstance(ts, (list, tuple)) and len(ts) == 2:
                start, end = ts
            else:
                start = ch.get("start")
                end = ch.get("end")

            try:
                start_f = float(start) if start is not None else 0.0
            except Exception:
                start_f = 0.0
            try:
                end_f = float(end) if end is not None else start_f
            except Exception:
                end_f = start_f

            if end_f < start_f:
                end_f = start_f

            segments.append(
                {
                    "start": start_f,
                    "end": end_f,
                    "text": text,
                }
            )

        if segments:
            return segments

        # Fallback: no structured segments, wrap the whole text.
        text = TextPostprocessor.plain_from_result(result)
        if not text:
            return []
        return [{"start": 0.0, "end": 0.0, "text": text}]

    # ----- Renderers -----

    @staticmethod
    def to_plain(segments: List[Dict[str, Any]]) -> str:
        """Join segments into plain text (one segment per line)."""
        lines: List[str] = []
        for seg in segments:
            text = clean_text(str(seg.get("text", "")))
            if text:
                lines.append(text)
        return "\n".join(lines).strip()

    @staticmethod
    def to_srt(segments: List[Dict[str, Any]]) -> str:
        """Render segments as SRT subtitles."""
        lines: List[str] = []
        idx = 1
        for seg in segments:
            text = clean_text(str(seg.get("text", "")))
            if not text:
                continue

            start = float(seg.get("start", 0.0) or 0.0)
            end = float(seg.get("end", start) or start)

            if end <= start:
                end = start + 0.5

            lines.append(str(idx))
            lines.append(f"{_format_ts_srt(start)} --> {_format_ts_srt(end)}")
            lines.append(text)
            lines.append("")
            idx += 1

        return "\n".join(lines).rstrip()

    @staticmethod
    def to_timestamped_plain(segments: List[Dict[str, Any]]) -> str:
        """
        Render segments as plain text with timestamps:
        "HH:MM:SS Text..."
        """
        lines: List[str] = []
        for seg in segments:
            text = clean_text(str(seg.get("text", "")))
            if not text:
                continue
            start = float(seg.get("start", 0.0) or 0.0)
            ts = _format_ts_plain(start)
            lines.append(f"{ts} {text}")
        return "\n".join(lines).rstrip()
