# app/view/ui_config.py
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from PyQt5 import QtWidgets


@dataclass(frozen=True)
class UIConfig:
    """Immutable UI sizing and spacing tokens shared across the application."""
    window_default_w: int = 1380
    window_default_h: int = 820
    window_min_w: int = 1280
    window_min_h: int = 720

    margin: int = 8
    space_l: int = 9
    space_m: int = 7
    space_s: int = 5

    control_min_h: int = 32
    control_min_w: int = 120
    button_big_h: int = 46
    button_min_w: int = 140

    option_row_min_h: int = 24
    table_check_indicator_size: int = 16
    radius_l: int = 10
    radius_m: int = 8
    radius_s: int = 6
    pad_x_m: int = 10
    pad_x_l: int = 12
    pad_y_s: int = 5
    pad_y_m: int = 6
    pad_y_l: int = 8

    dialog_min_w: int = 560
    dialog_max_w: int = 760

    floating_shadow_blur: int = 18
    floating_shadow_offset_y: int = 4
    floating_shadow_margin: int = 8

    live_render_interval_ms: int = 100
    progress_anim_interval_ms: int = 33
    progress_anim_small_delta_threshold: int = 8
    progress_anim_divisor: int = 6
    progress_text_active_threshold_pct: int = 48

    spectrum_min_h: int = 46
    spectrum_bar_count: int = 18
    spectrum_anim_interval_ms: int = 33

    @property
    def spacing(self) -> int:
        return int(self.space_m)

_DEFAULT_UI = UIConfig()


def _coerce_cfg(obj: Any) -> UIConfig | None:
    if obj is None:
        return None
    if isinstance(obj, UIConfig):
        return obj

    keys = {f.name for f in fields(UIConfig)}
    data: dict[str, Any] = {}

    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                data[key] = obj.get(key)
    else:
        for key in keys:
            if hasattr(obj, key):
                data[key] = getattr(obj, key)

    if not data:
        return None

    merged = {key: data.get(key, getattr(_DEFAULT_UI, key)) for key in keys}
    try:
        return UIConfig(**merged)
    except (TypeError, ValueError):
        return None


def ui(widget: QtWidgets.QWidget | None) -> UIConfig:
    """Resolve the nearest available UIConfig for a widget or the application."""
    w = widget
    while w is not None:
        if hasattr(w, "ui_config"):
            try:
                cfg = _coerce_cfg(w.ui_config())  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                cfg = None
            if cfg is not None:
                return cfg
        w = w.parentWidget()

    app = QtWidgets.QApplication.instance()
    cfg = _coerce_cfg(app.property("ui_config") if app is not None else None)
    return cfg if cfg is not None else _DEFAULT_UI
