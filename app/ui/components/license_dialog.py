# app/ui/components/license_dialog.py
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from app.core.config.config import AppConfig
from app.core.runtime.localization import tr
from app.ui.components.action_button import ActionButton
from app.ui.components.desktop_canvas import DesktopCanvas
from app.ui.components.window_title_bar import WindowTitleBar
from app.ui.support.action_icons import action_icon, refresh_action_icons
from app.ui.support.theme_runtime import apply_windows_dark_titlebar
from app.ui.support.widget_setup import setup_button, setup_input
from app.ui.support.window_frame import configure_native_frame, native_frame_event

_LOG = logging.getLogger(__name__)


class LicenseDialog(QtWidgets.QDialog):
  """Read-only local license documents with search and the application window chrome."""

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self.title_bar = None
    self._frame_configured = False
    self.setObjectName('LicenseDialog')
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    self.setWindowTitle(tr('about.viewer.title'))
    self.setWindowIcon(QtWidgets.QApplication.windowIcon())
    self.setMinimumSize(700, 480)
    screen = self.screen().availableGeometry()
    self.resize(min(980, int(screen.width() * 0.9)), min(700, int(screen.height() * 0.9)))
    self._build_ui()

  def _build_ui(self) -> None:
    outer = QtWidgets.QVBoxLayout(self)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    canvas = DesktopCanvas(self)
    canvas.setObjectName('MainCentral')
    outer.addWidget(canvas)
    layout = QtWidgets.QVBoxLayout(canvas)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    self.title_bar = WindowTitleBar(self, canvas)
    layout.addWidget(self.title_bar)
    body = QtWidgets.QWidget(canvas)
    content = QtWidgets.QVBoxLayout(body)
    content.setContentsMargins(20, 20, 20, 20)
    content.setSpacing(12)
    layout.addWidget(body, 1)
    self.tabs = QtWidgets.QTabWidget(body)
    self.tabs.setProperty('role', 'categoryTabs')
    self.editors: list[QtWidgets.QPlainTextEdit] = []
    for title, path, failure_key in (
      ('GPL-3.0', Path(AppConfig.PATHS.LICENSE_FILE), 'about.license.open_failed'),
      (tr('about.notices.label'), AppConfig.PATHS.INSTALL_ROOT_DIR / 'THIRD_PARTY_NOTICES.txt',
       'about.notices.open_failed'),
    ):
      editor = QtWidgets.QPlainTextEdit(self.tabs)
      editor.setProperty('role', 'licenseDocument')
      editor.setReadOnly(True)
      document_font = QtGui.QFont('Consolas')
      document_font.setPixelSize(13)
      editor.document().setDefaultFont(document_font)
      try:
        editor.setPlainText(path.read_text(encoding='utf-8-sig'))
      except (OSError, UnicodeError):
        _LOG.error('Reading a local license document failed. path=%s', path, exc_info=True)
        editor.setPlainText(tr(failure_key))
      self.tabs.addTab(editor, action_icon('file'), title)
      self.editors.append(editor)
    content.addWidget(self.tabs, 1)
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(10)
    self.ed_search = QtWidgets.QLineEdit(body)
    setup_input(self.ed_search, placeholder=tr('about.viewer.search_placeholder'), min_h=42)
    self.ed_search.setAccessibleName(tr('about.viewer.search_placeholder'))
    self.ed_search.returnPressed.connect(self.find_next)
    self.btn_find = ActionButton(tr('about.viewer.find_next'), body)
    setup_button(self.btn_find, min_w=0)
    self.btn_find.clicked.connect(self.find_next)
    self.btn_copy = ActionButton(action_icon('file'), tr('about.viewer.copy'), body)
    setup_button(self.btn_copy, min_w=0)
    self.btn_copy.clicked.connect(self.copy_document)
    self.btn_close = ActionButton(action_icon('window_close'), tr('about.viewer.close'), body)
    setup_button(self.btn_close, min_w=0)
    self.btn_close.clicked.connect(self.close)
    row.addWidget(self.ed_search, 1)
    for button in (self.btn_find, self.btn_copy, self.btn_close):
      row.addWidget(button)
    content.addLayout(row)
    self.lbl_search_status = QtWidgets.QLabel(body)
    self.lbl_search_status.setProperty('role', 'secondaryText')
    self.lbl_search_status.hide()
    content.addWidget(self.lbl_search_status)
    self.tabs.currentChanged.connect(self._clear_search_status)
    self.ed_search.textChanged.connect(self._clear_search_status)
    self._search_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Find, self)
    self._search_shortcut.activated.connect(self._focus_search)
    refresh_action_icons(self)

  def set_document(self, index: int) -> None:
    self.tabs.setCurrentIndex(index)

  def _focus_search(self) -> None:
    self.ed_search.setFocus()
    self.ed_search.selectAll()

  def _clear_search_status(self, *_args) -> None:
    self.lbl_search_status.hide()

  def find_next(self) -> None:
    query = self.ed_search.text()
    if not query:
      return
    editor = self.editors[self.tabs.currentIndex()]
    found = editor.find(query)
    if not found:
      cursor = editor.textCursor()
      cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
      editor.setTextCursor(cursor)
      found = editor.find(query)
    self.lbl_search_status.setText(tr('about.viewer.search_not_found'))
    self.lbl_search_status.setVisible(not found)

  def copy_document(self) -> None:
    QtWidgets.QApplication.clipboard().setText(self.editors[self.tabs.currentIndex()].toPlainText())

  def showEvent(self, event: QtGui.QShowEvent) -> None:
    super().showEvent(event)
    if not self._frame_configured:
      configure_native_frame(self)
      apply_windows_dark_titlebar(self)
      self._frame_configured = True

  def nativeEvent(self, event_type, message) -> tuple[bool, int]:
    return native_frame_event(self, getattr(self, 'title_bar', None), message)

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if getattr(self, 'title_bar', None) is None:
      return
    if event.type() == QtCore.QEvent.Type.WindowStateChange:
      self.title_bar.refresh_icons()
    if event.type() in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      refresh_action_icons(self)
      self.title_bar.refresh_icons()
      if hasattr(self, 'tabs'):
        for index in range(self.tabs.count()):
          self.tabs.setTabIcon(index, action_icon('file'))
