import pytest
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from app.ui.main_window import MainWindow
from app.ui.support.dialog_positioning import install_dialog_positioning
from app.ui.support.popup_host import PopupHostBinding
from app.ui.support.status_presenter import RuntimePresentation


@pytest.mark.parametrize('event_kind', ['wheel', 'mouse'])
def test_open_popup_dismisses_input_delivered_to_a_window(qt_app, monkeypatch, event_kind):
  popup = QtWidgets.QWidget()
  popup.show()
  owner = QtCore.QObject()
  binding = PopupHostBinding(owner)
  locations = []
  monkeypatch.setattr(QtWidgets.QApplication, 'widgetAt', lambda point: locations.append(point) or None)
  event = QtGui.QWheelEvent(
    QtCore.QPointF(0, 0), QtCore.QPointF(300, 250), QtCore.QPoint(), QtCore.QPoint(0, 120),
    QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
    QtCore.Qt.ScrollPhase.NoScrollPhase, False,
  )
  if event_kind == 'mouse':
    event = QtGui.QMouseEvent(
      QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(0, 0), QtCore.QPointF(300, 250),
      QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier,
    )
  try:
    binding.handle_dismiss_event(
      obj=owner, event=event, popup_widget=popup, hide_popup=popup.hide,
      contains_widget=lambda widget: widget is popup,
    )
    assert locations == [QtCore.QPoint(300, 250)] and not popup.isVisible()
  finally:
    popup.close()
    popup.deleteLater()


def test_message_and_modeless_dialogs_center_after_layout(qt_app):
  install_dialog_positioning(qt_app)
  owner = QtWidgets.QWidget()
  owner.resize(300, 200)
  owner.move(20, 30)
  owner.show()
  for dialog in (QtWidgets.QDialog(owner), QtWidgets.QMessageBox(owner)):
    try:
      dialog.resize(320, 180)
      if isinstance(dialog, QtWidgets.QMessageBox):
        dialog.setText('Sprawdzenie położenia okna po ułożeniu treści.')
      dialog.show()
      QtTest.QTest.qWait(20)
      expected = owner.screen().availableGeometry().center()
      actual = dialog.frameGeometry().center()
      assert (actual - expected).manhattanLength() <= 2
      dialog.move(25, 25)
      dialog.hide()
      dialog.show()
      QtTest.QTest.qWait(20)
      assert (dialog.frameGeometry().center() - expected).manhattanLength() <= 2
    finally:
      dialog.close()
      dialog.deleteLater()
  owner.close()
  owner.deleteLater()


def test_status_bar_shows_both_models_and_network_without_compressing_workspace(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    window.show()
    qt_app.processEvents()
    badge = window.files_panel.model_info
    badge.set_asr_presentation(RuntimePresentation('Whisper', 'ready'))
    badge.set_translation_presentation(RuntimePresentation('Model nie odpowiedział', 'error'))
    badge.set_network_presentation(RuntimePresentation('Offline', 'offline'))
    before = window.tabs.geometry()
    values = window.system_status._values
    assert [label.text() for label in values] == ['Gotowy', 'Błąd', 'Offline']
    assert values[1].toolTip() == 'Model nie odpowiedział'
    window.system_status.btn_details.click()
    qt_app.processEvents()
    assert window.system_status.is_expanded()
    assert window.tabs.geometry() == before
    assert badge.lbl_asr_value.isVisible() and badge.lbl_cpu_value.isVisible()
    assert badge.lbl_asr_value.text() == 'Whisper'
    window.system_status.set_expanded(False)
    assert not window._close_prepared and not window.system_status.is_expanded()
  finally:
    window.prepare_shutdown()
    window.hide()
    window.deleteLater()
    qt_app.processEvents()


def test_remembered_close_choice_preserves_other_unsaved_settings(qt_app):
  from dataclasses import replace

  from app.core.config.config import AppConfig

  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    settings = window.settings_panel
    settings.on_settings_loaded(AppConfig.SETTINGS)
    settings.cmb_app_language.setCurrentIndex(settings.cmb_app_language.findData('en'))
    snapshot = replace(AppConfig.SETTINGS, app={
      **AppConfig.SETTINGS.app, 'ui': {**AppConfig.SETTINGS.app['ui'], 'background': {
        'minimize_to_tray': False, 'close_to_tray': False, 'close_behavior_chosen': True,
      }},
    })
    settings.apply_close_behavior(snapshot)
    assert settings.cmb_app_language.currentData() == 'en'
    assert not settings.tg_close_to_tray.isChecked()
    assert settings._dirty and settings._collect_payload()['app']['ui']['background']['close_behavior_chosen']
  finally:
    window.prepare_shutdown()
    window.hide()
    window.deleteLater()
    qt_app.processEvents()
