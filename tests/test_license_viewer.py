from dataclasses import replace
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtTest

from app.core.config.config import AppConfig
from app.ui import dialogs
from app.ui.main_window import MainWindow


def dispose(window, qt_app):
  window.prepare_shutdown()
  window.hide()
  window.deleteLater()
  qt_app.processEvents()


def test_license_reader_preserves_document_and_reuses_its_window(qt_app, monkeypatch):
  def external_open(*_args):
    raise AssertionError('A local license must remain in the application')

  monkeypatch.setattr(QtGui.QDesktopServices, 'openUrl', external_open)
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    about = window.about_panel
    window.show()
    window.tabs.setCurrentWidget(about)
    qt_app.processEvents()
    about.btn_license.click()
    viewer = about._license_dialog
    assert viewer is not None and viewer.isVisible()
    expected = Path(AppConfig.PATHS.LICENSE_FILE).read_text(encoding='utf-8-sig')
    assert viewer.editors[0].isReadOnly()
    assert viewer.editors[0].toPlainText() == expected
    viewer.ed_search.setText('GNU General Public License')
    viewer.find_next()
    assert viewer.editors[0].textCursor().hasSelection()
    cursor = viewer.editors[0].textCursor()
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
    viewer.editors[0].setTextCursor(cursor)
    viewer.find_next()
    assert viewer.editors[0].textCursor().hasSelection()
    about.btn_notices.click()
    assert about._license_dialog is viewer and viewer.tabs.currentIndex() == 1
    notices = AppConfig.PATHS.INSTALL_ROOT_DIR / 'THIRD_PARTY_NOTICES.txt'
    assert viewer.editors[1].toPlainText() == notices.read_text(encoding='utf-8-sig')
    viewer.activateWindow()
    QtTest.QTest.qWait(40)
    viewer.editors[1].setFocus()
    QtTest.QTest.keyClick(viewer.editors[1], QtCore.Qt.Key.Key_F, QtCore.Qt.KeyboardModifier.ControlModifier)
    qt_app.processEvents()
    assert viewer.ed_search.hasFocus()
    viewer.btn_close.click()
    assert not window._close_prepared
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    qt_app.processEvents()
    assert about._license_dialog is None
    about.btn_license.click()
    assert about._license_dialog is not None and about._license_dialog is not viewer
  finally:
    dispose(window, qt_app)


def test_missing_license_uses_an_application_error(qt_app, tmp_path, monkeypatch):
  errors = []
  monkeypatch.setattr(dialogs, 'show_error', lambda *_args, **kwargs: errors.append(kwargs['message']))
  AppConfig.PATHS = replace(AppConfig.PATHS, LICENSE_FILE=tmp_path / 'missing-license')
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    window.about_panel.btn_license.click()
    assert errors == ['Nie znaleziono pliku LICENSE.']
    assert window.about_panel._license_dialog is None
  finally:
    dispose(window, qt_app)
