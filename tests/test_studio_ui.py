import copy
import re
from dataclasses import replace
from pathlib import Path

from PyQt6 import QtCore, QtTest, QtWidgets

from app.core.config.config import AppConfig
from app.runtime.manager import EngineManager
from app.ui.components.action_button import ActionButton
from app.ui.components.collapsible_section import CollapsibleSection
from app.ui.main_window import MainWindow
from app.ui.support.action_icons import action_icon
from app.ui.support.theme_runtime import (
  LogoSvgLabel,
  _ui_style_tokens,
  apply_application_theme,
  logo_svg_path,
  theme_style_tokens,
)
from app.ui.ui_config import UIConfig


def dispose(window, qt_app):
  window.prepare_shutdown()
  window.hide()
  window.deleteLater()
  qt_app.processEvents()


def test_model_and_app_edits_share_save_and_undo(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    settings = window.settings_panel
    settings.on_settings_loaded(AppConfig.SETTINGS)
    original = copy.deepcopy(settings._collect_payload())
    settings.cmb_app_language.setCurrentIndex(settings.cmb_app_language.findData('en'))
    settings.cmb_transcription_profile.setCurrentIndex(settings.cmb_transcription_profile.findData('fast'))
    updated = settings._collect_payload()
    assert updated['app']['language'] == 'en'
    assert updated['model']['transcription_model']['profile'] == 'fast'
    assert all(button.isEnabled() for button, name in settings._model_action_buttons if name == 'save')
    settings._on_undo_clicked()
    assert settings._collect_payload() == original
    assert not settings.btn_save.isEnabled()
    assert all(not button.isEnabled() for button, name in settings._model_action_buttons if name == 'save')
    settings.chk_models_advanced.setChecked(True)
    assert settings.chk_show_advanced.isChecked()
  finally:
    dispose(window, qt_app)


def test_theme_change_preserves_live_text_and_does_not_reload_models(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    live = window.live_panel
    live._archive_source = 'Zachowaj rozpoznaną wypowiedź.'
    live._archive_target = 'Preserve the recognised speech.'
    live._state = live.STATE_LISTENING
    original = AppConfig.SETTINGS
    changed = replace(original, app={**original.app, 'theme': 'light'})
    assert not EngineManager.settings_require_reload(original, changed)
    apply_application_theme('dark', animate=False)
    assert window._brand_logo._path.name == 'wordmark_dark.svg'
    apply_application_theme('light', animate=False)
    assert window._brand_logo._path.name == 'wordmark_light.svg'
    assert window.about_panel._logo._path.name == 'logo_light.svg'
    assert live._state == live.STATE_LISTENING
    assert live._archive_source == 'Zachowaj rozpoznaną wypowiedź.'
    assert live._archive_target == 'Preserve the recognised speech.'
  finally:
    dispose(window, qt_app)


def test_disclosure_keeps_selected_formats_and_keyboard_access(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    files = window.files_panel
    files._out_checks['json'].setChecked(True)
    assert files.formats_disclosure.is_expanded()
    assert not files.formats_disclosure.body.isHidden()
    files.formats_disclosure.set_expanded(False, animated=False)
    assert files._out_checks['json'].isChecked()
    assert len(files._out_checks) == 7
    disclosure = CollapsibleSection('Szczegóły', window)
    label = QtWidgets.QLabel('Treść', disclosure.body)
    disclosure.body_layout.addWidget(label)
    disclosure.btn_toggle.click()
    assert disclosure.is_expanded()
    disclosure.btn_toggle.click()
    assert not disclosure.is_expanded()
  finally:
    dispose(window, qt_app)


def test_light_navigation_has_readable_contrast():
  tokens = theme_style_tokens('light')

  def luminance(value):
    channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

  bright, dark = sorted((luminance(tokens['@NAV_ACTIVE_BG@']), luminance(tokens['@NAV_ACTIVE_TEXT@'])), reverse=True)
  assert (bright + 0.05) / (dark + 0.05) >= 7


def test_svg_logo_preserves_composition_at_high_pixel_density(qt_app, monkeypatch):
  logo = LogoSvgLabel(logo_svg_path('light'))
  try:
    monkeypatch.setattr(logo, 'devicePixelRatioF', lambda: 1.0)
    logo.update_for_bounds(180, 72)
    normal = logo.pixmap().toImage()
    monkeypatch.setattr(logo, 'devicePixelRatioF', lambda: 2.0)
    logo.update_for_bounds(180, 72)
    doubled = logo.pixmap().toImage().scaled(
      normal.size(), QtCore.Qt.AspectRatioMode.IgnoreAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation,
    )
    samples = [(x, y) for x in range(0, normal.width(), 3) for y in range(0, normal.height(), 3)]
    differences = sum(abs(normal.pixelColor(x, y).alpha() - doubled.pixelColor(x, y).alpha()) > 64 for x, y in samples)
    assert differences / len(samples) < 0.05
  finally:
    logo.deleteLater()
    qt_app.processEvents()


def test_setting_switch_keyboard_changes_payload_and_undo(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    settings = window.settings_panel
    settings.on_settings_loaded(AppConfig.SETTINGS)
    switch = settings.tg_close_to_tray
    original = switch.isChecked()
    QtTest.QTest.keyClick(switch, QtCore.Qt.Key.Key_Space)
    assert switch.isChecked() != original
    assert settings._collect_payload()['app']['ui']['background']['close_to_tray'] is not original
    assert settings.btn_save.isEnabled()
    settings._on_undo_clicked()
    assert switch.isChecked() == original
  finally:
    dispose(window, qt_app)


def test_queue_identity_keeps_source_and_selection(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    files = window.files_panel
    source = 'C:/Nagrania/wywiad.wav'
    files._insert_placeholder_row(source, source_kind='file', title='wywiad.wav', duration_s=60)
    table = files.tbl_sources
    assert table.text_at(0, files.COL_PATH) == source
    assert table.isColumnHidden(files.COL_PATH)
    table.checkbox_at(0, files.COL_CHECK).setChecked(True)
    assert table.rows_for_removal(files.COL_CHECK) == [0]
    assert table.internal_key_at(0, files.COL_PATH)
    assert table.itemDelegateForColumn(files.COL_TITLE) is not table.itemDelegate()
  finally:
    dispose(window, qt_app)


def test_themed_svg_colors_follow_the_shared_palette():
  assets = Path(__file__).resolve().parents[1] / 'assets'
  checked = 0
  for theme in ('light', 'dark'):
    colors = {value.lower() for value in theme_style_tokens(theme).values() if value.startswith('#')}
    for directory in ('icons', 'images'):
      for path in (assets / directory).rglob(f'*_{theme}.svg'):
        found = set(re.findall(r'#[0-9a-fA-F]{6}\b', path.read_text(encoding='utf-8').lower()))
        assert found <= colors, f'{path.name}: {found - colors}'
        checked += 1
  assert checked > 20


def test_sharp_shape_tokens_do_not_reintroduce_rounding():
  tokens = _ui_style_tokens(replace(UIConfig(), radius_l=0, radius_m=0, radius_s=0, radius_pill=0))
  assert all(tokens[key] == '0px' for key in ('@RADIUS_L@', '@RADIUS_M@', '@RADIUS_S@', '@RADIUS_PILL@'))


def test_custom_action_keeps_visible_svg_and_native_keyboard_activation(qt_app):
  for theme in ('light', 'dark'):
    apply_application_theme(theme, animate=False)
    button = ActionButton(action_icon('add'), '')
    try:
      button.resize(64, 42)
      button.show()
      qt_app.processEvents()
      rendered = button.grab().toImage()
      background = rendered.pixelColor(6, 6)
      ink = [rendered.pixelColor(x, y) for x in range(23, 41) for y in range(12, 30)]
      assert sum(color != background for color in ink) > 12
      clicks = QtTest.QSignalSpy(button.clicked)
      QtTest.QTest.keyClick(button, QtCore.Qt.Key.Key_Space)
      assert len(clicks) == 1
    finally:
      button.hide()
      button.deleteLater()
      qt_app.processEvents()
