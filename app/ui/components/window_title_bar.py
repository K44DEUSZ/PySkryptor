# app/ui/components/window_title_bar.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.core.runtime.localization import tr
from app.ui.components.action_button import ActionButton
from app.ui.support.action_icons import action_icon
from app.ui.support.widget_setup import setup_button


class WindowTitleBar(QtWidgets.QFrame):
  """Application title bar using native move, minimize and maximize operations."""

  def __init__(self, window: QtWidgets.QWidget, parent: QtWidgets.QWidget, *, navigation_callback=None) -> None:
    super().__init__(parent)
    self._window = window
    self._drag_offset = None
    self.setObjectName('WindowTitleBar')
    self.setFixedHeight(42)
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QtWidgets.QHBoxLayout(self)
    layout.setContentsMargins(16, 0, 6, 0)
    layout.setSpacing(12)
    self.btn_sidebar = self._button('sidebar', navigation_callback) if navigation_callback is not None else None
    if self.btn_sidebar is not None:
      layout.addWidget(self.btn_sidebar)
    self.lbl_icon = QtWidgets.QLabel(self)
    self.lbl_icon.setFixedSize(20, 20)
    self.lbl_caption = QtWidgets.QLabel(window.windowTitle(), self)
    self.lbl_caption.setObjectName('WindowCaption')
    self.lbl_caption.setMinimumWidth(0)
    self.lbl_caption.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
    for label in (self.lbl_icon, self.lbl_caption):
      label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    layout.addWidget(self.lbl_icon)
    layout.addWidget(self.lbl_caption, 1)
    self.btn_minimize = self._button('minimize', window.showMinimized)
    self.btn_maximize = self._button('maximize', self.toggle_maximize)
    self.btn_close = self._button('close', window.close)
    self.btn_close.setObjectName('WindowCloseButton')
    for button in (self.btn_minimize, self.btn_maximize, self.btn_close):
      layout.addWidget(button)
    window.windowTitleChanged.connect(self.lbl_caption.setText)
    window.windowIconChanged.connect(self.refresh_icons)
    self.refresh_icons()

  def _button(self, name: str, slot) -> ActionButton:
    button = ActionButton(self)
    setup_button(button, min_h=30, min_w=38)
    button.setFixedSize(38, 30)
    button.setProperty('role', 'windowControl')
    button.setProperty('windowAction', name)
    button.setToolTip(tr(f'desktop.window.{name}'))
    button.setAccessibleName(button.toolTip())
    button.clicked.connect(slot)
    return button

  def refresh_icons(self, *_args) -> None:
    if not hasattr(self, 'btn_close'):
      return
    self.lbl_icon.setPixmap(self._window.windowIcon().pixmap(20, 20))
    if self.btn_sidebar is not None:
      self.btn_sidebar.setIcon(action_icon('window_sidebar'))
    action = 'restore' if self._window.isMaximized() else 'maximize'
    for button, name in ((self.btn_minimize, 'minimize'), (self.btn_maximize, action), (self.btn_close, 'close')):
      button.setIcon(action_icon(f'window_{name}'))
      button.setToolTip(tr(f'desktop.window.{name}'))
      button.setAccessibleName(button.toolTip())

  def toggle_maximize(self) -> None:
    if self._window.isMaximized():
      self._window.showNormal()
    else:
      self._window.showMaximized()
    self.refresh_icons()

  def is_caption_point(self, point: QtCore.QPoint) -> bool:
    if self.btn_sidebar is not None and self.btn_sidebar.geometry().contains(point):
      return False
    return self.rect().contains(point) and point.x() < self.btn_minimize.x() - 6

  def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
    if event.button() == QtCore.Qt.MouseButton.LeftButton:
      handle = self._window.windowHandle()
      if handle is not None and handle.startSystemMove():
        event.accept()
        return
      self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
    super().mousePressEvent(event)

  def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
    if self._drag_offset is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
      if not self._window.isMaximized():
        self._window.move(event.globalPosition().toPoint() - self._drag_offset)
    super().mouseMoveEvent(event)

  def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
    self._drag_offset = None
    super().mouseReleaseEvent(event)

  def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
    if event.button() == QtCore.Qt.MouseButton.LeftButton:
      self.toggle_maximize()
      event.accept()
      return
    super().mouseDoubleClickEvent(event)

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() in {QtCore.QEvent.Type.PaletteChange, QtCore.QEvent.Type.StyleChange}:
      self.refresh_icons()
