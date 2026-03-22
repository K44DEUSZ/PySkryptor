# app/view/components/hint_popup.py
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from app.view.support.widget_effects import (
    apply_floating_shadow,
    enable_styled_background,
    floating_shadow_margins,
    overlay_edge_gap,
)
from app.view.ui_config import ui

_SHARED_HINT_POPUP: HintPopup | None = None
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

class HintPopup(QtWidgets.QWidget):
    """Rounded hint popup used by info buttons."""

    def __init__(self) -> None:
        super().__init__(None, QtCore.Qt.WindowType.ToolTip | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setProperty("role", "hintPopupHost")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*floating_shadow_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        self._body.setProperty("role", "hintPopup")
        enable_styled_background(self._body)
        apply_floating_shadow(self._body)

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
        avoid_rect: QtCore.QRect | None = None,
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
        if popup is not None:
            popup.hide()
