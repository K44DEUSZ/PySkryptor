# app/ui/components/boolean_switch.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.ui.support.theme_runtime import active_theme_key, theme_color
from app.ui.ui_config import ui


class BooleanSwitch(QtWidgets.QAbstractButton):
  """Keyboard-accessible setting switch with an animated position and explicit state text."""

  changed = QtCore.pyqtSignal()

  def __init__(
    self, *, checked_text: str, unchecked_text: str, height: int, parent: QtWidgets.QWidget | None = None,
  ) -> None:
    super().__init__(parent)
    self._checked_text = checked_text
    self._unchecked_text = unchecked_text
    self._position = 1.0
    self._dirty_value = False
    self.setCheckable(True)
    self.setChecked(True)
    self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
    self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
    self.setFixedHeight(height)
    self.setMinimumWidth(112)
    self._animation = QtCore.QVariantAnimation(self)
    self._animation.setDuration(140)
    self._animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
    self._animation.valueChanged.connect(self._update_position)
    self.toggled.connect(self._on_toggled)
    self.setText(checked_text)

  def _update_position(self, value: float) -> None:
    self._position = float(value)
    self.update()

  def _on_toggled(self, checked: bool) -> None:
    self.setText(self._checked_text if checked else self._unchecked_text)
    self._animation.stop()
    if self.isVisible():
      self._animation.setStartValue(self._position)
      self._animation.setEndValue(1.0 if checked else 0.0)
      self._animation.start()
    else:
      self._update_position(1.0 if checked else 0.0)
    self.changed.emit()

  def paintEvent(self, event: QtGui.QPaintEvent) -> None:
    theme = active_theme_key()
    painter = QtGui.QPainter(self)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    if not self.isEnabled():
      painter.setOpacity(0.45)
    track = QtCore.QRectF(self.width() - 48, (self.height() - 24) / 2, 46, 24)
    track_color = theme_color(theme, '@ACTIVE_BG@' if self.isChecked() else '@CONTROL_BG@')
    if self.underMouse() and self.isEnabled():
      track_color = track_color.lighter(120)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(track_color)
    painter.drawRoundedRect(track, ui(self).radius_m, ui(self).radius_m)
    painter.setBrush(theme_color(theme, '@BORDER_ACTIVE@' if self.isChecked() else '@TEXT_SECONDARY@'))
    thumb = QtCore.QRectF(track.x() + 4 + self._position * 22, track.y() + 4, 16, 16)
    painter.drawRoundedRect(thumb, ui(self).radius_s, ui(self).radius_s)
    painter.setPen(theme_color(theme, '@TEXT_PRIMARY@' if self._dirty_value else '@TEXT_SECONDARY@'))
    font = self.font()
    font.setWeight(QtGui.QFont.Weight.DemiBold if self._dirty_value else QtGui.QFont.Weight.Normal)
    painter.setFont(font)
    painter.drawText(self.rect().adjusted(0, 0, -60, 0), QtCore.Qt.AlignmentFlag.AlignRight |
                     QtCore.Qt.AlignmentFlag.AlignVCenter, self.text())
    if self.hasFocus():
      painter.setPen(QtGui.QPen(theme_color(theme, '@BORDER_ACTIVE@'), 1))
      painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
      painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), ui(self).radius_m, ui(self).radius_m)

  def set_dirty_value(self, enabled: bool) -> None:
    self._dirty_value = bool(enabled)
    self.update()

  def enterEvent(self, event: QtCore.QEvent) -> None:
    super().enterEvent(event)
    self.update()

  def leaveEvent(self, event: QtCore.QEvent) -> None:
    super().leaveEvent(event)
    self.update()
