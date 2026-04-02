# app/model/engines/capabilities.py
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import lru_cache
from importlib import import_module
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.model.core.config.config import AppConfig
from app.model.core.utils.string_utils import normalize_lang_code

_LOG = logging.getLogger(__name__)

_LANG_CACHE: dict[str, tuple[float, set[str]]] = {}


def _file_modified_time(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError, TypeError, ValueError) as ex:
        _LOG.debug("Language capability JSON read failed. path=%s detail=%s", path, ex)
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


def _load_module_attr(module_name: str, attr_name: str) -> Any | None:
    try:
        module = import_module(module_name)
    except ImportError as ex:
        _LOG.debug("Language capability module import failed. module=%s detail=%s", module_name, ex)
        return None
    value = getattr(module, attr_name, None)
    if value is None:
        _LOG.debug("Language capability attribute lookup failed. module=%s attr=%s", module_name, attr_name)
    return value


@lru_cache(maxsize=1)
def _whisper_canonical_language_codes() -> frozenset[str] | None:
    raw_codes = _load_module_attr("transformers.models.whisper.tokenization_whisper", "TO_LANGUAGE_CODE")
    if not isinstance(raw_codes, dict):
        return None

    codes = {
        normalize_lang_code(str(code or ""), drop_region=False)
        for code in raw_codes.values()
    }
    return frozenset(code for code in codes if code)


@lru_cache(maxsize=1)
def _m2m100_canonical_language_codes() -> frozenset[str] | None:
    raw_catalog = _load_module_attr("transformers.models.m2m_100.tokenization_m2m_100", "FAIRSEQ_LANGUAGE_CODES")
    if not isinstance(raw_catalog, dict):
        return None

    values = raw_catalog.get("m2m100")
    if not isinstance(values, list):
        return None

    codes = {
        normalize_lang_code(str(code or ""), drop_region=False)
        for code in values
    }
    return frozenset(code for code in codes if code)


def _extract_list(data: dict[str, Any], key: str) -> list[Any]:
    values = data.get(key)
    return values if isinstance(values, list) else []


def _parse_m2m100_language_token(token: Any) -> str:
    text = str(token or "").strip()
    if len(text) < 4 or not text.startswith("__") or not text.endswith("__"):
        return ""
    return normalize_lang_code(text[2:-2], drop_region=False)


def _parse_whisper_language_token(token: Any) -> str:
    text = str(token or "").strip()
    if len(text) < 6 or not text.startswith("<|") or not text.endswith("|>"):
        return ""
    code = text[2:-2]
    if "|" in code:
        return ""
    return normalize_lang_code(code, drop_region=False)


def _collect_supported_language_codes(
    path: Path,
    *,
    cache_key: str,
    parse_token: Callable[[Any], str],
    allowed_codes: frozenset[str] | None,
    fallback_accepts: Callable[[str], bool],
) -> set[str]:
    if not path.exists():
        return set()

    modified_time = _file_modified_time(path)
    cached = _cache_get(cache_key, modified_time)
    if cached is not None:
        return cached

    data = _read_json(path)
    tokens = _extract_list(data, "additional_special_tokens")
    codes: set[str] = set()
    if allowed_codes is None:
        _LOG.warning("Language capability fallback applied. path=%s cache_key=%s", path, cache_key)
    for token in tokens:
        normalized = parse_token(token)
        if not normalized:
            continue
        if allowed_codes is None:
            if fallback_accepts(normalized):
                codes.add(normalized)
            continue
        if normalized in allowed_codes:
            codes.add(normalized)

    return _cache_put(cache_key, modified_time, codes)


def translation_language_codes() -> set[str]:
    """Return supported translation language codes from the active tokenizer."""
    path = AppConfig.translation_model_tokenizer_path()
    return _collect_supported_language_codes(
        path,
        cache_key=f"m2m100::{path}",
        parse_token=_parse_m2m100_language_token,
        allowed_codes=_m2m100_canonical_language_codes(),
        fallback_accepts=lambda normalized: bool(normalized),
    )


def transcription_language_codes() -> set[str]:
    """Return supported transcription language codes from the active tokenizer."""
    path = AppConfig.transcription_model_tokenizer_path()
    return _collect_supported_language_codes(
        path,
        cache_key=f"whisper::{path}",
        parse_token=_parse_whisper_language_token,
        allowed_codes=_whisper_canonical_language_codes(),
        fallback_accepts=lambda normalized: 2 <= len(normalized) <= 3,
    )
