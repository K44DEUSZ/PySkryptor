# app/view/components/runtime_badge.py
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from app.controller.support.localization import tr
from app.view.ui_config import ui

_VALID_RUNTIME_STATES = {"ready", "loading", "offline", "disabled", "missing", "neutral"}

class RuntimeBadgeWidget(QtWidgets.QFrame):
    """Compact runtime information badge for the Files tab."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        cfg = ui(self)
        row_spacing = int(getattr(cfg, "inline_spacing", cfg.spacing))
        self.setObjectName("RuntimeBadgeWidget")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFrameShadow(QtWidgets.QFrame.Plain)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(cfg.margin, cfg.margin, cfg.margin, cfg.margin)
        layout.setSpacing(int(cfg.spacing))

        self.ico = QtWidgets.QLabel()
        self.ico.setFixedSize(18, 18)
        self.ico.setObjectName("RuntimeSummaryIcon")

        self.lbl_summary_label = QtWidgets.QLabel(tr("files.runtime.status_label"))
        self.lbl_summary_label.setObjectName("RuntimeSummaryLabel")

        self.lbl_summary_value = QtWidgets.QLabel(tr("files.runtime.status_loading"))
        self.lbl_summary_value.setObjectName("RuntimeSummaryValue")
        self.lbl_summary_value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        summary_box = QtWidgets.QHBoxLayout()
        summary_box.setContentsMargins(0, 0, 0, 0)
        summary_box.setSpacing(int(cfg.spacing))
        summary_box.addWidget(self.ico, 0, QtCore.Qt.AlignVCenter)
        summary_box.addWidget(self.lbl_summary_label, 0, QtCore.Qt.AlignVCenter)
        summary_box.addWidget(self.lbl_summary_value, 0, QtCore.Qt.AlignVCenter)
        summary_box.addStretch(1)

        self.lbl_device_label, self.lbl_device_value = self._build_line(
            label_key="files.runtime.device_label",
            label_object_name="RuntimeDeviceLabel",
            value_object_name="RuntimeDeviceValue",
        )
        self.lbl_asr_label, self.lbl_asr_value = self._build_line(
            label_key="files.runtime.asr_label",
            label_object_name="RuntimeAsrLabel",
            value_object_name="RuntimeAsrValue",
        )
        self.lbl_translation_label, self.lbl_translation_value = self._build_line(
            label_key="files.runtime.translation_label",
            label_object_name="RuntimeTranslationLabel",
            value_object_name="RuntimeTranslationValue",
        )
        self.lbl_network_label, self.lbl_network_value = self._build_line(
            label_key="files.runtime.network_label",
            label_object_name="RuntimeNetworkLabel",
            value_object_name="RuntimeNetworkValue",
        )

        details_grid = QtWidgets.QGridLayout()
        details_grid.setContentsMargins(0, 0, 0, 0)
        details_grid.setHorizontalSpacing(row_spacing)
        details_grid.setVerticalSpacing(int(cfg.option_spacing))
        details_grid.setColumnStretch(0, 0)
        details_grid.setColumnStretch(1, 1)

        details_grid.addWidget(self.lbl_device_label, 0, 0)
        details_grid.addWidget(self.lbl_device_value, 0, 1)
        details_grid.addWidget(self.lbl_asr_label, 1, 0)
        details_grid.addWidget(self.lbl_asr_value, 1, 1)
        details_grid.addWidget(self.lbl_translation_label, 2, 0)
        details_grid.addWidget(self.lbl_translation_value, 2, 1)
        details_grid.addWidget(self.lbl_network_label, 3, 0)
        details_grid.addWidget(self.lbl_network_value, 3, 1)

        layout.addLayout(summary_box)
        layout.addLayout(details_grid)

        self._set_line_state(self.lbl_summary_value, "loading")
        self._set_line_state(self.lbl_device_value, "neutral")
        self._set_line_state(self.lbl_asr_value, "neutral")
        self._set_line_state(self.lbl_translation_value, "neutral")
        self._set_line_state(self.lbl_network_value, "neutral")

    @staticmethod
    def _normalize_state(state: str) -> str:
        raw = str(state or "").strip().lower()
        return raw if raw in _VALID_RUNTIME_STATES else "neutral"

    @staticmethod
    def _set_line_state(label: QtWidgets.QLabel, state: str) -> None:
        norm = RuntimeBadgeWidget._normalize_state(state)
        if str(label.property("runtimeState") or "") == norm:
            return
        label.setProperty("runtimeState", norm)
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.update()

    @staticmethod
    def _build_line(
        *,
        label_key: str,
        label_object_name: str,
        value_object_name: str,
    ) -> tuple[QtWidgets.QLabel, QtWidgets.QLabel]:
        label = QtWidgets.QLabel(tr(label_key))
        label.setObjectName(label_object_name)
        label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        value = QtWidgets.QLabel(tr("common.na"))
        value.setObjectName(value_object_name)
        value.setWordWrap(True)
        value.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        value.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        value.setMinimumWidth(0)

        return label, value

    def set_summary_status(self, text: str, *, state: str) -> None:
        self.lbl_summary_value.setText(str(text or ""))
        self._set_line_state(self.lbl_summary_value, state)

    def set_summary_icon(self, icon: QtGui.QIcon) -> None:
        self.ico.setPixmap(icon.pixmap(18, 18))

    @staticmethod
    def _set_value_text(label: QtWidgets.QLabel, text: str) -> None:
        value = str(text or "")
        label.setText(value)
        label.setToolTip("")

    def set_device_value(self, text: str) -> None:
        self._set_value_text(self.lbl_device_value, text)
        self._set_line_state(self.lbl_device_value, "neutral")

    def set_asr_value(self, text: str, *, state: str) -> None:
        self._set_value_text(self.lbl_asr_value, text)
        self._set_line_state(self.lbl_asr_value, state)

    def set_translation_value(self, text: str, *, state: str) -> None:
        self._set_value_text(self.lbl_translation_value, text)
        self._set_line_state(self.lbl_translation_value, state)

    def set_network_value(self, text: str, *, state: str) -> None:
        self._set_value_text(self.lbl_network_value, text)
        self._set_line_state(self.lbl_network_value, state)
