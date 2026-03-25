# app/view/support/settings_mapping.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtWidgets

from app.view.components.choice_toggle import ChoiceToggle
from app.view.components.popup_combo import set_combo_data


def _resolve_field_default(default: Any) -> Any:
    return default() if callable(default) else default


def populate_combo_fields(
    data: dict[str, Any],
    specs: tuple[tuple[str, QtWidgets.QComboBox, Any], ...],
) -> None:
    """Populate combo boxes from a flat settings section."""
    for key, combo, default in specs:
        fallback = _resolve_field_default(default)
        value = data.get(key, fallback)
        if value is None:
            value = fallback
        set_combo_data(combo, str(value), fallback_data=fallback)


def populate_toggle_fields(
    data: dict[str, Any],
    specs: tuple[tuple[str, ChoiceToggle, bool], ...],
) -> None:
    """Populate two-state toggles from a flat settings section."""
    for key, toggle, default in specs:
        toggle.set_first_checked(bool(data.get(key, default)))


def populate_spin_fields(
    data: dict[str, Any],
    specs: tuple[tuple[str, QtWidgets.QSpinBox, int], ...],
) -> None:
    """Populate spin boxes from a flat settings section."""
    for key, spin, default in specs:
        raw = data.get(key, default)
        try:
            spin.setValue(int(raw))
        except (TypeError, ValueError):
            spin.setValue(int(default))


def collect_combo_fields(
    specs: tuple[tuple[str, QtWidgets.QComboBox, Any], ...],
) -> dict[str, Any]:
    """Collect combo box values into a flat payload fragment."""
    payload: dict[str, Any] = {}
    for key, combo, default in specs:
        fallback = _resolve_field_default(default)
        value = combo.currentData()
        payload[key] = fallback if value is None else str(value)
    return payload


def collect_toggle_fields(
    specs: tuple[tuple[str, ChoiceToggle], ...],
) -> dict[str, bool]:
    """Collect two-state toggles into a flat payload fragment."""
    return {key: bool(toggle.is_first_checked()) for key, toggle in specs}


def collect_spin_fields(
    specs: tuple[tuple[str, QtWidgets.QSpinBox], ...],
    *,
    none_if_non_positive: set[str] | None = None,
    none_if_negative: set[str] | None = None,
) -> dict[str, int | None]:
    """Collect spin box values into a flat payload fragment."""
    nullable_non_positive = set(none_if_non_positive or ())
    nullable_negative = set(none_if_negative or ())
    payload: dict[str, int | None] = {}
    for key, spin in specs:
        value = int(spin.value())
        if key in nullable_negative and value < 0:
            payload[key] = None
            continue
        payload[key] = None if key in nullable_non_positive and value <= 0 else value
    return payload
