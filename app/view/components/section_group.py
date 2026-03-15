# app/view/components/section_group.py
from __future__ import annotations

from typing import Optional, Literal

from PyQt5 import QtWidgets

from app.view.ui_config import setup_layout, ui

class SectionGroup(QtWidgets.QGroupBox):
    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
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
