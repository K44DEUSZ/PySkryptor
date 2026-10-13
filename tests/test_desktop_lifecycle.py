import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import QtWidgets

from app.core.config.config import AppConfig
from app.platform.desktop_lifecycle import DesktopLifecycle
from app.platform.instance_guard import InstanceGuard


def test_second_process_activates_first_and_stale_lock_is_recovered(tmp_path):
  script = Path(__file__).with_name('instance_probe.py')
  process = subprocess.Popen(
    [sys.executable, str(script), str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
  )
  try:
    assert process.stdout.readline().strip() == 'owned'
    second = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True, timeout=8)
    assert second.returncode == 0 and second.stdout.strip() == 'existing'
    deadline = time.monotonic() + 3
    while not (tmp_path / 'activated').exists() and time.monotonic() < deadline:
      time.sleep(0.02)
    assert (tmp_path / 'activated').read_text() == 'yes'
  finally:
    process.kill()
    process.wait(timeout=5)
    process.stdout.close()
  guard = InstanceGuard(tmp_path)
  try:
    assert guard.acquire()
  finally:
    guard.release()


def test_activation_restores_hidden_minimized_window(qt_app, tmp_path):
  guard = InstanceGuard(tmp_path)
  window = QtWidgets.QWidget()
  window.showMinimized()
  window.hide()
  guard.set_window(window)
  guard.restore_window()
  assert window.isVisible() and not window.isMinimized()
  window.close()


class Window(QtWidgets.QWidget):
  def __init__(self):
    super().__init__()
    self.live_panel = None
    self.closed = False

  def prepare_shutdown(self):
    self.closed = True

  def closeEvent(self, event):
    self.closed = True
    super().closeEvent(event)


def make_lifecycle():
  window = Window()
  coordinator = SimpleNamespace(
    live=SimpleNamespace(is_running=lambda: False), has_active_work=lambda: False, shutdown=lambda: None
  )
  lifecycle = DesktopLifecycle(window, coordinator)
  return window, lifecycle


def dispose(window, lifecycle):
  lifecycle._quitting = True
  lifecycle._timer.stop()
  lifecycle._status_timer.stop()
  lifecycle._tray.hide()
  window.close()
  window.deleteLater()


def test_close_to_tray_does_not_run_close_cleanup(monkeypatch, qt_app):
  AppConfig.SETTINGS.app['ui']['background']['close_behavior_chosen'] = True
  window, lifecycle = make_lifecycle()
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: True)
  window.show()
  window.close()
  assert not window.isVisible()
  assert not window.closed and not lifecycle._quitting
  lifecycle.restore_window()
  assert window.isVisible()
  dispose(window, lifecycle)


def test_missing_tray_requests_quit_instead_of_hiding(monkeypatch, qt_app):
  window, lifecycle = make_lifecycle()
  requested = []
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: False)
  monkeypatch.setattr(lifecycle, 'request_quit', lambda: requested.append(True))
  window.show()
  window.close()
  assert requested == [True] and window.isVisible()
  dispose(window, lifecycle)


def test_minimize_settings_are_independent_from_close_settings(qt_app):
  window, lifecycle = make_lifecycle()
  assert not lifecycle._background_option('minimize_to_tray', False)
  assert lifecycle._background_option('close_to_tray', True)
  AppConfig.SETTINGS.app['ui']['background'] = {'minimize_to_tray': True, 'close_to_tray': False}
  assert lifecycle._background_option('minimize_to_tray', False)
  assert not lifecycle._background_option('close_to_tray', True)
  dispose(window, lifecycle)


def test_first_close_remembers_background_choice_without_stopping_work(monkeypatch, qt_app, tmp_path):
  from dataclasses import replace

  from app.features.settings.service import SettingsService

  AppConfig.PATHS = replace(AppConfig.PATHS, SETTINGS_FILE=tmp_path / 'settings.json')
  service = SettingsService()
  service.restore_defaults()
  window, lifecycle = make_lifecycle()
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: True)
  asked = []

  def choose_background(dialog):
    asked.append(dialog.windowTitle())
    assert dialog.defaultButton().text() == 'Wróć'
    next(button for button in dialog.buttons() if button.text() == 'Pracuj w tle').click()
    return 0

  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', choose_background)
  try:
    window.show()
    window.close()
    assert not window.isVisible() and not window.closed and not lifecycle._quitting
    background = service.load().app['ui']['background']
    assert background['close_behavior_chosen'] and background['close_to_tray']
    assert not background['minimize_to_tray']
    lifecycle.restore_window()
    window.close()
    assert len(asked) == 1
  finally:
    dispose(window, lifecycle)


def test_first_close_quit_is_explicit_and_warns_about_work(monkeypatch, qt_app, tmp_path):
  from dataclasses import replace

  from app.features.settings.service import SettingsService

  AppConfig.PATHS = replace(AppConfig.PATHS, SETTINGS_FILE=tmp_path / 'settings.json')
  service = SettingsService()
  service.restore_defaults()
  window, lifecycle = make_lifecycle()
  lifecycle._coordinator.has_active_work = lambda: True
  window.live_panel = SimpleNamespace(has_unsaved_output=lambda: True)
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: True)
  shutdowns = []
  monkeypatch.setattr(lifecycle, '_begin_shutdown', lambda: shutdowns.append(True))

  def choose_quit(dialog):
    assert 'przerwie' in dialog.text() and 'Niezapisany' in dialog.text()
    next(button for button in dialog.buttons() if button.text() == 'Zakończ program').click()
    return 0

  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', choose_quit)
  try:
    window.show()
    window.close()
    assert shutdowns == [True]
    background = service.load().app['ui']['background']
    assert background['close_behavior_chosen'] and not background['close_to_tray']
  finally:
    dispose(window, lifecycle)


def test_cancel_first_close_preserves_window_and_does_not_remember_choice(monkeypatch, qt_app):
  window, lifecycle = make_lifecycle()
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: True)
  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda dialog: dialog.defaultButton().click())
  try:
    window.show()
    window.close()
    assert window.isVisible() and not window.closed and not lifecycle._asking
    assert not AppConfig.SETTINGS.app['ui']['background']['close_behavior_chosen']
  finally:
    dispose(window, lifecycle)


def test_close_preference_save_failure_keeps_window_and_tasks(monkeypatch, qt_app):
  from app.features.settings.service import SettingsService
  from app.ui import dialogs

  window, lifecycle = make_lifecycle()
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: True)
  errors = []
  monkeypatch.setattr(dialogs, 'show_error', lambda *_args, **kwargs: errors.append(kwargs['message']))

  def failed_save(*_args):
    raise OSError('disk unavailable')

  def choose_background(dialog):
    next(button for button in dialog.buttons() if button.text() == 'Pracuj w tle').click()
    return 0

  monkeypatch.setattr(SettingsService, 'save', failed_save)
  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', choose_background)
  try:
    window.show()
    window.close()
    assert window.isVisible() and not window.closed and not lifecycle._asking
    assert errors and not AppConfig.SETTINGS.app['ui']['background']['close_behavior_chosen']
  finally:
    dispose(window, lifecycle)


def test_tray_distinguishes_recording_from_pause(qt_app):
  window, lifecycle = make_lifecycle()
  lifecycle._coordinator.live.is_running = lambda: True
  window.live_panel = SimpleNamespace(recording_state=lambda: 'paused')
  lifecycle._update_tray_status()
  assert lifecycle._tray.toolTip() == 'PySkryptor: nagrywanie wstrzymane'
  window.live_panel.recording_state = lambda: 'listening'
  lifecycle._update_tray_status()
  assert lifecycle._tray.toolTip() == 'PySkryptor: nagrywanie'
  assert not lifecycle._tray.icon().isNull()
  dispose(window, lifecycle)


def test_quit_dialog_defaults_to_return_and_can_cancel(monkeypatch, qt_app):
  window, lifecycle = make_lifecycle()
  seen = []

  def cancel_dialog(dialog):
    assert dialog.defaultButton().text() == 'Wróć'
    assert dialog.escapeButton() is dialog.defaultButton()
    seen.append(dialog.windowTitle())
    dialog.defaultButton().click()
    return 0

  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', cancel_dialog)
  lifecycle.request_quit()
  assert seen == ['Zakończyć PySkryptor?'] and not lifecycle._quitting
  dispose(window, lifecycle)


def test_confirming_quit_runs_shutdown(monkeypatch, qt_app):
  window, lifecycle = make_lifecycle()
  requested = []

  def confirm_dialog(dialog):
    next(
      button
      for button in dialog.buttons()
      if dialog.buttonRole(button) == QtWidgets.QMessageBox.ButtonRole.DestructiveRole
    ).click()
    return 0

  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', confirm_dialog)
  monkeypatch.setattr(lifecycle, '_begin_shutdown', lambda: requested.append(True))
  lifecycle.request_quit()
  assert requested == [True]
  dispose(window, lifecycle)


def test_canceling_restart_keeps_window_and_current_tasks(monkeypatch, qt_app):
  window, lifecycle = make_lifecycle()

  def cancel(dialog):
    assert dialog.windowTitle() == 'Uruchomić ponownie PySkryptor?'
    dialog.defaultButton().click()
    return 0

  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', cancel)
  lifecycle.request_restart()
  assert not lifecycle._restart_after_shutdown and not lifecycle._quitting
  assert not window.closed
  dispose(window, lifecycle)


def test_windows_installer_mutex_is_held_until_guard_release(tmp_path):
  import ctypes
  from ctypes import wintypes

  import pytest

  if sys.platform != 'win32':
    pytest.skip('Windows installer lifetime')
  kernel = ctypes.WinDLL('kernel32', use_last_error=True)
  kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
  kernel.OpenMutexW.restype = wintypes.HANDLE
  kernel.CloseHandle.argtypes = [wintypes.HANDLE]
  guard = InstanceGuard(tmp_path)
  try:
    assert guard.acquire()
    handle = kernel.OpenMutexW(0x00100000, False, 'Local\\PySkryptorDesktop')
    assert handle
    kernel.CloseHandle(handle)
  finally:
    guard.release()
  assert not kernel.OpenMutexW(0x00100000, False, 'Local\\PySkryptorDesktop')
