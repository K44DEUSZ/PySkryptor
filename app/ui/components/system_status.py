# app/ui/components/system_status.py
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from app.core.runtime.localization import tr
from app.ui.components.action_button import ActionButton
from app.ui.components.app_dialog import AppDialog
from app.ui.components.runtime_badge import RuntimeBadgeWidget
from app.ui.support.action_icons import action_icon
from app.ui.support.dialog_positioning import center_dialog
from app.ui.support.responsive_layout import ResponsiveLayout
from app.ui.support.theme_runtime import apply_windows_dark_titlebar
from app.ui.support.widget_effects import repolish_widget
from app.ui.support.widget_setup import setup_button


class SystemStatusBar(QtWidgets.QFrame):
  """Persistent readiness indicators with separate model and hardware details."""

  def __init__(self, parent: QtWidgets.QWidget) -> None:
    super().__init__(parent)
    self.setObjectName('SystemStatus')
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
    self._badge: RuntimeBadgeWidget | None = None
    self._icons: list[tuple[QtWidgets.QLabel, str]] = []
    self._values: list[QtWidgets.QLabel] = []
    self._titles: list[QtWidgets.QLabel] = []
    row = QtWidgets.QHBoxLayout(self)
    row.setContentsMargins(0, 10, 0, 0)
    row.setSpacing(16)
    for name, key in (('mic', 'transcription'), ('translate', 'translation'), ('network', 'network')):
      host = QtWidgets.QWidget(self)
      line = QtWidgets.QHBoxLayout(host)
      line.setContentsMargins(0, 0, 0, 0)
      line.setSpacing(8)
      icon = QtWidgets.QLabel(host)
      icon.setFixedSize(16, 16)
      icon.setPixmap(action_icon(name).pixmap(16, 16))
      title = QtWidgets.QLabel(tr('desktop.status.' + key), host)
      title.setProperty('role', 'secondaryText')
      value = QtWidgets.QLabel(tr('desktop.status.loading'), host)
      value.setProperty('role', 'runtimeStatusChip')
      value.setProperty('runtimeState', 'loading')
      for widget in (icon, title, value):
        line.addWidget(widget)
      self._icons.append((icon, name))
      self._values.append(value)
      self._titles.append(title)
      row.addWidget(host)
    row.addStretch(1)
    self.btn_details = ActionButton(action_icon('settings'), tr('desktop.status.details'), self)
    setup_button(self.btn_details, min_w=0, min_h=34)
    self.btn_details.setProperty('actionRole', 'quiet')
    self.btn_details.clicked.connect(lambda: self.set_expanded(True))
    row.addWidget(self.btn_details)
    self._dialog = AppDialog(parent.window())
    self._dialog.setObjectName('RuntimeDetailsDialog')
    self._dialog.setWindowTitle(tr('desktop.system_status'))
    self._dialog.setWindowIcon(parent.window().windowIcon())
    self._dialog.setMinimumWidth(680)
    self._dialog.resize(820, 500)
    self.body_layout = QtWidgets.QVBoxLayout(self._dialog)
    self.body_layout.setContentsMargins(24, 20, 24, 20)
    self.body_layout.setSpacing(16)
    heading = QtWidgets.QLabel(tr('desktop.system_status'), self._dialog)
    heading.setProperty('role', 'sectionTitle')
    description = QtWidgets.QLabel(tr('desktop.status.description'), self._dialog)
    description.setWordWrap(True)
    description.setProperty('role', 'secondaryText')
    self.body_layout.addWidget(heading)
    self.body_layout.addWidget(description)
    self._responsive = ResponsiveLayout(self, self._reflow)

  def _reflow(self, width: int) -> None:
    for label in self._titles:
      label.setVisible(width >= 820)
    self.btn_details.setText(tr('desktop.status.details') if width >= 550 else '')
    self.btn_details.setToolTip(tr('desktop.status.details'))
    self.btn_details.setAccessibleName(tr('desktop.status.details'))

  def bind_badge(self, badge: RuntimeBadgeWidget) -> None:
    self._badge = badge
    self.body_layout.addWidget(badge, 1)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close, self._dialog)
    buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Close).setText(tr('about.viewer.close'))
    setup_button(buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Close), min_w=0)
    buttons.rejected.connect(self._dialog.close)
    self.body_layout.addWidget(buttons)
    badge.presentation_changed.connect(self.refresh)
    self.refresh()

  def refresh(self) -> None:
    if self._badge is None:
      return
    sources = (self._badge.lbl_asr_value, self._badge.lbl_translation_value, self._badge.lbl_network_value)
    for index, (source, value) in enumerate(zip(sources, self._values)):
      state = str(source.property('runtimeState') or 'neutral')
      key = 'online' if index == 2 and state == 'ready' else 'checking' if index == 2 and state == 'loading' else state
      value.setText(tr('desktop.status.' + key))
      value.setProperty('runtimeState', state)
      value.setToolTip(source.toolTip() or source.text())
      value.setAccessibleName(value.text() + ': ' + source.text())
      repolish_widget(value)

  def is_expanded(self) -> bool:
    return self._dialog.isVisible()

  def set_expanded(self, expanded: bool, *, animated: bool = True) -> None:
    if expanded:
      self._dialog.show()
      apply_windows_dark_titlebar(self._dialog)
      center_dialog(self._dialog)
      self._dialog.raise_()
      self._dialog.activateWindow()
    else:
      self._dialog.close()

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      for label, name in getattr(self, '_icons', []):
        label.setPixmap(action_icon(name).pixmap(16, 16))
      if hasattr(self, 'btn_details'):
        self.btn_details.setIcon(action_icon('settings'))
