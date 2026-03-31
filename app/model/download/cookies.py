# app/model/download/cookies.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_COOKIE_FILE_RUNTIME_INVALID_MARKERS: tuple[str, ...] = (
    "failed to load cookies",
    "cookieloaderror",
    "unicodedecodeerror",
    "unicode decode error",
    "codec can't decode",
    "invalid start byte",
    "invalid continuation byte",
    "invalid cookie file",
)


@dataclass(frozen=True)
class CookieFileValidationResult:
    """Normalized result of validating an exported cookies file."""

    ok: bool
    reason: str = ""
    path: str = ""
    detail: str = ""


def _display_path(raw_path: Any) -> tuple[str, Path | None]:
    raw = str(raw_path or "").strip()
    if not raw:
        return "", None
    try:
        candidate = Path(raw).expanduser()
    except (OSError, RuntimeError, TypeError, ValueError):
        return raw, None
    return str(candidate), candidate


def _looks_like_cookie_export(text: str) -> bool:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        fields = raw_line.split("\t")
        if len(fields) < 7:
            return False
        domain = str(fields[0] or "").strip()
        include_subdomains = str(fields[1] or "").strip().upper()
        cookie_path = str(fields[2] or "").strip()
        secure = str(fields[3] or "").strip().upper()
        expires = str(fields[4] or "").strip()
        name = str(fields[5] or "").strip()
        if not domain or include_subdomains not in {"TRUE", "FALSE"}:
            return False
        if not cookie_path or secure not in {"TRUE", "FALSE"}:
            return False
        if expires and not expires.lstrip("-").isdigit():
            return False
        if not name:
            return False
        return True
    return False


def validate_cookie_file(path: str | Path | None) -> CookieFileValidationResult:
    """Validate that a cookie file is readable and looks like a Netscape export."""
    display_path, candidate = _display_path(path)
    if not display_path:
        return CookieFileValidationResult(
            ok=False,
            reason="path_missing",
            detail="cookies file path is empty",
        )
    if candidate is None:
        return CookieFileValidationResult(
            ok=False,
            reason="unreadable",
            path=display_path,
            detail="cookies file path is invalid",
        )
    if not candidate.exists():
        return CookieFileValidationResult(
            ok=False,
            reason="missing",
            path=display_path,
            detail=f"cookies file not found: {display_path}",
        )
    if not candidate.is_file():
        return CookieFileValidationResult(
            ok=False,
            reason="not_file",
            path=display_path,
            detail=f"cookies path is not a file: {display_path}",
        )
    try:
        raw = candidate.read_bytes()
    except OSError as ex:
        return CookieFileValidationResult(
            ok=False,
            reason="unreadable",
            path=display_path,
            detail=str(ex),
        )
    if not raw:
        return CookieFileValidationResult(
            ok=False,
            reason="empty",
            path=display_path,
            detail=f"cookies file is empty: {display_path}",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as ex:
        return CookieFileValidationResult(
            ok=False,
            reason="invalid",
            path=display_path,
            detail=f"cookies file is not valid UTF-8 text: {display_path} ({ex})",
        )
    if not _looks_like_cookie_export(text):
        return CookieFileValidationResult(
            ok=False,
            reason="invalid",
            path=display_path,
            detail=f"cookies file is not a valid Netscape cookies export: {display_path}",
        )
    return CookieFileValidationResult(ok=True, path=display_path)


def is_cookie_file_runtime_error(detail: Any) -> bool:
    """Return True when yt_dlp detail indicates a cookie-file parsing failure."""
    text = str(detail or "").strip().lower()
    return any(marker in text for marker in _COOKIE_FILE_RUNTIME_INVALID_MARKERS)
