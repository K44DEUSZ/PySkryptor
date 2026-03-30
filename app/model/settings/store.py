# app/model/settings/store.py
from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.settings.validation import SettingsError

SETTINGS_SECTIONS: tuple[str, ...] = (
    "app",
    "engine",
    "model",
    "transcription",
    "translation",
    "downloader",
    "browser_cookies",
    "network",
)


def read_json_dict(
    path: Path,
    *,
    missing_key: str,
    invalid_key: str = "error.settings.json_invalid",
    root_error_key: str = "error.settings.section_invalid",
    root_section: str = "root",
) -> dict[str, Any]:
    """Read a JSON file and return a dict payload with settings-aware errors."""
    if not path.exists():
        raise SettingsError(missing_key, path=str(path))

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError, TypeError, ValueError) as ex:
        raise SettingsError(invalid_key, path=str(path), detail=str(ex)) from ex

    if isinstance(raw, dict):
        return raw
    if root_error_key == "error.settings.section_invalid":
        raise SettingsError(root_error_key, section=root_section)
    raise SettingsError(root_error_key, path=str(path), detail="root-not-object")


def write_json_dict(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON dict payload using UTF-8 and stable indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge JSON-like mappings."""
    if isinstance(base, dict) and isinstance(patch, dict):
        merged: dict[str, Any] = dict(base)
        for key, value in patch.items():
            merged[key] = _deep_merge(merged.get(key), value) if key in merged else value
        return merged
    return patch


def apply_settings_payload(raw: dict[str, Any], payload: dict[str, Any] | None) -> dict[str, Any]:
    """Apply a partial settings patch onto the raw persisted payload."""
    updated = dict(raw or {})
    for section, patch in (payload or {}).items():
        if section not in SETTINGS_SECTIONS:
            continue
        base = updated.get(section, {})
        if isinstance(base, dict) and isinstance(patch, dict):
            updated[section] = _deep_merge(base, patch)
        else:
            updated[section] = patch
    return updated
