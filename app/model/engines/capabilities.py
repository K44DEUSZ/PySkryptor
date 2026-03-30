# app/model/engines/capabilities.py
from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.core.config.config import AppConfig
from app.model.core.utils.string_utils import normalize_lang_code

_LANG_CACHE: dict[str, tuple[float, set[str]]] = {}

def _file_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0

def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError, TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}

def _cache_get(key: str, mtime: float) -> set[str] | None:
    current = _LANG_CACHE.get(key)
    if not current:
        return None
    cached_mtime, cached_codes = current
    if float(cached_mtime) == float(mtime):
        return set(cached_codes)
    return None

def _cache_put(key: str, mtime: float, codes: set[str]) -> set[str]:
    normalized_codes = {code for code in (codes or set()) if code}
    _LANG_CACHE[key] = (float(mtime), set(normalized_codes))
    return set(normalized_codes)

def translation_language_codes() -> set[str]:
    """Return supported translation language codes from the active tokenizer."""
    path = AppConfig.translation_model_tokenizer_path()
    if not path.exists():
        return set()

    mtime = _file_mtime(path)
    cache_key = f"m2m100::{path}"
    cached = _cache_get(cache_key, mtime)
    if cached is not None:
        return cached

    data = _read_json(path)
    values = data.get("additional_special_tokens")
    tokens = values if isinstance(values, list) else []

    codes: set[str] = set()
    for token in tokens:
        text = str(token or "").strip()
        if len(text) >= 4 and text.startswith("__") and text.endswith("__"):
            normalized = normalize_lang_code(text[2:-2], drop_region=False)
            if normalized:
                codes.add(normalized)

    return _cache_put(cache_key, mtime, codes)

def transcription_language_codes() -> set[str]:
    """Return supported transcription language codes from the active tokenizer."""
    path = AppConfig.transcription_model_tokenizer_path()
    if not path.exists():
        return set()

    mtime = _file_mtime(path)
    cache_key = f"whisper::{path}"
    cached = _cache_get(cache_key, mtime)
    if cached is not None:
        return cached

    data = _read_json(path)
    values = data.get("additional_special_tokens")
    tokens = values if isinstance(values, list) else []

    codes: set[str] = set()
    for token in tokens:
        text = str(token or "").strip()
        if len(text) >= 6 and text.startswith("<|") and text.endswith("|>"):
            code = text[2:-2]
            if "|" in code:
                continue
            normalized = normalize_lang_code(code, drop_region=False)
            if normalized:
                codes.add(normalized)

    return _cache_put(cache_key, mtime, codes)
