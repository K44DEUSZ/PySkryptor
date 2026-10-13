# app/ui/components/desktop_canvas.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.ui.support.theme_runtime import active_theme_key


class DesktopCanvas(QtWidgets.QWidget):
  """Quiet neutral backdrop shared by the window chrome and workspace."""

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self._background = QtGui.QPixmap()
    self._cache_key = None
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

  def _render_background(self) -> None:
    dpr = self.devicePixelRatioF()
    theme = active_theme_key()
    key = (self.size(), dpr, theme)
    if self._cache_key == key:
      return
    self._cache_key = key
    width, height = self.width(), self.height()
    self._background = QtGui.QPixmap(round(width * dpr), round(height * dpr))
    self._background.setDevicePixelRatio(dpr)
    painter = QtGui.QPainter(self._background)
    painter.fillRect(self.rect(), QtGui.QColor('#131415' if theme == 'dark' else '#FAFAF8'))
    painter.end()

  def paintEvent(self, event: QtGui.QPaintEvent) -> None:
    self._render_background()
    painter = QtGui.QPainter(self)
    painter.drawPixmap(0, 0, self._background)

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() in {QtCore.QEvent.Type.PaletteChange, QtCore.QEvent.Type.StyleChange}:
      self.update()
