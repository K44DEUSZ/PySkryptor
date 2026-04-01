# app/view/support/audio_track_labels.py
from __future__ import annotations

from typing import Any, Iterable

from app.model.core.runtime.localization import current_language, language_display_name, tr
from app.model.core.utils.string_utils import normalize_lang_code


def _normalize_track_role(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "descriptive" in text:
        return "descriptive"
    if "original" in text:
        return "original"
    if "default" in text:
        return "default"
    return ""


def _track_role_text(role: str) -> str:
    normalized = _normalize_track_role(role)
    if normalized == "original":
        return tr("down.select.audio_track.role_original")
    if normalized == "descriptive":
        return tr("down.select.audio_track.role_descriptive")
    return ""


def _language_label(lang_code: str) -> str:
    normalized = normalize_lang_code(lang_code, drop_region=False) or ""
    if not normalized:
        return ""
    if "-" not in normalized:
        return language_display_name(normalized, ui_lang=current_language())

    base_code = normalized.split("-", 1)[0].strip()
    if not base_code:
        return normalized

    base_label = language_display_name(base_code, ui_lang=current_language())
    base_name = str(base_label or base_code).split("(", 1)[0].strip() or base_code
    return f"{base_name} ({normalized})"


def _track_base_label(track: dict[str, Any], *, fallback_text: str) -> str:
    lang_label = _language_label(str(track.get("lang_code") or ""))
    if not lang_label:
        return str(fallback_text or tr("down.select.audio_track.default")).strip()

    role_text = _track_role_text(str(track.get("role") or ""))
    if role_text:
        return f"{lang_label} — {role_text}"
    return lang_label


def build_audio_track_display_map(
    tracks: Iterable[dict[str, Any]] | None,
    *,
    fallback_text: str | None = None,
) -> dict[str, str]:
    """Build localized display labels for one audio-track list."""

    rows = [track for track in list(tracks or []) if isinstance(track, dict)]
    resolved_fallback = str(fallback_text or tr("down.select.audio_track.default")).strip()

    final_labels: dict[str, str] = {}
    for track in rows:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue
        final_labels[track_id] = _track_base_label(track, fallback_text=resolved_fallback)

    return final_labels
