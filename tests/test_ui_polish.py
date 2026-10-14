from pathlib import Path

import pytest
from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from app.bootstrap import _apply_stylesheet, _load_fonts
from app.core.config.config import AppConfig
from app.core.runtime.localization import load
from app.ui.components.file_dialog import AppFileDialog
from app.ui.components.popup_combo import PopupComboBox
from app.ui.support.language_icons import language_icon


@pytest.mark.parametrize('count', [1, 2, 5])
def test_short_dropdown_fits_the_actual_rows_without_an_empty_lower_area(qt_app, count):
  previous_style = qt_app.styleSheet()
  _load_fonts(qt_app, AppConfig.PATHS.ASSETS_DIR / 'fonts')
  _apply_stylesheet(qt_app, AppConfig.PATHS.STYLES_DIR, 'dark')
  combo = PopupComboBox()
  combo.addItems(['Model ' + str(index) for index in range(count)])
  combo.resize(360, 38)
  combo.show()
  try:
    combo.showPopup()
    QtTest.QTest.qWait(30)
    view = combo._popup._list
    bottom = view.visualRect(view.model().index(count - 1, 0)).bottom() + 1
    assert -2 <= view.viewport().height() - bottom <= 4
  finally:
    combo.hide_popup()
    combo.hide()
    combo.deleteLater()
    qt_app.setStyleSheet(previous_style)


@pytest.mark.parametrize('language', ['pl', 'en'])
def test_folder_picker_uses_the_app_language_and_returns_the_selected_folder(qt_app, tmp_path, language):
  load(AppConfig.PATHS.LOCALES_DIR, language)
  folder = tmp_path / 'Model z polskimi znakami ąę'
  folder.mkdir()
  observations = {}

  def select():
    dialog = qt_app.activeModalWidget()
    observations['caption'] = dialog.title_bar.lbl_caption.text()
    observations['look_in'] = QtCore.QCoreApplication.translate('QFileDialog', 'Look in:')
    observations['location_label'] = dialog.labelText(dialog.DialogLabel.LookIn)
    observations['navigation'] = dialog.findChild(QtWidgets.QToolButton, 'backButton').iconSize()
    dialog.accept()

  QtCore.QTimer.singleShot(30, select)
  path = AppFileDialog.getExistingDirectory(None, 'Wybierz folder modelu', str(folder))
  assert Path(path) == folder
  assert observations['caption'] == 'Wybierz folder modelu'
  assert observations['navigation'] == QtCore.QSize(22, 22)
  assert observations['location_label'] == ('Lokalizacja:' if language == 'pl' else 'Location:')
  assert (observations['look_in'] != 'Look in:') == (language == 'pl')


def test_file_selection_preserves_multiple_files_and_the_chosen_filter(qt_app, tmp_path):
  for name in ('one.wav', 'two.wav'):
    (tmp_path / name).write_bytes(b'fixture')

  def select():
    dialog = qt_app.activeModalWidget()
    dialog.findChild(QtWidgets.QLineEdit, 'fileNameEdit').setText('"one.wav" "two.wav"')
    dialog.accept()

  QtCore.QTimer.singleShot(80, select)
  paths, selected = AppFileDialog.getOpenFileNames(None, 'Audio', str(tmp_path), 'Audio (*.wav)')
  assert {Path(path).name for path in paths} == {'one.wav', 'two.wav'}
  assert selected == 'Audio (*.wav)'


def test_cancelling_file_selection_returns_no_path(qt_app, tmp_path):
  QtCore.QTimer.singleShot(30, lambda: qt_app.activeModalWidget().reject())
  assert AppFileDialog.getSaveFileName(None, 'Zapisz', str(tmp_path / 'text.txt'), 'TXT (*.txt)') == ('', '')


def test_file_picker_keeps_its_selection_and_caption_controls_when_the_theme_changes(qt_app, tmp_path):
  previous_style = qt_app.styleSheet()
  dialog = AppFileDialog(None, 'Model', str(tmp_path))
  dialog.setFileMode(dialog.FileMode.Directory)
  dialog.show()
  try:
    for theme in ('light', 'dark'):
      _apply_stylesheet(qt_app, AppConfig.PATHS.STYLES_DIR, theme)
      QtTest.QTest.qWait(20)
      assert dialog.title_bar.btn_close.size() == QtCore.QSize(38, 30)
      assert Path(dialog.selectedFiles()[0]) == tmp_path
  finally:
    dialog.close()
    dialog.deleteLater()
    qt_app.setStyleSheet(previous_style)


@pytest.mark.parametrize('mode', ['Directory', 'ExistingFile', 'ExistingFiles', 'AnyFile'])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_file_picker_actions_share_a_footer_row_at_minimum_width(qt_app, tmp_path, mode, theme):
  previous_style = qt_app.styleSheet()
  _apply_stylesheet(qt_app, AppConfig.PATHS.STYLES_DIR, theme)
  dialog = AppFileDialog(None, 'Pliki', str(tmp_path), 'Audio (*.wav);;Wszystkie (*)')
  dialog.setFileMode(getattr(dialog.FileMode, mode))
  if mode == 'AnyFile':
    dialog.setAcceptMode(dialog.AcceptMode.AcceptSave)
  dialog.resize(640, 420)
  dialog.show()
  try:
    QtTest.QTest.qWait(30)
    box = dialog.findChild(QtWidgets.QDialogButtonBox)
    buttons = box.buttons()
    assert len(buttons) == 2
    assert abs(buttons[0].geometry().center().y() - buttons[1].geometry().center().y()) <= 1
    assert not buttons[0].geometry().intersects(buttons[1].geometry())
    edit = dialog.findChild(QtWidgets.QLineEdit, 'fileNameEdit')
    assert box.mapTo(dialog, QtCore.QPoint()).y() >= edit.geometry().bottom()
    assert all(button.width() >= 88 for button in buttons)
    assert dialog.width() == 640
  finally:
    dialog.close()
    dialog.deleteLater()
    qt_app.setStyleSheet(previous_style)


def test_every_whisper_and_m2m100_language_has_a_renderable_flag(qt_app):
  codes = (
    'af am ar as ast az ba be bg bn bo br bs ca ceb cs cy da de el en es et eu fa ff fi fo fr fy '
    'ga gd gl gu ha haw he hi hr ht hu hy id ig ilo is it ja jv jw ka kk km kn ko la lb lg ln lo lt lv '
    'mg mi mk ml mn mr ms mt my ne nl nn no ns oc or pa pl ps pt ro ru sa sd si sk sl sn so sq sr ss su '
    'sv sw ta te tg th tk tl tn tr tt uk ur uz vi wo xh yi yo yue zh zu'
  ).split()
  fallback = language_icon('auto').cacheKey()
  for code in codes:
    icon = language_icon(code)
    assert icon.cacheKey() != fallback, f'Missing flag: {code}'
    assert not icon.pixmap(24, 18).isNull(), f'Invalid flag: {code}'


@pytest.mark.parametrize('code, flag', [
  ('en_US', 'us'), ('pt-BR', 'br'), ('zh-Hant', 'tw'), ('zh-Hans', 'cn'),
  ('sr-Latn', 'rs'), ('zh-Hant-HK', 'hk'), ('cy', 'gb-wls'), ('pt-XX', 'pt'),
])
def test_language_flags_resolve_regions_and_scripts_without_losing_base_language(qt_app, code, flag):
  expected = QtGui.QIcon(str(AppConfig.PATHS.ICONS_DIR / 'flags' / f'{flag}.svg'))
  assert language_icon(code).pixmap(24, 18).toImage() == expected.pixmap(24, 18).toImage()
