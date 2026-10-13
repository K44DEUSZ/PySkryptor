# app/ui/components/source_identity_delegate.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.ui.support.action_icons import action_icon
from app.ui.support.theme_runtime import active_theme_key, theme_color


class SourceIdentityDelegate(QtWidgets.QStyledItemDelegate):
  """Present source identity and its location as one readable queue cell."""

  def __init__(self, table: QtWidgets.QTableWidget, *, detail_column: int, icon_name: str) -> None:
    super().__init__(table)
    self._table = table
    self._detail_column = detail_column
    self._icon_name = icon_name

  def paint(self, painter: QtGui.QPainter, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex) -> None:
    item_option = QtWidgets.QStyleOptionViewItem(option)
    self.initStyleOption(item_option, index)
    title = item_option.text
    detail_index = index.siblingAtColumn(self._detail_column)
    detail = str(detail_index.data(QtCore.Qt.ItemDataRole.DisplayRole) or '')
    item_option.text = ''
    painter.save()
    self._table.style().drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, item_option, painter, self._table)
    rect = option.rect.adjusted(8, 0, -8, 0)
    icon_name = 'link' if detail.lower().startswith(('https://', 'http://')) else self._icon_name
    action_icon(icon_name).paint(painter, QtCore.QRect(rect.x(), rect.center().y() - 9, 18, 18))
    rect.adjust(28, 0, 0, 0)
    font = QtGui.QFont(option.font)
    font.setWeight(QtGui.QFont.Weight.Medium)
    painter.setFont(font)
    selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
    theme = active_theme_key()
    painter.setPen(theme_color(theme, '@TEXT_ON_SELECTION@' if selected else '@TEXT_PRIMARY@'))
    metrics = QtGui.QFontMetrics(font)
    title_rect = QtCore.QRect(rect.x(), rect.center().y() - 18, rect.width(), 22)
    painter.drawText(title_rect, QtCore.Qt.AlignmentFlag.AlignVCenter,
                     metrics.elidedText(title, QtCore.Qt.TextElideMode.ElideMiddle, rect.width()))
    font.setPixelSize(11)
    font.setWeight(QtGui.QFont.Weight.Normal)
    painter.setFont(font)
    painter.setPen(theme_color(theme, '@TEXT_ON_SELECTION@' if selected else '@TEXT_SECONDARY@'))
    detail_rect = QtCore.QRect(rect.x(), rect.center().y() + 3, rect.width(), 18)
    painter.drawText(detail_rect, QtCore.Qt.AlignmentFlag.AlignVCenter,
                     QtGui.QFontMetrics(font).elidedText(detail, QtCore.Qt.TextElideMode.ElideMiddle, rect.width()))
    painter.restore()

  def helpEvent(
    self, event: QtGui.QHelpEvent, view: QtWidgets.QAbstractItemView,
    option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex,
  ) -> bool:
    if not index.isValid():
      return False
    text = str(index.data(QtCore.Qt.ItemDataRole.ToolTipRole) or '')
    if not text:
      title = str(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or '')
      detail = str(index.siblingAtColumn(self._detail_column).data(QtCore.Qt.ItemDataRole.DisplayRole) or '')
      text = f'{title}\n{detail}'
    QtWidgets.QToolTip.showText(event.globalPos(), text, view)
    return True
