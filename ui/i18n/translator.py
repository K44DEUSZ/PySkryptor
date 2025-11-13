# ui/i18n/translator.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Set

from PyQt5 import QtCore


_MESSAGES: Dict[str, str] = {}
_CURRENT_LANG: str = "en"


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("locale file must contain a JSON object")
    return data


def system_lang_hint() -> str:
    """Return system language as a short code like 'pl' or 'en-us'."""
    loc = QtCore.QLocale.system()
    name = loc.name()  # e.g. 'pl_PL'
    parts = name.split("_")
    lang = parts[0].lower() if parts else "en"
    region = parts[1].lower() if len(parts) > 1 else ""
    return f"{lang}-{region}" if region else lang


def discover_locales(locales_dir: Path) -> Set[str]:
    """Discover available locale codes from *.json files in locales_dir."""
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
    """Load exact language file (without discovery logic)."""
    global _MESSAGES, _CURRENT_LANG
    path: Optional[Path] = None

    # Prefer exact match, then base language (e.g. pt-br -> pt)
    exact = locales_dir / f"{lang.lower()}.json"
    base = locales_dir / f"{lang.split('-', 1)[0].lower()}.json"
    if exact.exists():
        path = exact
    elif base.exists():
        path = base

    if path is None:
        raise FileNotFoundError(f"locale '{lang}' not found in {locales_dir}")

    messages = _read_json(path)
    # Flatten only simple key -> string entries
    _MESSAGES = {k: str(v) for k, v in messages.items() if isinstance(k, str)}
    _CURRENT_LANG = lang


def load_best(locales_dir: Path, system_first: bool = True, fallback: str = "en") -> None:
    """
    Discover available locales and load the best match.
    - If system_first: use system language as primary hint.
    - Otherwise: use 'fallback' as primary hint.
    """
    available = discover_locales(locales_dir)
    if not available:
        # No locales available; keep empty dictionary
        return
    hint = system_lang_hint() if system_first else fallback
    picked = _pick_best(hint, available, fallback=fallback)
    load(locales_dir, picked)


def tr(key: str, **params: Any) -> str:
    """Translate key with optional {format} parameters."""
    template = _MESSAGES.get(key, key)
    try:
        return template.format(**params)
    except Exception:
        return template


class Translator:
    """
    Backward-compatible static facade used across the app.
    """
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
