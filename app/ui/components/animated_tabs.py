# app/ui/components/animated_tabs.py
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from app.ui.support.action_icons import refresh_action_icons
from app.ui.ui_config import ui


class AnimatedTabs(QtWidgets.QTabWidget):
  """Switch pages directly and animate only the selected tab's small indicator."""

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self._indicator = QtWidgets.QFrame(self.tabBar())
    self._indicator.setObjectName('TabSelectionIndicator')
    self._indicator.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    self._indicator.hide()
    self._animation = QtCore.QPropertyAnimation(self._indicator, b'geometry', self)
    self._animation.setDuration(ui(self).page_animation_ms)
    self._animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
    self.tabBar().installEventFilter(self)
    self.currentChanged.connect(self._animate_page)

  def _finish_transition(self) -> None:
    self._animation.stop()

  def _position_indicator(self, *, animate: bool = False) -> None:
    bar = self.tabBar()
    if not bar.isVisible() or self.currentIndex() < 0:
      self._indicator.hide()
      return
    rect = bar.tabRect(self.currentIndex())
    target = QtCore.QRect(rect.left() + 12, rect.bottom() - 2, max(0, rect.width() - 24), 2)
    self._animation.stop()
    if animate and self._indicator.isVisible():
      self._animation.setStartValue(self._indicator.geometry())
      self._animation.setEndValue(target)
      self._animation.start()
    else:
      self._indicator.setGeometry(target)
    self._indicator.show()
    self._indicator.raise_()

  def _animate_page(self, _index: int) -> None:
    page = self.currentWidget()
    if page is not None:
      refresh_action_icons(page)
    self._position_indicator(animate=True)

  def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
    if obj is self.tabBar() and event.type() in {QtCore.QEvent.Type.Show, QtCore.QEvent.Type.Resize}:
      QtCore.QTimer.singleShot(0, self._position_indicator)
    return super().eventFilter(obj, event)
