# app/ui/components/section_group.py
from __future__ import annotations

from typing import Literal

from PyQt6 import QtWidgets

from app.ui.support.widget_setup import setup_layout
from app.ui.ui_config import ui


class SectionGroup(QtWidgets.QGroupBox):
  """Titleless group box that provides a preconfigured section layout container."""

  root: QtWidgets.QVBoxLayout | QtWidgets.QHBoxLayout | QtWidgets.QGridLayout

  def __init__(
    self,
    parent: QtWidgets.QWidget | None = None,
    *,
    object_name: str | None = None,
    role: str | None = None,
    layout: Literal["vbox", "hbox", "grid"] = "vbox",
  ) -> None:
    super().__init__(parent)
    self.setTitle("")
    self.setProperty("uiTitleless", "true")
    if role is not None:
      self.setProperty("uiRole", role)
    if object_name:
      self.setObjectName(object_name)

    cfg = ui(self)
    if layout == "hbox":
      root = QtWidgets.QHBoxLayout(self)
    elif layout == "grid":
      root = QtWidgets.QGridLayout(self)
    else:
      root = QtWidgets.QVBoxLayout(self)

    setup_layout(root, cfg=cfg)
    self.root = root

  def release_elevation(self) -> None:
    """Release the graphics effect before the native window is destroyed."""
    self.setGraphicsEffect(None)
