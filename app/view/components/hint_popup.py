# app/view/components/hint_popup.py
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from app.view.ui_config import apply_floating_shadow, enable_styled_background, floating_shadow_margins, ui

_SHARED_HINT_POPUP: Optional["HintPopup"] = None

def hint_popup() -> "HintPopup":
    global _SHARED_HINT_POPUP
    if _SHARED_HINT_POPUP is None:
        _SHARED_HINT_POPUP = HintPopup()
    return _SHARED_HINT_POPUP

class HintPopup(QtWidgets.QWidget):
    """Rounded hint popup used by info buttons."""

    def __init__(self) -> None:
        super().__init__(None, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
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

        cfg = ui(self)

        self._label = QtWidgets.QLabel(self._body)
        self._label.setProperty("role", "hintPopupText")
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(int(cfg.hint_popup_max_text_w))
        body_lay.addWidget(self._label)

        root.addWidget(self._body)

    def show_for(self, anchor: QtWidgets.QWidget, text: str) -> None:
        global_rect = QtCore.QRect(anchor.mapToGlobal(QtCore.QPoint(0, 0)), anchor.size())
        self.show_at(
            global_rect.topRight() + QtCore.QPoint(int(ui(self).hint_popup_anchor_gap_x), max(0, global_rect.height() // 2)),
            text,
            avoid_rect=global_rect,
        )

    def show_for_rect(self, host: QtWidgets.QWidget, rect: QtCore.QRect, text: str) -> None:
        global_rect = QtCore.QRect(host.mapToGlobal(rect.topLeft()), rect.size())
        self.show_at(
            global_rect.topRight() + QtCore.QPoint(int(ui(self).hint_popup_anchor_gap_x), max(0, global_rect.height() // 2)),
            text,
            avoid_rect=global_rect,
        )

    def show_at(
        self,
        pos: QtCore.QPoint,
        text: str,
        *,
        avoid_rect: Optional[QtCore.QRect] = None,
    ) -> None:
        self._label.setText(str(text or "").strip())
        self.adjustSize()

        geom = self.frameGeometry()
        geom.moveTopLeft(pos)

        screen = QtWidgets.QApplication.screenAt(pos) or QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            cfg = ui(self)
            edge = int(cfg.hint_popup_edge_margin)
            avail = screen.availableGeometry()
            if geom.right() > avail.right() - edge:
                left_anchor = pos.x() - geom.width() - int(cfg.hint_popup_left_gap_x)
                if avoid_rect is not None:
                    left_anchor = avoid_rect.left() - geom.width() - int(cfg.hint_popup_avoid_gap_x)
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

    _popup: Optional[HintPopup] = None

    def __init__(self, tooltip: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._tooltip_text = str(tooltip or "").strip()
        self.setProperty("role", "hint")
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAutoRaise(True)
        self.setText("")
        cfg = ui(self)
        self.setIconSize(QtCore.QSize(int(cfg.hint_icon_size), int(cfg.hint_icon_size)))

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
        if event.type() == QtCore.QEvent.ToolTip:
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
