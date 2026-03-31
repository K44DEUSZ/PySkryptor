# app/view/components/hint_popup.py
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QRect

from app.view.support.popup_host import clamp_popup_geometry, hide_popup_widget
from app.view.support.widget_effects import (
    configure_floating_popup_surface,
    overlay_edge_gap,
    popup_host_root_margins,
)
from app.view.ui_config import ui

_SHARED_HINT_POPUP: HintPopup | None = None
_APP_TOOLTIP_FILTER: _ApplicationTooltipFilter | None = None
_HINT_MAX_TEXT_W = 360


def _hint_anchor_gap_x(cfg) -> int:
    return max(2, int(cfg.space_l) - 1)


def _hint_left_gap_x(cfg) -> int:
    return max(8, int(cfg.pad_x_l) + int(cfg.space_s) - 1)


def _hint_avoid_gap_x(cfg) -> int:
    return int(cfg.pad_x_m)


def _hint_icon_size(cfg) -> int:
    return max(12, int(cfg.radius_l) + 4)


def hint_popup() -> "HintPopup":
    global _SHARED_HINT_POPUP
    if _SHARED_HINT_POPUP is None:
        _SHARED_HINT_POPUP = HintPopup()
    return _SHARED_HINT_POPUP


def hide_hint_popup() -> None:
    hide_popup_widget(hint_popup())


def install_application_tooltip_filter(app: QtWidgets.QApplication | None = None) -> None:
    """Route widget tooltips through the shared hint popup instead of native Qt tooltips."""
    global _APP_TOOLTIP_FILTER
    qt_app = app or QtWidgets.QApplication.instance()
    if not isinstance(qt_app, QtWidgets.QApplication):
        return
    if _APP_TOOLTIP_FILTER is not None:
        return
    _APP_TOOLTIP_FILTER = _ApplicationTooltipFilter(qt_app)
    qt_app.installEventFilter(_APP_TOOLTIP_FILTER)


class _ApplicationTooltipFilter(QtCore.QObject):
    """Show the shared hint popup for standard QWidget tooltip requests."""

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        widget = obj if isinstance(obj, QtWidgets.QWidget) else None
        if widget is None:
            return False

        event_type = event.type()
        if event_type == QtCore.QEvent.Type.ToolTip:
            if bool(widget.property("customHintDisabled")):
                hide_hint_popup()
                return False

            text = str(widget.toolTip() or "").strip()
            if not text:
                hide_hint_popup()
                return False

            hint_popup().show_for(widget, text)
            event.accept()
            return True

        if event_type in {
            QtCore.QEvent.Type.Leave,
            QtCore.QEvent.Type.Hide,
            QtCore.QEvent.Type.Close,
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QEvent.Type.Wheel,
            QtCore.QEvent.Type.WindowDeactivate,
            QtCore.QEvent.Type.ApplicationDeactivate,
            QtCore.QEvent.Type.DeferredDelete,
        }:
            hide_hint_popup()

        return False


class HintPopup(QtWidgets.QWidget):
    """Rounded hint popup used by info buttons."""

    def __init__(self) -> None:
        super().__init__(None, QtCore.Qt.WindowType.ToolTip | QtCore.Qt.WindowType.FramelessWindowHint)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*popup_host_root_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        configure_floating_popup_surface(self, self._body)

        body_lay = QtWidgets.QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        self._label = QtWidgets.QLabel(self._body)
        self._label.setProperty("role", "hintPopupText")
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(int(_HINT_MAX_TEXT_W))
        body_lay.addWidget(self._label)

        root.addWidget(self._body)

    def show_for(self, anchor: QtWidgets.QWidget, text: str) -> None:
        global_rect = QtCore.QRect(anchor.mapToGlobal(QtCore.QPoint(0, 0)), anchor.size())
        cfg = ui(self)
        self.show_at(
            global_rect.topRight() + QtCore.QPoint(_hint_anchor_gap_x(cfg), max(0, global_rect.height() // 2)),
            text,
            avoid_rect=global_rect,
        )

    def show_for_rect(self, host: QtWidgets.QWidget, rect: QtCore.QRect, text: str) -> None:
        global_rect = QtCore.QRect(host.mapToGlobal(rect.topLeft()), rect.size())
        cfg = ui(self)
        self.show_at(
            global_rect.topRight() + QtCore.QPoint(_hint_anchor_gap_x(cfg), max(0, global_rect.height() // 2)),
            text,
            avoid_rect=global_rect,
        )

    def show_at(
        self,
        pos: QtCore.QPoint,
        text: str,
        *,
        avoid_rect: Optional["QRect"] = None,
    ) -> None:
        self._label.setText(str(text or "").strip())
        self.adjustSize()

        geom = self.frameGeometry()
        geom.moveTopLeft(pos)

        screen = QtWidgets.QApplication.screenAt(pos) or QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            cfg = ui(self)
            edge = overlay_edge_gap(cfg)
            avail = screen.availableGeometry()
            if geom.right() > avail.right() - edge:
                left_anchor = pos.x() - geom.width() - _hint_left_gap_x(cfg)
                if avoid_rect is not None:
                    left_anchor = avoid_rect.left() - geom.width() - _hint_avoid_gap_x(cfg)
                geom.moveLeft(max(avail.left() + edge, left_anchor))
            if geom.bottom() > avail.bottom() - edge:
                geom.moveTop(max(avail.top() + edge, avail.bottom() - geom.height() - edge))
            if geom.top() < avail.top() + edge:
                geom.moveTop(avail.top() + edge)
        geom = clamp_popup_geometry(geom, point=pos, fallback_widget=self)

        self.move(geom.topLeft())
        self.show()
        self.raise_()


class InfoButton(QtWidgets.QToolButton):
    """Tooltip hint button used next to settings controls."""

    _popup: HintPopup | None = None

    def __init__(self, tooltip: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._tooltip_text = str(tooltip or "").strip()
        self.setProperty("role", "hint")
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setAutoRaise(True)
        self.setText("")
        cfg = ui(self)
        icon_size = _hint_icon_size(cfg)
        self.setIconSize(QtCore.QSize(int(icon_size), int(icon_size)))

    @classmethod
    def _popup_widget(cls) -> HintPopup:
        popup = hint_popup()
        cls._popup = popup
        return popup

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self._show_hint()

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._hide_hint()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._hide_hint()

    def event(self, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.ToolTip:
            self._show_hint()
            return True
        return super().event(event)

    def _show_hint(self) -> None:
        if not self._tooltip_text:
            return
        self._popup_widget().show_for(self, self._tooltip_text)

    def _hide_hint(self) -> None:
        popup = self.__class__._popup
        hide_popup_widget(popup)
