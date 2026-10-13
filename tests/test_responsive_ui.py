from PyQt6 import QtCore, QtTest

from app.core.config.config import AppConfig
from app.ui.components.popup_combo import LanguageCombo, rebuild_code_combo
from app.ui.main_window import MainWindow
from app.ui.support.theme_runtime import apply_application_theme


def dispose(window, qt_app):
  window.prepare_shutdown()
  window.hide()
  window.deleteLater()
  qt_app.processEvents()


def test_breakpoints_preserve_queue_languages_formats_and_live_text(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    files = window.files_panel
    files._insert_placeholder_row('C:/test/wywiad.wav', source_kind='file', title='wywiad.wav', duration_s=60)
    files._out_checks['json'].setChecked(True)
    window.live_panel._archive_source = 'Treść nagrania zachowana podczas zmiany układu.'
    window.settings_panel.on_settings_loaded(AppConfig.SETTINGS)
    source_code = files.cmb_source_language.code()
    window.show()
    for width, expected_sidebar in ((1600, 208), (1200, 72), (760, 0), (1600, 208)):
      window.tabs.setCurrentWidget(files)
      window.resize(width, 700)
      QtTest.QTest.qWait(30)
      assert window.width() == width
      if expected_sidebar:
        assert window._sidebar.isVisible() and window._sidebar.width() == expected_sidebar
      else:
        assert not window._sidebar.isVisible() and files._compact_options
        files.options_disclosure.set_expanded(True, animated=False)
        assert files.cmb_source_language.isVisible()
      for panel in window._panels.values():
        window.tabs.setCurrentWidget(panel)
        QtTest.QTest.qWait(10)
        assert panel.width() <= window.width()
      assert files._out_checks['json'].isChecked()
      assert files.cmb_source_language.code() == source_code
      assert files.tbl_sources.rowCount() == 1
      assert window.live_panel._archive_source == 'Treść nagrania zachowana podczas zmiany układu.'
  finally:
    dispose(window, qt_app)


def test_sidebar_button_and_shortcut_restore_hidden_navigation(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    window.resize(760, 700)
    window.show()
    QtTest.QTest.qWait(20)
    assert not window._sidebar.isVisible()
    button = window.title_bar.btn_sidebar
    assert not button.icon().isNull()
    assert not window.title_bar.is_caption_point(button.geometry().center())
    button.click()
    assert window._sidebar.isVisible()
    button.click()
    assert not window._sidebar.isVisible()
    window.activateWindow()
    QtTest.QTest.qWait(20)
    window.setFocus()
    QtTest.QTest.keyClick(window, QtCore.Qt.Key.Key_B, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert window._sidebar.isVisible()
  finally:
    dispose(window, qt_app)


def test_download_details_preserve_selected_queue_row_and_fit_short_windows(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  try:
    downloads = window.downloader_panel
    downloads._add_single_job('https://example.invalid/material')
    job = downloads._jobs[-1]
    downloads._prime_job_meta(job.key, title='Wybrany materiał', duration_s=60)
    window.tabs.setCurrentWidget(downloads)
    window.resize(760, 600)
    window.show()
    QtTest.QTest.qWait(30)
    downloads.tbl_queue.selectRow(0)
    downloads.meta_disclosure.set_expanded(True, animated=False)
    QtTest.QTest.qWait(30)
    assert downloads._meta_dialog.isVisible()
    assert not downloads.meta_disclosure.is_expanded()
    assert downloads.lbl_title.property('fullText') == 'Wybrany materiał'
    title_item = downloads.tbl_queue.item(0, downloads.COL_TITLE)
    assert downloads.tbl_queue.viewport().rect().contains(downloads.tbl_queue.visualItemRect(title_item).center())
    downloads._meta_scroll.ensureWidgetVisible(downloads.lbl_thumbnail)
    QtTest.QTest.qWait(20)
    assert not downloads.lbl_thumbnail.visibleRegion().isEmpty()
    downloads._meta_dialog.close()
    assert downloads._meta_scroll.parentWidget() is downloads.meta_disclosure.body
    window.resize(1600, 1000)
    QtTest.QTest.qWait(30)
    downloads.meta_disclosure.set_expanded(True, animated=False)
    QtTest.QTest.qWait(30)
    assert downloads.meta_disclosure.is_expanded() and not downloads._meta_dialog.isVisible()
    assert downloads._selected_job_key() == job.key
    assert downloads.lbl_title.property('fullText') == 'Wybrany materiał'
  finally:
    dispose(window, qt_app)


def test_flags_decorate_choices_without_changing_language_codes_or_selection(qt_app):
  combo = LanguageCombo(codes_provider=lambda: ['pl', 'en', 'ar'])
  try:
    rebuild_code_combo(combo, [('pl', 'polski'), ('en', 'angielski'), ('ar', 'arabski')],
                       desired_code='ar', fallback_code='pl')
    assert combo.code() == 'ar' and [combo.itemData(i) for i in range(combo.count())] == ['pl', 'en', 'ar']
    assert all(not combo.itemIcon(i).isNull() for i in range(combo.count()))
    assert combo.itemIcon(0).cacheKey() != combo.itemIcon(1).cacheKey()
    apply_application_theme('light', animate=False)
    assert combo.code() == 'ar'
  finally:
    combo.hide()
    combo.deleteLater()
    qt_app.processEvents()
