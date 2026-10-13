# app/runtime/capabilities.py
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from app.core.config.config import AppConfig
from app.core.utils.string_utils import normalize_lang_code

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


def _extract_list(data: dict[str, Any], key: str) -> list[Any]:
  values = data.get(key)
  return values if isinstance(values, list) else []


def _parse_m2m100_language_token(token: Any) -> str:
  text = str(token or "").strip()
  if len(text) < 4 or not text.startswith("__") or not text.endswith("__"):
    return ""
  code = text[2:-2]
  return normalize_lang_code(code, drop_region=False) if re.fullmatch(r'[a-z]{2,3}', code) else ''


def _parse_whisper_language_token(token: Any) -> str:
  text = str(token or "").strip()
  if len(text) < 6 or not text.startswith("<|") or not text.endswith("|>"):
    return ""
  code = text[2:-2]
  return normalize_lang_code(code, drop_region=False) if re.fullmatch(r'[a-z]{2,3}', code) else ''


def _collect_supported_language_codes(
  path: Path,
  *,
  cache_key: str,
  parse_token: Callable[[Any], str],
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
  for token in tokens:
    normalized = parse_token(token)
    if normalized:
      codes.add(normalized)

  return _cache_put(cache_key, modified_time, codes)


def translation_language_codes() -> set[str]:
  """Return supported translation language codes from the active tokenizer."""
  path = AppConfig.translation_model_tokenizer_path()
  return _collect_supported_language_codes(
    path,
    cache_key=f"m2m100::{path}",
    parse_token=_parse_m2m100_language_token,
  )


def transcription_language_codes() -> set[str]:
  """Return supported transcription language codes from the active tokenizer."""
  path = AppConfig.transcription_model_tokenizer_path()
  return _collect_supported_language_codes(
    path,
    cache_key=f"whisper::{path}",
    parse_token=_parse_whisper_language_token,
  )
