# app/view/support/language_options.py
from __future__ import annotations

from collections.abc import Iterable

from app.model.config.app_config import AppConfig as Config
from app.model.config.language_policy import LanguagePolicy
from app.model.helpers.string_utils import normalize_lang_code
from app.model.services.localization_service import build_language_options, language_display_name, tr
from app.model.runtime_resolver import (
    resolve_source_language_for_run,
    resolve_target_language_for_run,
    transcription_language_codes as resolve_source_language_codes,
    translation_language_codes as resolve_target_language_codes,
)

LanguageOption = tuple[str, str]


def supported_source_language_codes() -> list[str]:
    """Return normalized supported transcription source language codes."""
    try:
        return normalized_language_codes(
            resolve_source_language_codes(),
            drop_region=False,
        )
    except (RuntimeError, TypeError, ValueError):
        return []


def supported_target_language_codes() -> list[str]:
    """Return normalized supported translation target language codes."""
    try:
        return normalized_language_codes(
            resolve_target_language_codes(),
            drop_region=True,
        )
    except (RuntimeError, TypeError, ValueError):
        return []


def normalized_language_codes(raw_codes: Iterable[str], *, drop_region: bool) -> list[str]:
    """Normalize, deduplicate and keep language codes in input order."""
    out: list[str] = []
    seen: set[str] = set()
    for code in raw_codes:
        norm = normalize_lang_code(str(code or ""), drop_region=drop_region)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def default_source_language_code(tab_name: str, *, supported: Iterable[str]) -> str:
    """Resolve the configured default source language for a given tab."""
    supported_codes = set(supported)
    resolved = Config.resolve_default_source_language_for_tab(tab_name)
    if LanguagePolicy.is_auto(resolved):
        return LanguagePolicy.AUTO
    return resolved if resolved in supported_codes else LanguagePolicy.AUTO


def resolve_source_language_selection(selection: str | None, *, supported: Iterable[str]) -> str:
    """Resolve a panel source selection while preserving special policy values."""
    supported_codes = set(supported)
    raw = LanguagePolicy.normalize_panel_source_language_selection(selection)
    if LanguagePolicy.is_preferred(raw) or LanguagePolicy.is_auto(raw):
        return raw
    norm = normalize_lang_code(raw, drop_region=False)
    if norm and norm in supported_codes:
        return norm
    return LanguagePolicy.PREFERRED


def resolve_target_language_selection(selection: str | None, *, supported: Iterable[str]) -> str:
    """Resolve a panel target selection while preserving special policy values."""
    raw = LanguagePolicy.normalize_panel_target_language_selection(selection)
    if LanguagePolicy.is_preferred(raw) or LanguagePolicy.is_default_ui(raw):
        return raw
    supported_codes = set(supported)
    return raw if raw in supported_codes else LanguagePolicy.PREFERRED


def effective_source_language_code(tab_name: str, selection: str | None, *, supported: Iterable[str]) -> str:
    """Resolve a panel source selection into the concrete runtime source language."""
    return resolve_source_language_for_run(
        str(tab_name or "").strip().lower(),
        str(selection or LanguagePolicy.PREFERRED),
        supported=supported,
    )


def effective_target_language_code(
    tab_name: str,
    selection: str | None,
    *,
    ui_language: str,
    supported: Iterable[str],
) -> str:
    """Resolve a panel target selection into the concrete runtime target language."""
    return resolve_target_language_for_run(
        str(tab_name or "").strip().lower(),
        str(selection or LanguagePolicy.PREFERRED),
        ui_language=ui_language,
        supported=supported,
    )


def default_source_language_label(tab_name: str, *, supported: Iterable[str], ui_language: str) -> str:
    """Build the user-facing label for the preferred source language option."""
    default_code = default_source_language_code(tab_name, supported=supported)
    if LanguagePolicy.is_auto(default_code):
        base_label = tr("lang.special.auto_detect")
    else:
        base_label = language_display_name(default_code, ui_lang=ui_language)
    base_label = str(base_label or tr("lang.special.auto_detect")).strip()
    return tr("lang.special.preferred_named", name=base_label)


def preferred_target_language_label(tab_name: str, *, supported: Iterable[str], ui_language: str) -> str:
    """Build the user-facing label for the preferred target language option."""
    resolved = effective_target_language_code(
        tab_name,
        LanguagePolicy.PREFERRED,
        ui_language=ui_language,
        supported=supported,
    )
    base_label = language_display_name(resolved, ui_lang=ui_language) if resolved else tr("lang.special.default_ui")
    base_label = str(base_label or tr("lang.special.default_ui")).strip()
    return tr("lang.special.preferred_named", name=base_label)


def build_source_language_items(tab_name: str, *, supported: Iterable[str], ui_language: str) -> list[LanguageOption]:
    """Build source-language combo items with preferred and auto options first."""
    codes = list(supported)
    items: list[LanguageOption] = [
        (LanguagePolicy.PREFERRED, default_source_language_label(tab_name, supported=codes, ui_language=ui_language)),
        (LanguagePolicy.AUTO, tr("lang.special.auto_detect")),
    ]
    items.extend(build_language_options(codes, ui_lang=ui_language))
    return items


def build_target_language_items(tab_name: str, *, supported: Iterable[str], ui_language: str) -> list[LanguageOption]:
    """Build target-language combo items with preferred and UI-language options first."""
    codes = list(supported)
    items: list[LanguageOption] = [
        (LanguagePolicy.PREFERRED, preferred_target_language_label(tab_name, supported=codes, ui_language=ui_language)),
        (LanguagePolicy.DEFAULT_UI, tr("lang.special.default_ui")),
    ]
    items.extend(build_language_options(codes, ui_lang=ui_language))
    return items
