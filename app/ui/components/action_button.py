# app/ui/components/action_button.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets


class ActionButton(QtWidgets.QPushButton):
  """Native push button with deliberate icon spacing and centered content."""

  ICON_GAP = 10

  def __init__(self, *args, **kwargs) -> None:
    super().__init__(*args, **kwargs)
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
    self.setIconSize(QtCore.QSize(18, 18))

  def sizeHint(self) -> QtCore.QSize:
    size = super().sizeHint()
    if self.text() and not self.icon().isNull():
      size.setWidth(size.width() + self.ICON_GAP - 4)
    return size

  def paintEvent(self, event: QtGui.QPaintEvent) -> None:
    option = QtWidgets.QStyleOptionButton()
    self.initStyleOption(option)
    text = option.text
    icon = QtGui.QIcon(option.icon)
    option.text = ''
    option.icon = QtGui.QIcon()
    painter = QtWidgets.QStylePainter(self)
    painter.drawControl(QtWidgets.QStyle.ControlElement.CE_PushButton, option)
    rect = self.style().subElementRect(QtWidgets.QStyle.SubElement.SE_PushButtonContents, option, self)
    if option.state & QtWidgets.QStyle.StateFlag.State_Sunken:
      rect.translate(1, 1)
    icon_width = self.iconSize().width() if not icon.isNull() else 0
    leading = self.property('role') == 'navigation' or self.property('contentAlignment') == 'leading'
    gap = (14 if leading else self.ICON_GAP) if icon_width and text else 0
    flags = QtCore.Qt.TextFlag.TextShowMnemonic
    if not self.style().styleHint(QtWidgets.QStyle.StyleHint.SH_UnderlineShortcut, option, self):
      flags |= QtCore.Qt.TextFlag.TextHideMnemonic
    text_width = min(self.fontMetrics().size(int(flags), text).width(), max(0, rect.width() - icon_width - gap))
    width = icon_width + gap + text_width
    left = rect.left() if leading else rect.left() + (rect.width() - width) // 2
    if icon_width:
      icon_rect = QtCore.QRect(left, rect.center().y() - self.iconSize().height() // 2 + 1,
                               icon_width, self.iconSize().height())
      mode = QtGui.QIcon.Mode.Normal if self.isEnabled() else QtGui.QIcon.Mode.Disabled
      icon.paint(painter, icon_rect, QtCore.Qt.AlignmentFlag.AlignCenter, mode)
    text_rect = QtCore.QRect(left + icon_width + gap, rect.top(), text_width, rect.height())
    color_group = QtGui.QPalette.ColorGroup.Active if self.isEnabled() else QtGui.QPalette.ColorGroup.Disabled
    painter.setFont(self.font())
    painter.setPen(option.palette.color(color_group, QtGui.QPalette.ColorRole.ButtonText))
    painter.drawText(text_rect, int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft) |
                     int(flags), self.fontMetrics().elidedText(text, QtCore.Qt.TextElideMode.ElideRight, text_width))
