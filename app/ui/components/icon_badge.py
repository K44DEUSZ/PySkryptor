# app/ui/components/icon_badge.py
from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtSvg, QtWidgets

from app.ui.support.action_icons import action_icon
from app.ui.ui_config import ui


class IconBadge(QtWidgets.QWidget):
  """Original SVG mark or a plain section icon."""

  def __init__(
    self, name: str, *, tone: str = 'transcription', image_path: Path | None = None,
    parent: QtWidgets.QWidget | None = None,
  ) -> None:
    super().__init__(parent)
    self._name = name
    self._tone = tone
    self._renderer = QtSvg.QSvgRenderer(str(image_path)) if image_path is not None else None
    size = 48 if image_path is not None else 24
    self.setFixedSize(size, size)

  def paintEvent(self, event: QtGui.QPaintEvent) -> None:
    painter = QtGui.QPainter(self)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    radius = ui(self).radius_m
    if self._renderer is not None:
      clip = QtGui.QPainterPath()
      clip.addRoundedRect(QtCore.QRectF(self.rect()), radius, radius)
      painter.setClipPath(clip)
      self._renderer.render(painter, QtCore.QRectF(self.rect()))
      return
    action_icon(self._name).paint(painter, self.rect().adjusted(3, 3, -3, -3))
