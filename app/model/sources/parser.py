# app/model/sources/parser.py
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeAlias
from urllib.parse import parse_qs, urlparse

from app.model.download.policy import DownloadPolicy
from app.model.sources.probe import is_url_source

EntryPayload: TypeAlias = dict[str, object]


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


def files_media_supported_extensions() -> list[str]:
    """Return supported media extensions for Files panel input parsing."""
    return list(DownloadPolicy.files_media_input_file_exts())


def parse_source_input(raw: str) -> EntryPayload:
    """Parse a raw source input from the Files panel."""
    key = normalize_source_key(raw)
    if not key:
        return {"ok": False, "error": "empty"}

    if is_url_source(key):
        return {"ok": True, "type": "url", "key": key}

    path = Path(key)
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "not_found"}

    supported_extensions = {str(ext).lower().lstrip(".") for ext in files_media_supported_extensions()}
    if supported_extensions and path.suffix.lower().lstrip(".") not in supported_extensions:
        return {"ok": False, "error": "unsupported"}

    return {"ok": True, "type": "file", "key": str(path)}


def collect_media_files(
    paths: list[str],
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> list[str]:
    """Collect media files from the given paths for the Files panel."""
    supported_extensions = {str(ext).lower().lstrip(".") for ext in files_media_supported_extensions()}
    out: list[str] = []

    def _guard_cancel() -> None:
        if cancel_check is not None and bool(cancel_check()):
            from app.model.core.domain.errors import OperationCancelled

            raise OperationCancelled()

    def _add_file(file_path: Path) -> None:
        _guard_cancel()
        if not file_path.exists() or not file_path.is_file():
            return
        if supported_extensions and file_path.suffix.lower().lstrip(".") not in supported_extensions:
            return
        out.append(str(file_path))

    for raw in list(paths or []):
        _guard_cancel()
        norm = normalize_source_key(raw)
        if not norm:
            continue
        path = Path(norm)
        if not path.exists():
            continue
        if path.is_dir():
            for child_path in path.rglob("*"):
                _guard_cancel()
                if child_path.is_file():
                    _add_file(child_path)
        else:
            _add_file(path)

    return list(dict.fromkeys(out))


def normalize_source_key(raw: str) -> str:
    """Normalize a user-provided source key (path/URL)."""
    return str(raw or "").strip()



def build_entries(source_keys: list[str], audio_track_by_key: dict[str, str]) -> list[dict[str, Any]]:
    """Build normalized transcription entries, attaching audio tracks only for URLs."""
    entries: list[dict[str, Any]] = []
    for raw_key in source_keys or []:
        key = normalize_source_key(raw_key)
        if not key:
            continue

        entry: dict[str, Any] = {"src": key}
        audio_track_id = str((audio_track_by_key or {}).get(key) or "").strip()
        if audio_track_id and is_url_source(key):
            entry["audio_track_id"] = audio_track_id
        entries.append(entry)
    return entries
