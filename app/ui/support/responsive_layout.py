# app/ui/support/responsive_layout.py
from __future__ import annotations

from collections.abc import Callable

from PyQt6 import QtCore, QtWidgets


class ResponsiveLayout(QtCore.QObject):
  """Reflow existing controls when their host crosses a width breakpoint."""

  def __init__(self, host: QtWidgets.QWidget, apply: Callable[[int], None]) -> None:
    super().__init__(host)
    self._host = host
    self._apply = apply
    self._width = -1
    host.installEventFilter(self)
    QtCore.QTimer.singleShot(0, self.refresh)

  def refresh(self) -> None:
    width = self._host.width()
    if width != self._width:
      self._width = width
      self._apply(width)

  def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
    if obj is self._host and event.type() in {QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show}:
      self.refresh()
    return False


def responsive_box(host: QtWidgets.QWidget, layout: QtWidgets.QBoxLayout, breakpoint: int) -> ResponsiveLayout:
  def reflow(width: int) -> None:
    layout.setDirection(QtWidgets.QBoxLayout.Direction.LeftToRight if width >= breakpoint
                        else QtWidgets.QBoxLayout.Direction.TopToBottom)

  return ResponsiveLayout(host, reflow)


def responsive_grid(
  host: QtWidgets.QWidget,
  layout: QtWidgets.QGridLayout,
  widgets: tuple[QtWidgets.QWidget, ...],
  breakpoints: tuple[tuple[int, int], ...],
  *,
  full_width_last: bool = False,
) -> ResponsiveLayout:
  columns = -1

  def reflow(width: int) -> None:
    nonlocal columns
    count = next((count for minimum, count in breakpoints if width >= minimum), 1)
    if count == columns:
      return
    columns = count
    for widget in widgets:
      layout.removeWidget(widget)
    for column in range(max(4, len(widgets))):
      layout.setColumnStretch(column, int(column < columns))
      layout.setColumnMinimumWidth(column, 0)
    cells = widgets[:-1] if full_width_last else widgets
    for index, widget in enumerate(cells):
      layout.addWidget(widget, index // columns, index % columns)
    if full_width_last:
      layout.addWidget(widgets[-1], (len(cells) + columns - 1) // columns, 0, 1, columns)

  return ResponsiveLayout(host, reflow)


def responsive_input_row(
  host: QtWidgets.QWidget, layout: QtWidgets.QGridLayout, field: QtWidgets.QWidget, actions: QtWidgets.QWidget,
) -> ResponsiveLayout:
  def reflow(width: int) -> None:
    layout.removeWidget(field)
    layout.removeWidget(actions)
    for column in range(4):
      layout.setColumnStretch(column, int(column == 0))
      layout.setColumnMinimumWidth(column, 0)
    layout.addWidget(field, 0, 0, 1, 2 if width < 740 else 1)
    layout.addWidget(actions, 1 if width < 740 else 0, 0 if width < 740 else 1, 1, 2 if width < 740 else 1)

  return ResponsiveLayout(host, reflow)
