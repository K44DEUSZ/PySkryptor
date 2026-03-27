# app/view/components/runtime_badge.py
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

from app.model.services.localization_service import tr
from app.view.support.status_presenter import RuntimePresentation
from app.view.support.widget_effects import repolish_widget
from app.view.ui_config import ui

_VALID_RUNTIME_STATES = {"ready", "loading", "offline", "disabled", "missing", "neutral", "error"}

class RuntimeBadgeWidget(QtWidgets.QFrame):
    """Compact runtime information badge for the Files tab."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        cfg = ui(self)
        row_spacing = int(cfg.space_s)
        self.setObjectName("RuntimeBadgeWidget")
        self.setProperty("role", "runtimeBadge")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFrameShadow(QtWidgets.QFrame.Plain)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(cfg.margin, cfg.margin, cfg.margin, cfg.margin)
        layout.setSpacing(int(cfg.spacing))

        self.lbl_icon = QtWidgets.QLabel()
        self.lbl_icon.setFixedSize(18, 18)
        self.lbl_icon.setObjectName("RuntimeSummaryIcon")

        self.lbl_summary_label = QtWidgets.QLabel(tr("files.runtime.status_label"))
        self.lbl_summary_label.setObjectName("RuntimeSummaryLabel")
        self.lbl_summary_label.setProperty("role", "runtimeLabel")

        self.lbl_summary_value = QtWidgets.QLabel(tr("files.runtime.status_loading"))
        self.lbl_summary_value.setObjectName("RuntimeSummaryValue")
        self.lbl_summary_value.setProperty("role", "runtimeValue")
        self.lbl_summary_value.setProperty("runtimeValueType", "summary")
        self.lbl_summary_value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        summary_box = QtWidgets.QHBoxLayout()
        summary_box.setContentsMargins(0, 0, 0, 0)
        summary_box.setSpacing(int(cfg.spacing))
        summary_box.addWidget(self.lbl_icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        summary_box.addWidget(self.lbl_summary_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        summary_box.addWidget(self.lbl_summary_value, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
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
        details_grid.setVerticalSpacing(int(cfg.space_s))
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
        repolish_widget(label)

    @staticmethod
    def _build_line(
        *,
        label_key: str,
        label_object_name: str,
        value_object_name: str,
    ) -> tuple[QtWidgets.QLabel, QtWidgets.QLabel]:
        label = QtWidgets.QLabel(tr(label_key))
        label.setObjectName(label_object_name)
        label.setProperty("role", "runtimeLabel")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)

        value = QtWidgets.QLabel(tr("common.na"))
        value.setObjectName(value_object_name)
        value.setProperty("role", "runtimeValue")
        value.setWordWrap(True)
        value.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        value.setMinimumWidth(0)

        return label, value

    def set_summary_icon(self, icon: QtGui.QIcon) -> None:
        self.lbl_icon.setPixmap(icon.pixmap(18, 18))

    @staticmethod
    def _apply_presentation(label: QtWidgets.QLabel, presentation: RuntimePresentation) -> None:
        label.setText(str(presentation.text or ""))
        label.setToolTip(str(presentation.tooltip or ""))
        RuntimeBadgeWidget._set_line_state(label, presentation.state)

    def set_summary_presentation(self, presentation: RuntimePresentation) -> None:
        self._apply_presentation(self.lbl_summary_value, presentation)

    def set_device_value(self, text: str) -> None:
        self.lbl_device_value.setText(str(text or ""))
        self.lbl_device_value.setToolTip("")
        self._set_line_state(self.lbl_device_value, "neutral")

    def set_asr_presentation(self, presentation: RuntimePresentation) -> None:
        self._apply_presentation(self.lbl_asr_value, presentation)

    def set_translation_presentation(self, presentation: RuntimePresentation) -> None:
        self._apply_presentation(self.lbl_translation_value, presentation)

    def set_network_presentation(self, presentation: RuntimePresentation) -> None:
        self._apply_presentation(self.lbl_network_value, presentation)
