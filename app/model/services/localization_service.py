# app/model/services/localization_service.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt5 import QtCore

from app.model.domain.errors import AppError

LanguageOption = tuple[str, str]
SpecialLanguageOptions = LanguageOption | list[LanguageOption] | tuple[LanguageOption, ...] | None

_MESSAGES: dict[str, str] = {}
_CURRENT_LANG: str = "en"

def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as ex:
        raise AppError(key="error.i18n.locale_invalid", params={"path": str(path), "detail": str(ex)}) from ex
    if not isinstance(data, dict):
        raise AppError(key="error.i18n.locale_invalid", params={"path": str(path), "detail": "root-not-object"})
    return data

def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten nested dicts into dot-separated keys (e.g. tabs.files)."""
    out: dict[str, str] = {}
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

def _system_lang_hint() -> str:
    loc = QtCore.QLocale.system()
    name = loc.name()
    parts = name.split("_")
    lang = parts[0].lower() if parts else "en"
    region = parts[1].lower() if len(parts) > 1 else ""
    return f"{lang}-{region}" if region else lang

def _discover_locales(locales_dir: Path) -> set[str]:
    available: set[str] = set()
    if not locales_dir.exists():
        return available
    for p in locales_dir.glob("*.json"):
        code = p.stem.strip().lower().replace("_", "-")
        if code:
            available.add(code)
            if "-" in code:
                available.add(code.split("-", 1)[0])
    return available

def _locale_display_name(locales_dir: Path, code: str) -> str:
    """Return a human-friendly name for a locale code (from meta.name when available)."""
    locales_dir = Path(locales_dir)
    p = locales_dir / f"{str(code).lower()}.json"
    if not p.exists():
        base_code = str(code).split("-", 1)[0].lower()
        p = locales_dir / f"{base_code}.json"
    if not p.exists():
        return str(code)

    try:
        data = _read_json(p)
    except AppError:
        return str(code)

    meta = data.get("meta") if isinstance(data, dict) else None
    if isinstance(meta, dict):
        name = str(meta.get("name") or "").strip()
        if name:
            return name
    return str(code)

def _pick_best(sys_hint: str, available: set[str], fallback: str = "en") -> str:
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
    base_lang = lang.split("-", 1)[0].lower()
    base = locales_dir / f"{base_lang}.json"

    path: Path | None = exact if exact.exists() else (base if base.exists() else None)
    if path is None:
        raise AppError(key="error.i18n.locale_not_found", params={"lang": str(lang), "path": str(locales_dir)})

    data = _read_json(path)
    _MESSAGES = _flatten(data)
    _CURRENT_LANG = lang

def load_best(locales_dir: Path, system_first: bool = True, fallback: str = "en") -> None:
    """Load the best available locale using the system hint and fallback policy."""
    available = _discover_locales(locales_dir)
    if not available:
        _MESSAGES.clear()
        return
    hint = _system_lang_hint() if system_first else fallback
    picked = _pick_best(hint, available, fallback=fallback)
    load(locales_dir, picked)

def tr(key: str, **params: Any) -> str:
    """Translate a key using the currently loaded locale messages."""
    template = _MESSAGES.get(key, key)
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template

def current_language() -> str:
    """Return the currently loaded locale code."""
    return _CURRENT_LANG

def list_locales(locales_dir: Path) -> list[tuple[str, str]]:
    """List available locales as (code, display_name) sorted by display name."""
    locales_dir = Path(locales_dir)
    items: list[tuple[str, str]] = []
    if not locales_dir.exists():
        return items

    for p in locales_dir.glob("*.json"):
        code = p.stem.strip().lower().replace("_", "-")
        if not code:
            continue
        items.append((code, _locale_display_name(locales_dir, code)))

    items.sort(key=lambda x: (x[1].lower(), x[0]))
    return items

def _normalize_display_lang_code(code: str) -> str:
    return str(code or "").strip().lower().replace("_", "-")

def language_display_name(code: str, *, ui_lang: str | None = None) -> str:
    """Return a user-facing language label, e.g. "polski (pl)"."""
    norm = _normalize_display_lang_code(code)
    if not norm:
        return ""

    try:
        from babel import Locale

        ui_code = _normalize_display_lang_code(ui_lang or _CURRENT_LANG).split("-", 1)[0] or "en"
        loc_ui = Locale.parse(ui_code, sep="-")
        loc_en = Locale.parse("en", sep="-")

        localized = str(loc_ui.languages.get(norm) or "").strip()
        english = str(loc_en.languages.get(norm) or "").strip()

        best = (localized or english or norm).strip()
        if best and best.lower() != norm.lower():
            return f"{best} ({norm})"
        return norm
    except (ImportError, ValueError, AttributeError):
        return norm

def build_language_options(
    codes: list[str] | tuple[str, ...] | set[str],
    *,
    special_first: SpecialLanguageOptions = None,
    ui_lang: str | None = None,
) -> list[tuple[str, str]]:
    """Build sorted (code, label) pairs for plain language combo boxes."""
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    specials: list[tuple[str, str]] = []
    if isinstance(special_first, tuple) and len(special_first) == 2 and isinstance(special_first[0], str):
        specials = [special_first]
    elif isinstance(special_first, (list, tuple)):
        for special_item in special_first:
            if isinstance(special_item, (list, tuple)) and len(special_item) == 2:
                specials.append((str(special_item[0]), str(special_item[1])))

    for label_key, raw_code in specials:
        code = _normalize_display_lang_code(raw_code)
        label = tr(label_key).strip() or str(raw_code)
        if code and code not in seen:
            items.append((code, label))
            seen.add(code)

    rows: list[tuple[str, str]] = []
    for raw_code in list(codes or []):
        code = _normalize_display_lang_code(raw_code)
        if not code or code in seen:
            continue
        label = language_display_name(code, ui_lang=ui_lang)
        if not label:
            continue
        rows.append((code, label))
        seen.add(code)

    rows.sort(key=lambda row: (row[1].lower(), row[0]))
    items.extend(rows)
    return items
