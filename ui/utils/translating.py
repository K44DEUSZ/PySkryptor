# ui/i18n/translating.py
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
    except Exception as ex:
        raise RuntimeError(f"error.i18n.locale_invalid::{path}::{ex}")
    if not isinstance(data, dict):
        raise RuntimeError(f"error.i18n.locale_invalid::{path}::root-not-object")
    return data


def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    """Flatten nested dicts into dot-separated keys (e.g., tabs.files)."""
    out: Dict[str, str] = {}
    for k, v in d.items():
        if not isinstance(k, str):
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


def discover_locales(locales_dir: Path) -> Set[str]:
    available: Set[str] = set()
    if not locales_dir.exists():
        return available
    for p in locales_dir.glob("*.json"):
        code = p.stem.strip().lower().replace("_", "-")
        if code:
            available.add(code)
            if "-" in code:
                available.add(code.split("-", 1)[0])
    return available


def _pick_best(sys_hint: str, available: Set[str], fallback: str = "en") -> str:
    if sys_hint in available:
        return sys_hint
    base = sys_hint.split("-", 1)[0]
    if base in available:
        return base
    if fallback in available:
        return fallback
    if "en" in available:
        return "en"
    return sorted(available)[0] if available else "en"


def load(locales_dir: Path, lang: str) -> None:
    """Load exact language file (prefer exact, fallback to base), or raise."""
    global _MESSAGES, _CURRENT_LANG
    locales_dir = Path(locales_dir)

    exact = locales_dir / f"{lang.lower()}.json"
    base = locales_dir / f"{lang.split('-', 1)[0].lower()}.json"

    path: Optional[Path] = exact if exact.exists() else (base if base.exists() else None)
    if path is None:
        raise RuntimeError(f"error.i18n.locale_not_found::{lang}::{locales_dir}")

    data = _read_json(path)
    _MESSAGES = _flatten(data)
    _CURRENT_LANG = lang


def load_best(locales_dir: Path, system_first: bool = True, fallback: str = "en") -> None:
    available = discover_locales(locales_dir)
    if not available:
        _MESSAGES.clear()
        return
    hint = system_lang_hint() if system_first else fallback
    picked = _pick_best(hint, available, fallback=fallback)
    load(locales_dir, picked)


def tr(key: str, **params: Any) -> str:
    template = _MESSAGES.get(key, key)
    try:
        return template.format(**params)
    except Exception:
        return template


class Translator:
    @staticmethod
    def load(locales_dir: Path, lang: str) -> None:
        load(locales_dir, lang)

    @staticmethod
    def load_best(locales_dir: Path, system_first: bool = True, fallback: str = "en") -> None:
        load_best(locales_dir, system_first=system_first, fallback=fallback)

    @staticmethod
    def tr(key: str, **params: Any) -> str:
        return tr(key, **params)

    @staticmethod
    def current_language() -> str:
        return _CURRENT_LANG

    @staticmethod
    def loaded_count() -> int:
        return len(_MESSAGES)
