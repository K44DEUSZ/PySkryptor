from PyQt6 import QtCore, QtTest, QtWidgets

from app.ui.components.action_button import ActionButton
from app.ui.components.app_dialog import AppMessageBox
from app.ui.components.hint_popup import HintPopup, InfoButton, hide_hint_popup, hint_popup
from app.ui.components.section_group import SectionGroup
from app.ui.main_window import MainWindow
from app.ui.support.theme_runtime import apply_application_theme
from app.ui.support.widget_setup import setup_button


def dispose(widget, qt_app):
  if isinstance(widget, MainWindow):
    widget.prepare_shutdown()
  widget.hide()
  widget.deleteLater()
  qt_app.processEvents()


def test_shared_tooltip_remeasures_long_and_short_text_without_retaining_dimensions(qt_app):
  apply_application_theme('dark', animate=False)
  popup = HintPopup()
  try:
    point = QtCore.QPoint(250, 250)
    long_text = 'Dokładniejsze obliczenia potrzebują więcej pamięci. ' * 8
    popup.show_at(point, long_text)
    large = popup.size()
    for _ in range(20):
      popup.show_at(point, 'Wybierz język interfejsu.')
      assert popup.height() < large.height()
      assert popup.width() < large.width()
      assert popup._label.height() >= popup._label.heightForWidth(popup._label.width())
      popup.show_at(point, long_text)
      assert popup.size() == large
  finally:
    dispose(popup, qt_app)


def test_fast_help_hover_cancels_old_requests_and_leaves_only_current_owner(qt_app):
  window = QtWidgets.QWidget()
  row = QtWidgets.QHBoxLayout(window)
  first = InfoButton('Pierwsza wskazówka', window)
  second = InfoButton('Druga wskazówka, dłuższa od pierwszej.', window)
  row.addWidget(first)
  row.addWidget(second)
  window.show()
  window.activateWindow()
  QtTest.QTest.qWait(30)
  try:
    QtTest.QTest.mouseMove(first, first.rect().center())
    QtTest.QTest.mouseMove(second, second.rect().center())
    QtTest.QTest.qWait(230)
    popup = hint_popup()
    assert popup.isVisible() and popup._anchor is second
    assert popup._label.text() == 'Druga wskazówka, dłuższa od pierwszej.'
    hide_hint_popup(first)
    assert popup.isVisible()
    hide_hint_popup(second)
    assert not popup.isVisible()
  finally:
    hide_hint_popup()
    dispose(window, qt_app)


def test_rapid_page_switches_do_not_apply_opacity_or_panel_effects(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  window.show()
  try:
    for _ in range(10):
      for page in window._panels.values():
        window.tabs.setCurrentWidget(page)
        assert page.graphicsEffect() is None
    assert all(group.graphicsEffect() is None for group in window.findChildren(SectionGroup))
    assert window.live_panel._state == window.live_panel.STATE_STOPPED
  finally:
    dispose(window, qt_app)


def test_details_caption_and_footers_stay_accessible_at_both_widths(qt_app):
  apply_application_theme('dark', animate=False)
  window = MainWindow()
  window.live_panel._first_shown = True
  window.show()
  try:
    for width in (1600, 760):
      window.resize(width, 700)
      window.tabs.setCurrentWidget(window.settings_panel)
      QtTest.QTest.qWait(30)
      settings = window.settings_panel
      assert settings.btn_restore.x() < settings.btn_undo.x() < settings.btn_save.x()
      assert settings.btn_restore.y() == settings.btn_save.y()
      assert settings.chk_show_advanced.mapTo(settings, QtCore.QPoint()).y() < (
        settings.btn_save.mapTo(settings, QtCore.QPoint()).y()
      )
      assert settings.btn_save.rect().width() >= settings.btn_save.minimumSizeHint().width()
    window.system_status.set_expanded(True)
    QtTest.QTest.qWait(20)
    dialog = window.system_status._dialog
    assert dialog.title_bar.isVisible()
    assert dialog.title_bar.lbl_caption.text() == dialog.windowTitle()
    assert dialog.title_bar.lbl_caption.width() > 100
    assert dialog.title_bar.btn_minimize.isVisible() and dialog.title_bar.btn_maximize.isVisible()
    dialog.title_bar.btn_close.click()
    assert not dialog.isVisible()
    button = settings.btn_restore
    assert button.testAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
    assert isinstance(window.files_panel.btn_open_output, ActionButton)
  finally:
    dispose(window, qt_app)


def test_short_confirmation_reserves_space_for_caption_and_controls(qt_app):
  apply_application_theme('dark', animate=False)
  dialog = AppMessageBox()
  dialog.setWindowTitle('Zakończyć PySkryptor?')
  dialog.setText('Zakończyć?')
  dialog.addButton('Wróć', QtWidgets.QMessageBox.ButtonRole.RejectRole)
  dialog.show()
  QtTest.QTest.qWait(30)
  try:
    caption = dialog.title_bar.lbl_caption
    assert caption.width() >= caption.fontMetrics().horizontalAdvance(dialog.windowTitle())
    assert dialog.title_bar.btn_close.x() + dialog.title_bar.btn_close.width() <= dialog.width()
  finally:
    dispose(dialog, qt_app)


def test_enabled_action_styles_change_on_hover_in_both_themes(qt_app):
  host = QtWidgets.QWidget()
  layout = QtWidgets.QVBoxLayout(host)
  buttons = []
  for role in ('quiet', 'primary', 'navigation', 'windowControl'):
    button = ActionButton('Akcja', host)
    setup_button(button)
    button.setProperty('role' if role in {'navigation', 'windowControl'} else 'actionRole', role)
    layout.addWidget(button)
    buttons.append(button)
  host.resize(300, 320)
  host.show()
  try:
    for theme in ('dark', 'light'):
      apply_application_theme(theme, animate=False)
      for button in buttons:
        QtTest.QTest.mouseMove(host, QtCore.QPoint(1, 1))
        QtTest.QTest.qWait(10)
        normal = button.grab().toImage()
        QtTest.QTest.mouseMove(button, button.rect().center())
        QtTest.QTest.qWait(10)
        assert button.underMouse()
        assert button.grab().toImage() != normal, (theme, button.property('role'), button.property('actionRole'))
  finally:
    dispose(host, qt_app)


def test_task_options_in_short_window_preserve_visible_queue_and_selected_formats(qt_app):
  window = MainWindow()
  window.live_panel._first_shown = True
  files = window.files_panel
  files._insert_placeholder_row('C:/test/wywiad.wav', source_kind='file', title='wywiad.wav', duration_s=60)
  window.resize(760, 600)
  window.show()
  QtTest.QTest.qWait(30)
  try:
    files.options_disclosure.set_expanded(True, animated=False)
    QtTest.QTest.qWait(30)
    assert files._options_dialog.isVisible() and not files.options_disclosure.is_expanded()
    title = files.tbl_sources.item(0, files.COL_TITLE)
    assert files.tbl_sources.viewport().rect().contains(files.tbl_sources.visualItemRect(title).center())
    files._out_checks['json'].setChecked(True)
    files._options_dialog.close()
    assert files._options_scroll.parentWidget() is files.options_disclosure.body
    window.resize(1600, 1000)
    QtTest.QTest.qWait(30)
    assert files.options_disclosure.is_expanded() and files.cmb_source_language.isVisible()
    assert files._out_checks['json'].isChecked() and files.tbl_sources.rowCount() == 1
  finally:
    dispose(window, qt_app)
