# app/view/support/status_presenter.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.model.core.runtime.localization import tr

_PROGRESS_STATUS_KEYS = frozenset(
    {
        "status.downloading",
        "status.transcribing",
        "status.translating",
        "status.postprocessing",
    }
)

_TERMINAL_STATUS_KEYS = frozenset(
    {
        "status.done",
        "status.saved",
        "status.skipped",
        "status.error",
        "status.cancelled",
    }
)

_ACTIVE_WORK_STATUS_KEYS = frozenset(
    {
        "status.processing",
        "status.probing",
        "status.downloading",
        "status.transcribing",
        "status.translating",
        "status.postprocessing",
        "status.saving",
    }
)

_DEFAULT_RUNTIME_ICON_NAMES = {
    "ready": "status_ready",
    "disabled": "status_info",
    "error": "status_error",
    "missing": "status_error",
    "loading": "status_loading",
}


@dataclass(frozen=True)
class RuntimePresentation:
    """Resolved runtime UI state shared across panels and widgets."""

    text: str
    state: str
    tooltip: str = ""
    status_key: str = ""
    icon_name: str = ""


def normalize_status_base_key(status: str) -> str:
    """Strip transient progress suffixes from a status key."""
    try:
        return re.sub(r"\s*\(\d+%\)\s*$", "", str(status or "")).strip()
    except (TypeError, ValueError):
        return str(status or "").strip()


def is_terminal_status(status_key: str) -> bool:
    """Return whether the status represents terminal work."""
    return normalize_status_base_key(status_key) in _TERMINAL_STATUS_KEYS


def is_progress_status(status_key: str) -> bool:
    """Return whether the status should surface percentage progress."""
    return normalize_status_base_key(status_key) in _PROGRESS_STATUS_KEYS


def is_active_work_status(status_key: str) -> bool:
    """Return whether the status represents ongoing work."""
    return normalize_status_base_key(status_key) in _ACTIVE_WORK_STATUS_KEYS


def status_display_text(status_key: str, fallback: str = "") -> str:
    """Resolve display text for a status key or fallback value."""
    key = normalize_status_base_key(status_key)
    if str(key or "").startswith("status."):
        return tr(key)
    return str(fallback or key or "")


def compose_status_text(status_key: str, pct: int | None = None, *, fallback: str = "") -> str:
    """Compose localized status text with an optional percentage suffix."""
    text = status_display_text(status_key, fallback)
    if text and pct is not None and 0 <= int(pct) < 100 and is_progress_status(status_key):
        return f"{text} ({int(pct)}%)"
    return text


def display_texts_for_statuses(keys: Iterable[str]) -> list[str]:
    """Resolve display labels for a sequence of status keys."""
    out: list[str] = []
    for key in keys:
        text = status_display_text(key, key)
        if text:
            out.append(str(text).strip())
    return out


def build_static_runtime_presentation(
    *,
    text: str,
    state: str,
    tooltip: str = "",
    status_key: str = "",
    icon_name: str = "",
) -> RuntimePresentation:
    """Build a normalized runtime presentation from fixed values."""
    value = str(text or "")
    tip = str(tooltip or value or "")
    return RuntimePresentation(
        text=value,
        state=str(state or "neutral").strip().lower() or "neutral",
        tooltip=tip,
        status_key=str(status_key or "").strip(),
        icon_name=str(icon_name or "").strip(),
    )


def runtime_error_text(
    error_key: str | None,
    error_params: dict[str, Any] | None = None,
    *,
    fallback: str = "",
) -> str:
    """Resolve localized runtime error text with a fallback."""
    key = str(error_key or "").strip()
    if not key:
        return str(fallback or "")

    params = dict(error_params or {})
    try:
        return tr(key, **params)
    except TypeError:
        return tr(key)


def build_runtime_presentation(
    *,
    ready: bool,
    disabled: bool,
    ready_text: str,
    disabled_text: str,
    missing_text: str,
    error_key: str | None = None,
    error_params: dict[str, Any] | None = None,
    ready_status_key: str = "",
    disabled_status_key: str = "",
    missing_status_key: str = "",
    error_status_key: str = "status.error",
    icon_names: dict[str, str] | None = None,
) -> RuntimePresentation:
    """Resolve a runtime presentation from readiness, disabled, and error state."""
    names = dict(_DEFAULT_RUNTIME_ICON_NAMES)
    if isinstance(icon_names, dict):
        names.update({str(k): str(v) for k, v in icon_names.items() if str(k).strip() and str(v).strip()})

    if ready:
        return build_static_runtime_presentation(
            text=str(ready_text or ""),
            state="ready",
            tooltip=str(ready_text or ""),
            status_key=ready_status_key,
            icon_name=names.get("ready", ""),
        )
    if disabled:
        return build_static_runtime_presentation(
            text=str(disabled_text or ""),
            state="disabled",
            tooltip=str(disabled_text or ""),
            status_key=disabled_status_key,
            icon_name=names.get("disabled", ""),
        )
    if str(error_key or "").strip():
        error_text = runtime_error_text(error_key, error_params, fallback=missing_text)
        return build_static_runtime_presentation(
            text=error_text,
            state="error",
            tooltip=error_text,
            status_key=error_status_key,
            icon_name=names.get("error", ""),
        )
    return build_static_runtime_presentation(
        text=str(missing_text or ""),
        state="missing",
        tooltip=str(missing_text or ""),
        status_key=missing_status_key,
        icon_name=names.get("missing", ""),
    )
