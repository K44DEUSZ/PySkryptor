# view/widgets/model_runtime_widget.py
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets


class ModelRuntimeWidget(QtWidgets.QFrame):
    """Compact runtime/model information for the Files tab."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModelRuntimeWidget")
        # This widget is typically hosted inside a QGroupBox.
        # Keep it visually neutral (no own border/background) and compact.
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFrameShadow(QtWidgets.QFrame.Plain)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        self.ico = QtWidgets.QLabel()
        self.ico.setFixedSize(18, 18)

        self.lbl_status = QtWidgets.QLabel("-")
        f = self.lbl_status.font()
        f.setBold(True)
        self.lbl_status.setFont(f)
        self.lbl_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        status_box = QtWidgets.QHBoxLayout()
        status_box.setContentsMargins(0, 0, 0, 0)
        status_box.setSpacing(8)
        status_box.addWidget(self.ico, 0, QtCore.Qt.AlignVCenter)
        status_box.addWidget(self.lbl_status, 0, QtCore.Qt.AlignVCenter)

        status_row = QtWidgets.QWidget()
        status_row_lay = QtWidgets.QHBoxLayout(status_row)
        status_row_lay.setContentsMargins(0, 0, 0, 0)
        status_row_lay.setSpacing(8)
        status_row_lay.addLayout(status_box)
        status_row_lay.addStretch(1)

        self.lbl_device = QtWidgets.QLabel("-")
        self.lbl_device.setWordWrap(True)
        self.lbl_device.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_device.setIndent(2)

        self.lbl_asr = QtWidgets.QLabel("-")
        self.lbl_asr.setWordWrap(True)
        self.lbl_asr.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.lbl_asr.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_asr.setIndent(2)

        self.lbl_translation = QtWidgets.QLabel("-")
        self.lbl_translation.setWordWrap(True)
        self.lbl_translation.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.lbl_translation.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.lbl_translation.setIndent(2)

        lay.addWidget(status_row)
        lay.addWidget(self.lbl_device)
        lay.addWidget(self.lbl_asr)
        lay.addWidget(self.lbl_translation)

    def set_status_text(self, text: str) -> None:
        self.lbl_status.setText(text)

    def set_device_text(self, text: str) -> None:
        self.lbl_device.setText(text)

    def set_asr_text(self, text: str) -> None:
        self.lbl_asr.setText(text)

    def set_translation_text(self, text: str) -> None:
        self.lbl_translation.setText(text)

    def set_status_icon(self, icon: QtGui.QIcon) -> None:
        pm = icon.pixmap(18, 18)
        self.ico.setPixmap(pm)

    # Backwards-compatible aliases
    def set_model_text(self, text: str) -> None:
        self.set_status_text(text)

    def set_mode_text(self, text: str) -> None:
        # Mode is no longer shown in this widget, but keep API stable.
        self.set_translation_text(text)
