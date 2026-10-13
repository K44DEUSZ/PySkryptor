# app/ui/components/hint_popup.py
from __future__ import annotations

import math
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from app.ui.support.popup_host import clamp_popup_geometry, hide_popup_widget
from app.ui.support.widget_effects import (
  configure_floating_popup_surface,
  overlay_edge_gap,
  popup_host_root_margins,
)
from app.ui.ui_config import ui

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
  return max(16, int(cfg.control_min_h) // 3)


def hint_popup() -> "HintPopup":
  global _SHARED_HINT_POPUP
  if _SHARED_HINT_POPUP is None:
    _SHARED_HINT_POPUP = HintPopup()
  return _SHARED_HINT_POPUP


def hide_hint_popup(anchor: QtWidgets.QWidget | None = None) -> None:
  if anchor is not None and _SHARED_HINT_POPUP is not None:
    owner = _SHARED_HINT_POPUP._anchor
    try:
      if owner is None or not (owner is anchor or anchor.isAncestorOf(owner)):
        return
    except RuntimeError:
      pass
  hide_popup_widget(_SHARED_HINT_POPUP)


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
    app = QtWidgets.QApplication.instance()
    if app is None or app.property('ui_shutting_down'):
      return False
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

      if not widget.underMouse():
        event.accept()
        return True
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
      scoped = event_type in {QtCore.QEvent.Type.Leave, QtCore.QEvent.Type.Hide,
                              QtCore.QEvent.Type.Close, QtCore.QEvent.Type.DeferredDelete}
      hide_hint_popup(widget if scoped else None)

    return False


class HintPopup(QtWidgets.QWidget):
  """Rounded hint popup used by info buttons."""

  def __init__(self) -> None:
    super().__init__(None, QtCore.Qt.WindowType.ToolTip | QtCore.Qt.WindowType.FramelessWindowHint)
    self.setWindowFlag(QtCore.Qt.WindowType.WindowTransparentForInput, True)
    self._anchor: QtWidgets.QWidget | None = None

    root = QtWidgets.QVBoxLayout(self)
    root.setContentsMargins(*popup_host_root_margins(self))
    root.setSpacing(0)
    root.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)

    self._body = QtWidgets.QFrame(self)
    configure_floating_popup_surface(self, self._body)

    body_lay = QtWidgets.QVBoxLayout(self._body)
    body_lay.setContentsMargins(12, 10, 12, 10)
    body_lay.setSpacing(0)
    body_lay.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)

    self._label = QtWidgets.QLabel(self._body)
    self._label.setProperty("role", "hintPopupText")
    self._label.setWordWrap(True)
    self._label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    self._label.setMaximumWidth(int(_HINT_MAX_TEXT_W))
    body_lay.addWidget(self._label)

    root.addWidget(self._body)

  def show_for(self, anchor: QtWidgets.QWidget, text: str) -> None:
    self._anchor = anchor
    global_rect = QtCore.QRect(anchor.mapToGlobal(QtCore.QPoint(0, 0)), anchor.size())
    cfg = ui(self)
    self.show_at(
      global_rect.topRight() + QtCore.QPoint(_hint_anchor_gap_x(cfg), max(0, global_rect.height() // 2)),
      text,
      avoid_rect=global_rect,
    )

  def show_for_rect(self, host: QtWidgets.QWidget, rect: QtCore.QRect, text: str) -> None:
    self._anchor = host
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
    avoid_rect: Optional[QtCore.QRect] = None,
  ) -> None:
    text = str(text or '').strip()
    if not text:
      self.hide()
      return
    self._label.ensurePolished()
    screen = QtWidgets.QApplication.screenAt(pos) or QtWidgets.QApplication.primaryScreen()
    maximum = _HINT_MAX_TEXT_W
    if screen is not None:
      maximum = min(maximum, max(80, screen.availableGeometry().width() - 80))
    metrics = self._label.fontMetrics()
    width = min(maximum, max(120, max(metrics.horizontalAdvance(line) for line in text.splitlines())))
    document = QtGui.QTextDocument()
    document.setDefaultFont(self._label.font())
    document.setDocumentMargin(0)
    document.setPlainText(text)
    document.setTextWidth(width)
    self._label.setText(text)
    self._label.setFixedSize(width, math.ceil(document.size().height()) + 2)
    self._body.layout().invalidate()
    self._body.layout().activate()
    self.layout().invalidate()
    self.layout().activate()
    self.resize(self.layout().sizeHint())

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

  def hideEvent(self, event: QtGui.QHideEvent) -> None:
    self._anchor = None
    super().hideEvent(event)


class InfoButton(QtWidgets.QToolButton):
  """Tooltip hint button used next to settings controls."""

  _popup: HintPopup | None = None

  def __init__(self, tooltip: str, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self._tooltip_text = str(tooltip or "").strip()
    self._hovered = False
    self._show_timer = QtCore.QTimer(self)
    self._show_timer.setSingleShot(True)
    self._show_timer.setInterval(180)
    self._show_timer.timeout.connect(self._show_hint)
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
    self._hovered = True
    self._show_timer.start()

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
    if not self._tooltip_text or not self._hovered or not self.isVisible() or not self.window().isActiveWindow():
      return
    self._popup_widget().show_for(self, self._tooltip_text)

  def _hide_hint(self) -> None:
    self._hovered = False
    self._show_timer.stop()
    hide_hint_popup(self)
