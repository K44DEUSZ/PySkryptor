# app/ui/support/dialog_positioning.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.ui.support.action_icons import action_icon
from app.ui.support.theme_runtime import status_icon


def dialog_screen(dialog: QtWidgets.QWidget) -> QtGui.QScreen | None:
  """Use the owner's display for application dialogs, including startup decisions."""
  owner = dialog.parentWidget() or QtWidgets.QApplication.activeWindow()
  if owner is not None and owner is not dialog:
    return owner.window().screen()
  return QtWidgets.QApplication.screenAt(QtGui.QCursor.pos()) or dialog.screen()


def center_dialog(dialog: QtWidgets.QWidget) -> None:
  """Center the complete window frame within the display's available work area."""
  screen = dialog_screen(dialog)
  if screen is None:
    return
  available = screen.availableGeometry()
  frame = dialog.frameGeometry()
  frame.moveCenter(available.center())
  frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
  frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
  offset = dialog.pos() - dialog.frameGeometry().topLeft()
  dialog.move(frame.topLeft() + offset)


class _DialogPositioningFilter(QtCore.QObject):
  """Position modal and modeless application dialogs after their final layout pass."""

  def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
    if event.type() == QtCore.QEvent.Type.Show and isinstance(obj, QtWidgets.QDialog):
      if isinstance(obj, QtWidgets.QMessageBox) and obj.icon() != QtWidgets.QMessageBox.Icon.NoIcon:
        icon = (status_icon('status_error') if obj.icon() in (
          QtWidgets.QMessageBox.Icon.Warning, QtWidgets.QMessageBox.Icon.Critical,
        ) else action_icon('info'))
        obj.setIconPixmap(icon.pixmap(32, 32))
      def position(dialog=obj) -> None:
        try:
          if dialog.isVisible() and not dialog.isMaximized():
            center_dialog(dialog)
        except RuntimeError:
          pass

      QtCore.QTimer.singleShot(0, position)
    return False


def install_dialog_positioning(app: QtWidgets.QApplication) -> None:
  app.setAttribute(QtCore.Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
  if not hasattr(app, '_dialog_positioning_filter'):
    app._dialog_positioning_filter = _DialogPositioningFilter(app)
    app.installEventFilter(app._dialog_positioning_filter)
