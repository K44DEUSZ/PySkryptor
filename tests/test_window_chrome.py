from types import SimpleNamespace

from PyQt6 import QtCore, QtTest, QtWidgets

from app.core.config.config import AppConfig
from app.platform.desktop_lifecycle import DesktopLifecycle
from app.ui.main_window import MainWindow


def dispose(window, qt_app, lifecycle=None):
  if lifecycle is not None:
    lifecycle._quitting = True
    lifecycle._timer.stop()
    lifecycle._status_timer.stop()
    lifecycle._tray.hide()
  window.prepare_shutdown()
  window.hide()
  window.deleteLater()
  qt_app.processEvents()


def make_window():
  window = MainWindow()
  window.live_panel._first_shown = True
  window.live_panel._archive_source = 'Zachowana wypowiedź.'
  window.live_panel._state = window.live_panel.STATE_LISTENING
  shutdowns = []
  coordinator = SimpleNamespace(live=SimpleNamespace(is_running=lambda: True),
                                has_active_work=lambda: True, shutdown=lambda: shutdowns.append(True))
  return window, DesktopLifecycle(window, coordinator), shutdowns


def test_caption_maximizes_restores_and_minimizes_with_native_states(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    window.show()
    original = window.size()
    window.title_bar.btn_maximize.click()
    assert window.isMaximized()
    assert window.title_bar.btn_maximize.toolTip() == 'Przywróć rozmiar okna'
    window.title_bar.btn_maximize.click()
    assert not window.isMaximized() and window.size() == original
    QtTest.QTest.mouseDClick(window.title_bar, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(220, 20))
    assert window.isMaximized()
    window.title_bar.btn_minimize.click()
    assert window.isMinimized()
  finally:
    dispose(window, qt_app)


def test_caption_close_keeps_live_text_when_hiding_to_tray(monkeypatch, qt_app):
  AppConfig.SETTINGS.app['ui']['background']['close_behavior_chosen'] = True
  window, lifecycle, shutdowns = make_window()
  monkeypatch.setattr(lifecycle, '_tray_available', lambda: True)
  try:
    window.show()
    window.title_bar.btn_close.click()
    assert not window.isVisible() and not window._close_prepared
    assert window.live_panel._archive_source == 'Zachowana wypowiedź.' and not shutdowns
    lifecycle.restore_window()
    assert window.isVisible()
  finally:
    dispose(window, qt_app, lifecycle)


def test_caption_close_respects_required_confirmation(monkeypatch, qt_app):
  AppConfig.SETTINGS.app['ui']['background']['close_behavior_chosen'] = True
  AppConfig.SETTINGS.app['ui']['background']['close_to_tray'] = False
  window, lifecycle, shutdowns = make_window()
  dialogs = []

  def cancel(dialog):
    dialogs.append(dialog.windowTitle())
    assert dialog.defaultButton().text() == 'Wróć'
    dialog.defaultButton().click()
    return 0

  monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', cancel)
  try:
    window.show()
    window.title_bar.btn_close.click()
    assert dialogs == ['Zakończyć PySkryptor?']
    assert window.isVisible() and not window._close_prepared and not shutdowns
    assert window.live_panel._archive_source == 'Zachowana wypowiedź.'
  finally:
    dispose(window, qt_app, lifecycle)


def test_runtime_loading_disables_workspace_but_keeps_window_controls(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    window.set_runtime_busy(True)
    assert not window.files_panel.isEnabled()
    assert all(button.isEnabled() for button in (
      window.title_bar.btn_minimize, window.title_bar.btn_maximize, window.title_bar.btn_close,
    ))
    window.set_runtime_busy(False)
    assert window.files_panel.isEnabled()
  finally:
    dispose(window, qt_app)
