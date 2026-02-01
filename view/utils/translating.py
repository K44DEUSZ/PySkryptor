# view/utils/translating.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Set

from PyQt5 import QtCore

_MESSAGES: Dict[str, str] = {}
_CURRENT_LANG: str = "en"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    """Flatten nested dicts into dot-separated keys (e.g., tabs.files)."""
    out: Dict[str, str] = {}
    for k, v in d.items():
        if not isinstance(k, str):
            continue
        if not prefix and k == "meta":
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = str(v)
    return out


def system_lang_hint() -> str:
    loc = QtCore.QLocale.system()
    name = loc.name()  # e.g. 'pl_PL'
    parts = name.split("_")
    lang = parts[0].lower() if parts else "en"
    region = parts[1].lower() if len(parts) > 1 else ""
    return f"{lang}-{region}" if region else lang


def list_locales(locales_dir: Path) -> Set[str]:
    codes: Set[str] = set()
    if not locales_dir.exists():
        return codes
    for p in locales_dir.glob("*.json"):
        if p.is_file():
            codes.add(p.stem.lower())
    return codes


def load(locales_dir: Path, code: str) -> bool:
    global _MESSAGES, _CURRENT_LANG

    code = (code or "").strip().lower()
    if not code:
        return False

    path = locales_dir / f"{code}.json"
    if not path.exists() or not path.is_file():
        return False

    data = _read_json(path)
    flat = _flatten(data)

    if not flat:
        return False

    _MESSAGES = flat
    _CURRENT_LANG = code
    return True


def load_best(
    locales_dir: Path,
    *,
    system_first: bool = True,
    fallback: str = "en",
) -> str:
    available = list_locales(locales_dir)
    if not available:
        return ""

    candidates: list[str] = []
    if system_first:
        hint = system_lang_hint()
        candidates.append(hint)
        if "-" in hint:
            candidates.append(hint.split("-", 1)[0])

    fb = (fallback or "").strip().lower()
    if fb:
        candidates.append(fb)

    for c in candidates:
        if c in available and load(locales_dir, c):
            return c

    first = sorted(available)[0]
    if load(locales_dir, first):
        return first

    return ""


def tr(key: str, **kwargs: Any) -> str:
    text = _MESSAGES.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


class Translator:
    @staticmethod
    def tr(key: str, **kwargs: Any) -> str:
        return tr(key, **kwargs)

    @staticmethod
    def load(locales_dir: Path, code: str) -> bool:
        return load(locales_dir, code)

    @staticmethod
    def load_best(locales_dir: Path, *, system_first: bool = True, fallback: str = "en") -> str:
        return load_best(locales_dir, system_first=system_first, fallback=fallback)

    @staticmethod
    def current_lang() -> str:
        return _CURRENT_LANG
