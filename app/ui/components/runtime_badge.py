# app/ui/components/runtime_badge.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.core.runtime.localization import tr
from app.platform.hardware_summary import processor_name
from app.ui.support.action_icons import action_icon
from app.ui.support.status_presenter import RuntimePresentation
from app.ui.support.theme_runtime import status_icon
from app.ui.support.widget_effects import repolish_widget
from app.ui.ui_config import ui

_VALID_RUNTIME_STATES = {"ready", "loading", "offline", "disabled", "missing", "neutral", "error"}


class RuntimeBadgeWidget(QtWidgets.QFrame):
  """Shared hardware, network and model information for the application footer."""

  presentation_changed = QtCore.pyqtSignal()

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    cfg = ui(self)
    self.setObjectName("RuntimeBadgeWidget")
    self.setProperty("role", "runtimeBadge")
    self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    self.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)

    layout = QtWidgets.QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
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

    for label in (self.lbl_icon, self.lbl_summary_label, self.lbl_summary_value):
      label.setParent(self)
      label.hide()

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
    self.lbl_cpu_label, self.lbl_cpu_value = self._build_line(
      label_key='desktop.processor', label_object_name='RuntimeCpuLabel', value_object_name='RuntimeCpuValue',
    )
    self.lbl_gpu_label, self.lbl_gpu_value = self._build_line(
      label_key='desktop.graphics_card', label_object_name='RuntimeGpuLabel', value_object_name='RuntimeGpuValue',
    )
    self.lbl_cpu_label.setText(tr('desktop.status.processor'))
    self.lbl_device_label.setText(tr('desktop.status.device'))
    self.lbl_cpu_value.setText(processor_name() or tr('common.na'))

    details_grid = QtWidgets.QGridLayout()
    details_grid.setContentsMargins(0, 0, 0, 0)
    details_grid.setSpacing(12)
    self._detail_icons: list[tuple[QtWidgets.QLabel, str]] = []
    self._model_states: dict[QtWidgets.QLabel, QtWidgets.QLabel] = {}
    lines = (
      (self.lbl_asr_label, self.lbl_asr_value, 'mic'),
      (self.lbl_translation_label, self.lbl_translation_value, 'translate'),
      (self.lbl_cpu_label, self.lbl_cpu_value, 'cpu'),
      (self.lbl_gpu_label, self.lbl_gpu_value, 'gpu'),
      (self.lbl_device_label, self.lbl_device_value, 'settings'),
      (self.lbl_network_label, self.lbl_network_value, 'network'),
    )
    for index, (label, value, name) in enumerate(lines):
      host = QtWidgets.QFrame(self)
      host.setProperty('role', 'runtimeDetailCard')
      host.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
      tile = QtWidgets.QVBoxLayout(host)
      tile.setContentsMargins(16, 14, 16, 14)
      tile.setSpacing(8)
      heading = QtWidgets.QHBoxLayout()
      heading.setSpacing(cfg.space_s)
      icon = QtWidgets.QLabel(host)
      icon.setFixedSize(16, 16)
      icon.setPixmap(action_icon(name).pixmap(16, 16))
      heading.addWidget(icon)
      heading.addWidget(label, 1)
      if index < 2:
        state = QtWidgets.QLabel(tr('desktop.status.loading'), host)
        state.setProperty('role', 'runtimeStatusChip')
        state.setProperty('runtimeState', 'loading')
        heading.addWidget(state)
        value.setProperty('runtimeValueType', 'model')
        self._model_states[value] = state
      tile.addLayout(heading)
      tile.addWidget(value)
      self._detail_icons.append((icon, name))
      details_grid.addWidget(host, index // 2, index % 2)
      details_grid.setColumnStretch(index % 2, 1)
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
    value.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
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
    self.presentation_changed.emit()

  def set_device_value(self, text: str) -> None:
    self.lbl_device_value.setText(str(text or ""))
    self.lbl_device_value.setToolTip("")
    self._set_line_state(self.lbl_device_value, "neutral")
    self.presentation_changed.emit()

  def set_asr_presentation(self, presentation: RuntimePresentation) -> None:
    self._apply_presentation(self.lbl_asr_value, presentation)
    self._update_model_state(self.lbl_asr_value, presentation.state)
    self.presentation_changed.emit()

  def set_translation_presentation(self, presentation: RuntimePresentation) -> None:
    self._apply_presentation(self.lbl_translation_value, presentation)
    self._update_model_state(self.lbl_translation_value, presentation.state)
    self.presentation_changed.emit()

  def _update_model_state(self, value: QtWidgets.QLabel, state: str) -> None:
    label = self._model_states[value]
    state = self._normalize_state(state)
    label.setText(tr('desktop.status.' + state))
    label.setProperty('runtimeState', state)
    repolish_widget(label)

  def set_network_presentation(self, presentation: RuntimePresentation) -> None:
    self._apply_presentation(self.lbl_network_value, presentation)
    self.presentation_changed.emit()

  def set_hardware_devices(self, devices: list[dict]) -> None:
    gpu_names = []
    for device in devices:
      model = str(device.get('DEVICE_MODEL') or '').strip()
      kind = str(device.get('DEVICE_KIND') or '').lower()
      identifier = str(device.get('DEVICE_ID') or '').lower()
      if model and (kind == 'gpu' or identifier.startswith('cuda')):
        if model not in gpu_names:
          gpu_names.append(model)
      elif model and (kind == 'cpu' or identifier == 'cpu'):
        self.lbl_cpu_value.setText(model)
    self.lbl_gpu_value.setText(' / '.join(gpu_names) or tr('common.na'))

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      if hasattr(self, 'lbl_summary_value'):
        state = str(self.lbl_summary_value.property('runtimeState') or 'loading')
        icon_name = 'status_ready' if state == 'ready' else 'status_loading' if state == 'loading' else 'status_info'
        if state in {'missing', 'error'}:
          icon_name = 'status_error'
        self.set_summary_icon(status_icon(icon_name))
        for label, name in getattr(self, '_detail_icons', []):
          label.setPixmap(action_icon(name).pixmap(16, 16))
