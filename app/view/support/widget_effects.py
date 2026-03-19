# app/view/support/widget_effects.py
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from app.view.ui_config import UIConfig, ui


def enable_styled_background(w: QtWidgets.QWidget) -> None:
    w.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)


def overlay_edge_gap(cfg: UIConfig) -> int:
    return max(4, int(cfg.space_s) + 1)


def install_app_event_filter(owner: QtCore.QObject, *, installed: bool) -> bool:
    app = QtWidgets.QApplication.instance()
    if app is None or installed:
        return bool(installed)
    app.installEventFilter(owner)
    return True


def bind_tracked_window(
    owner: QtCore.QObject,
    tracked_window: QtWidgets.QWidget | None,
    widget: QtWidgets.QWidget | None,
) -> QtWidgets.QWidget | None:
    win = widget.window() if isinstance(widget, QtWidgets.QWidget) else None
    if win is tracked_window:
        return tracked_window if isinstance(tracked_window, QtWidgets.QWidget) else None

    if tracked_window is not None:
        tracked_window.removeEventFilter(owner)

    tracked = win if isinstance(win, QtWidgets.QWidget) else None
    if tracked is not None:
        tracked.installEventFilter(owner)
    return tracked


def contains_widget_chain(widget: QtWidgets.QWidget | None, *roots: QtWidgets.QWidget | None) -> bool:
    valid_roots = [root for root in roots if isinstance(root, QtWidgets.QWidget)]
    current = widget
    while current is not None:
        for root in valid_roots:
            if current is root or root.isAncestorOf(current):
                return True
        current = current.parentWidget()
    return False


def repolish_widget(w: QtWidgets.QWidget | None) -> None:
    if w is None:
        return
    try:
        style = w.style()
        if style is not None:
            style.unpolish(w)
            style.polish(w)
    except Exception:
        pass
    w.update()


def sync_progress_text_role(progress_bar: QtWidgets.QProgressBar) -> None:
    cfg = ui(progress_bar)
    role = "primary"
    maximum = int(progress_bar.maximum())
    minimum = int(progress_bar.minimum())
    if maximum > minimum:
        value = int(progress_bar.value())
        filled = int(round(((value - minimum) * 100.0) / float(maximum - minimum)))
        if filled >= int(cfg.progress_text_accent_threshold_pct):
            role = "accent"
    progress_bar.setProperty("progressTextRole", role)
    repolish_widget(progress_bar)


def apply_floating_shadow(w: QtWidgets.QWidget) -> QtWidgets.QGraphicsDropShadowEffect:
    cfg = ui(w)
    effect = QtWidgets.QGraphicsDropShadowEffect(w)
    effect.setBlurRadius(float(cfg.floating_shadow_blur))
    effect.setOffset(0.0, float(cfg.floating_shadow_offset_y))
    effect.setColor(QtCore.Qt.GlobalColor.transparent)
    try:
        from PyQt5 import QtGui

        effect.setColor(QtGui.QColor(str(cfg.floating_shadow_color)))
    except Exception:
        pass
    w.setGraphicsEffect(effect)
    return effect


def floating_shadow_margins(
    widget: QtWidgets.QWidget | None,
    *,
    extra: int = 0,
) -> tuple[int, int, int, int]:
    cfg = ui(widget)
    base = max(0, int(cfg.floating_shadow_margin))
    bottom = base + max(0, int(cfg.floating_shadow_offset_y)) + max(0, int(extra))
    return base, base, base, bottom
