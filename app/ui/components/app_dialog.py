# app/ui/components/app_dialog.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.ui.components.window_title_bar import WindowTitleBar
from app.ui.support.theme_runtime import apply_windows_dark_titlebar
from app.ui.support.window_frame import configure_native_frame, native_frame_event


class _DialogChrome:
  """Use the application caption without wrapping a dialog's content in another panel."""

  def prepare_chrome(self) -> None:
    layout = self.layout()
    if layout is None:
      return
    if getattr(self, 'title_bar', None) is None:
      self.title_bar = WindowTitleBar(self, self)
      self.title_bar.btn_close.setEnabled(not hasattr(self, '_no_close_filter'))
    caption_width = min(320, self.title_bar.lbl_caption.fontMetrics().horizontalAdvance(self.windowTitle()))
    caption_minimum = self.title_bar.minimumSizeHint().width() + caption_width
    if isinstance(self, QtWidgets.QMessageBox) and isinstance(layout, QtWidgets.QGridLayout):
      if getattr(self, '_caption_spacer', None) is None:
        self._caption_spacer = QtWidgets.QSpacerItem(
          0, 0, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed,
        )
        layout.addItem(self._caption_spacer, layout.rowCount(), 0, 1, layout.columnCount())
      self._caption_spacer.changeSize(
        max(0, caption_minimum - 40), 0,
        QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Fixed,
      )
      layout.invalidate()
    else:
      self.setMinimumWidth(max(self.minimumWidth(), caption_minimum))
    layout.setContentsMargins(20, self.title_bar.height() + 20, 20, 20)
    self.title_bar.setGeometry(0, 0, self.width(), self.title_bar.height())
    self.title_bar.show()
    self.title_bar.raise_()

  def showEvent(self, event: QtGui.QShowEvent) -> None:
    self.prepare_chrome()
    super().showEvent(event)
    self.prepare_chrome()
    configure_native_frame(self)
    apply_windows_dark_titlebar(self)

  def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
    super().resizeEvent(event)
    if getattr(self, 'title_bar', None) is not None:
      self.title_bar.setGeometry(0, 0, self.width(), self.title_bar.height())

  def nativeEvent(self, event_type, message) -> tuple[bool, int]:
    return native_frame_event(self, getattr(self, 'title_bar', None), message)

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if getattr(self, 'title_bar', None) is not None and event.type() in {
      QtCore.QEvent.Type.WindowStateChange, QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange,
    }:
      self.title_bar.refresh_icons()


class AppDialog(_DialogChrome, QtWidgets.QDialog):
  """Application dialog with the shared movable, resizable window caption."""

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    self.setWindowIcon(QtWidgets.QApplication.windowIcon())
    self.setProperty('appDialog', True)
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)


class AppMessageBox(_DialogChrome, QtWidgets.QMessageBox):
  """Confirmation using Qt's button semantics and the application's window caption."""

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    self.setProperty('appDialog', True)
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
