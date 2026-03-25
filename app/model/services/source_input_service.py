# app/model/services/source_input_service.py
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeAlias
from urllib.parse import parse_qs, urlparse

from app.model.config.download_policy import DownloadPolicy
from app.model.io.file_manager import FileManager
from app.model.io.media_probe import is_url_source

EntryPayload: TypeAlias = dict[str, Any]


def is_playlist_url(url: str) -> bool:
    """Return True when the given URL likely points to a playlist."""
    value = str(url or "").strip()
    if not value:
        return False

    parsed = urlparse(value)
    query = parse_qs(parsed.query or "")
    if query.get("list"):
        return True
    if "playlist" in (parsed.path or "").lower():
        return True
    if "list=" in (parsed.fragment or ""):
        return True
    return False


def _files_media_supported_extensions() -> list[str]:
    return list(DownloadPolicy.files_media_input_file_exts())


def parse_source_input(raw: str) -> EntryPayload:
    """Parse a raw source input from the Files panel."""
    return FileManager.parse_source_input(
        raw,
        supported_exts=_files_media_supported_extensions(),
    )


def collect_media_files(
    paths: list[str],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[str]:
    """Collect media files from the given paths for the Files panel."""
    return FileManager.collect_media_files(
        list(paths),
        supported_exts=_files_media_supported_extensions(),
        cancel_check=cancel_check,
    )


def normalize_source_key(raw: str) -> str:
    """Normalize a user-provided source key (path/URL)."""
    return str(raw or "").strip()


def try_add_source_key(existing: set[str], raw: str) -> tuple[bool, str, bool]:
    """Try to add a source key, returning (ok, normalized_key, duplicate)."""
    key = normalize_source_key(raw)
    if not key:
        return False, "", False
    if key in existing:
        return False, key, True
    existing.add(key)
    return True, key, False


def build_entries(keys: Iterable[str], audio_lang_by_key: dict[str, str | None]) -> list[EntryPayload]:
    """Build worker input entries for the given sources."""
    out: list[EntryPayload] = []
    for raw_key in keys:
        source_key = normalize_source_key(raw_key)
        if not source_key:
            continue
        payload: EntryPayload = {"src": source_key}
        if is_url_source(source_key):
            lang = audio_lang_by_key.get(source_key)
            if lang:
                payload["audio_lang"] = lang
        out.append(payload)
    return out
