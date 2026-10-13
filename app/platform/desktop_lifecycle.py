# app/platform/desktop_lifecycle.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtGui, QtWidgets

from app.core.config.config import AppConfig
from app.core.domain.errors import AppError
from app.core.runtime.localization import tr
from app.features.settings.service import SettingsService
from app.ui import dialogs
from app.ui.components.app_dialog import AppMessageBox
from app.ui.support.action_icons import action_icon
from app.ui.support.dialog_positioning import center_dialog

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
  from app.application.coordinators.app_coordinator import AppCoordinator
  from app.ui.main_window import MainWindow


class DesktopLifecycle(QtCore.QObject):
  """Manage window hiding, tray actions and confirmed application shutdown."""

  def __init__(self, window: MainWindow, coordinator: AppCoordinator) -> None:
    super().__init__(QtWidgets.QApplication.instance())
    self._window = window
    self._coordinator = coordinator
    self._quitting = False
    self._restart_after_shutdown = False
    self._asking = False
    self._hide_notice_shown = False
    self._tray = QtWidgets.QSystemTrayIcon(window.windowIcon(), self)
    menu = QtWidgets.QMenu(window)
    self._menu = menu
    menu.addAction(tr('desktop.show'), self.restore_window)
    menu.addSeparator()
    menu.addAction(tr('desktop.quit'), self.request_quit)
    self._tray.setContextMenu(menu)
    self._tray.activated.connect(self._on_tray_activated)
    self._tray.show()
    self._timer = QtCore.QTimer(self)
    self._timer.setInterval(150)
    self._timer.timeout.connect(self._poll_shutdown)
    self._status_timer = QtCore.QTimer(self)
    self._status_timer.setInterval(750)
    self._status_timer.timeout.connect(self._update_tray_status)
    self._status_timer.start()
    window.installEventFilter(self)
    app = QtWidgets.QApplication.instance()
    app.setQuitOnLastWindowClosed(False)
    app.commitDataRequest.connect(self._on_session_shutdown)

  def attach_window(self, window: MainWindow) -> None:
    """Replace the startup surface while keeping one tray and shutdown controller."""
    self._window.removeEventFilter(self)
    self._window = window
    window.installEventFilter(self)
    self._menu.setParent(window, QtCore.Qt.WindowType.Popup)
    self._tray.setIcon(window.windowIcon())

  def _background_option(self, key: str, default: bool) -> bool:
    snapshot = AppConfig.SETTINGS
    config = snapshot.app.get('ui', {}).get('background', {}) if snapshot is not None else {}
    return bool(config.get(key, default))

  def _tray_available(self) -> bool:
    return bool(self._tray.isVisible() and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable())

  def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
    if obj is self._window and not self._quitting:
      if event.type() == QtCore.QEvent.Type.Close:
        event.ignore()
        if self._asking:
          return True
        if not self._background_option('close_behavior_chosen', False) and self._tray_available():
          self._ask_first_close()
        elif self._background_option('close_to_tray', True) and self._tray_available():
          self._hide_window()
        else:
          self.request_quit()
        return True
      if (
        event.type() == QtCore.QEvent.Type.WindowStateChange
        and self._window.isMinimized()
        and self._background_option('minimize_to_tray', False)
        and self._tray_available()
      ):
        QtCore.QTimer.singleShot(0, self._hide_window)
    return super().eventFilter(obj, event)

  def _hide_window(self) -> None:
    if not self._tray_available() or self._quitting:
      return
    self._window.hide()
    if not self._hide_notice_shown:
      self._tray.showMessage('PySkryptor', tr('desktop.background_notice'))
      self._hide_notice_shown = True

  def restore_window(self) -> None:
    self._window.setWindowState(self._window.windowState() & ~QtCore.Qt.WindowState.WindowMinimized)
    self._window.show()
    self._window.raise_()
    self._window.activateWindow()

  def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
    if reason in (
      QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
      QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
    ):
      self.restore_window()

  def _update_tray_status(self) -> None:
    panel = self._window.live_panel
    state = panel.recording_state() if panel is not None else 'stopped'
    if self._coordinator.live.is_running() and state == 'listening':
      key, icon = 'desktop.recording', action_icon('recording')
    elif self._coordinator.live.is_running() and state == 'paused':
      key, icon = 'desktop.paused', action_icon('pause')
    else:
      key, icon = 'desktop.running', self._window.windowIcon()
    self._tray.setToolTip(tr(key))
    self._tray.setIcon(icon)

  def request_quit(self) -> None:
    if self._quitting or self._asking:
      return
    self._asking = True
    dialog = AppMessageBox(self._window)
    prefix = 'desktop.restart' if self._restart_after_shutdown else 'desktop.quit'
    dialog.setWindowTitle(tr(prefix + '_title'))
    dialog.setText(self._shutdown_text(prefix))
    dialog.setIconPixmap(action_icon('info').pixmap(32, 32))
    button_key = 'desktop.restart' if self._restart_after_shutdown else 'desktop.quit'
    quit_button = dialog.addButton(tr(button_key), QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
    back_button = dialog.addButton(tr('desktop.back'), QtWidgets.QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(back_button)
    dialog.setEscapeButton(back_button)
    dialog.adjustSize()
    center_dialog(dialog)
    try:
      dialog.exec()
    finally:
      self._asking = False
    if dialog.clickedButton() is quit_button:
      self._begin_shutdown()

  def _shutdown_text(self, prefix: str = 'desktop.quit') -> str:
    text = tr(prefix + '_active') if self._coordinator.has_active_work() else tr(prefix + '_text')
    has_unsaved_output = getattr(self._window.live_panel, 'has_unsaved_output', lambda: False)
    if has_unsaved_output():
      text += '\n\n' + tr('desktop.unsaved_live_text')
    return text

  def _ask_first_close(self) -> None:
    self._asking = True
    dialog = AppMessageBox(self._window)
    dialog.setWindowTitle(tr('desktop.close_choice.title'))
    text = tr('desktop.close_choice.text')
    if self._coordinator.has_active_work() or getattr(self._window.live_panel, 'has_unsaved_output', lambda: False)():
      text += '\n\n' + self._shutdown_text()
    dialog.setText(text)
    dialog.setIconPixmap(action_icon('info').pixmap(32, 32))
    background_button = dialog.addButton(
      tr('desktop.close_choice.background'), QtWidgets.QMessageBox.ButtonRole.ActionRole,
    )
    quit_button = dialog.addButton(tr('desktop.quit'), QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
    back_button = dialog.addButton(tr('desktop.back'), QtWidgets.QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(back_button)
    dialog.setEscapeButton(back_button)
    dialog.adjustSize()
    center_dialog(dialog)
    try:
      dialog.exec()
      clicked = dialog.clickedButton()
      if clicked not in (background_button, quit_button):
        return
      close_to_tray = clicked is background_button
      if not self._save_close_choice(close_to_tray):
        return
      if close_to_tray:
        self._hide_window()
      else:
        self._begin_shutdown()
    finally:
      self._asking = False

  def _save_close_choice(self, close_to_tray: bool) -> bool:
    settings = getattr(self._coordinator, 'settings', None)
    if settings is not None and settings.is_busy():
      dialogs.show_info(self._window, title=tr('dialog.info.title'), message=tr('desktop.close_choice.saving'))
      return False
    payload = {'app': {'ui': {'background': {
      'close_to_tray': close_to_tray, 'close_behavior_chosen': True,
    }}}}
    service = SettingsService()
    try:
      snapshot = service.save(payload)
      if AppConfig.SETTINGS is not None:
        AppConfig.SETTINGS = service.validate_payload(AppConfig.SETTINGS, payload)
    except (AppError, OSError, ValueError):
      _LOG.error('Saving the window close preference failed.', exc_info=True)
      dialogs.show_error(self._window, title=tr('dialog.error.title'), message=tr('desktop.close_choice.save_failed'))
      return False
    panel = getattr(self._window, 'settings_panel', None)
    if panel is not None:
      panel.apply_close_behavior(snapshot)
    return True

  def request_restart(self) -> None:
    self._restart_after_shutdown = True
    self.request_quit()
    if not self._quitting:
      self._restart_after_shutdown = False

  def _on_session_shutdown(self, _manager: QtGui.QSessionManager) -> None:
    self._restart_after_shutdown = False
    self._begin_shutdown()

  def _begin_shutdown(self) -> None:
    self._quitting = True
    self._window.setProperty('application_closing', True)
    self.restore_window()
    self._window.setEnabled(False)
    self._window.setWindowTitle(tr('desktop.stopping'))
    self._window.prepare_shutdown()
    self._coordinator.shutdown()
    self._timer.start()
    self._poll_shutdown()

  def _poll_shutdown(self) -> None:
    if self._coordinator.has_active_work():
      return
    self._timer.stop()
    self._status_timer.stop()
    self._tray.hide()
    self._window.close()
    app = QtWidgets.QApplication.instance()
    app.setProperty('restart_requested', self._restart_after_shutdown)
    app.quit()
