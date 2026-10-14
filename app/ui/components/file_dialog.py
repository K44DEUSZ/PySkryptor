# app/ui/components/file_dialog.py
from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from app.core.runtime.localization import tr
from app.ui.components.app_dialog import _DialogChrome
from app.ui.support.action_icons import action_icon
from app.ui.support.widget_setup import setup_button, setup_combo, setup_input


class AppFileDialog(_DialogChrome, QtWidgets.QFileDialog):
  """Standard Qt file selection with the shared application chrome and navigation."""

  def __init__(self, parent=None, caption='', directory='', filter='') -> None:
    super().__init__(parent, caption, directory, filter)
    self.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
    self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    self.setWindowIcon(QtWidgets.QApplication.windowIcon())
    self.setProperty('appDialog', True)
    self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
    self.setMinimumSize(640, 420)
    self.resize(840, 560)

  def _prepare_controls(self) -> None:
    directory_only = self.fileMode() == self.FileMode.Directory
    self.setLabelText(self.DialogLabel.LookIn, tr('file_dialog.location'))
    self.setLabelText(
      self.DialogLabel.FileName, tr('file_dialog.folder' if directory_only else 'file_dialog.file_name')
    )
    self.setLabelText(self.DialogLabel.FileType, tr('file_dialog.file_type'))
    actions = {
      'backButton': ('back', 'back'),
      'forwardButton': ('forward', 'forward'),
      'toParentButton': ('parent', 'parent'),
      'newFolderButton': ('folder_add', 'new_folder'),
      'listModeButton': ('view_list', 'list'),
      'detailModeButton': ('view_details', 'details'),
    }
    for name, (icon, message) in actions.items():
      button = self.findChild(QtWidgets.QToolButton, name)
      if button is not None:
        button.setIcon(action_icon(icon))
        button.setIconSize(QtCore.QSize(22, 22))
        button.setFixedSize(38, 38)
        button.setProperty('fileNavigation', True)
        button.setToolTip(tr('file_dialog.' + message))
        button.setAccessibleName(button.toolTip())
    tree = self.findChild(QtWidgets.QTreeView, 'treeView')
    if tree is not None and tree.model() is not None:
      tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
      for column in range(1, tree.model().columnCount()):
        tree.header().setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
      if self.fileMode() == self.FileMode.Directory:
        tree.hideColumn(1)
        tree.hideColumn(2)
    for name in ('fileTypeLabel', 'fileTypeCombo'):
      widget = self.findChild(QtWidgets.QWidget, name)
      if widget is not None and self.fileMode() == self.FileMode.Directory:
        widget.hide()
    button_box = self.findChild(QtWidgets.QDialogButtonBox)
    if button_box is not None:
      layout = self.layout()
      if isinstance(layout, QtWidgets.QGridLayout):
        layout.removeWidget(button_box)
        button_box.setOrientation(QtCore.Qt.Orientation.Horizontal)
        layout.addWidget(button_box, 4, 0, 1, layout.columnCount(), QtCore.Qt.AlignmentFlag.AlignRight)
        layout.setColumnStretch(1, 1)
        for name in ('fileNameEdit', 'fileTypeCombo'):
          field = self.findChild(QtWidgets.QWidget, name)
          if field is not None:
            row, column, rows, _ = layout.getItemPosition(layout.indexOf(field))
            layout.removeWidget(field)
            layout.addWidget(field, row, column, rows, layout.columnCount() - column)
      for button in button_box.buttons():
        setup_button(button, min_h=36, min_w=88)
    for name in ('lookInCombo', 'fileTypeCombo'):
      combo = self.findChild(QtWidgets.QComboBox, name)
      if combo is not None:
        setup_combo(combo, min_h=36)
    edit = self.findChild(QtWidgets.QLineEdit, 'fileNameEdit')
    if edit is not None:
      setup_input(edit, min_h=36)
    sidebar = self.findChild(QtWidgets.QListView, 'sidebar')
    if sidebar is not None:
      sidebar.setMinimumWidth(150)

  def showEvent(self, event: QtGui.QShowEvent) -> None:
    self._prepare_controls()
    super().showEvent(event)

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      self._prepare_controls()

  @classmethod
  def _select(cls, parent, caption, directory, filter, *, mode, save=False) -> tuple[list[str], str]:
    dialog = cls(parent, caption, directory, filter)
    dialog.setFileMode(mode)
    dialog.setAcceptMode(cls.AcceptMode.AcceptSave if save else cls.AcceptMode.AcceptOpen)
    if mode == cls.FileMode.Directory:
      dialog.setOption(cls.Option.ShowDirsOnly, True)
    try:
      if dialog.exec() == cls.DialogCode.Accepted:
        return dialog.selectedFiles(), dialog.selectedNameFilter()
      return [], ''
    finally:
      dialog.deleteLater()

  @classmethod
  def getExistingDirectory(cls, parent=None, caption='', directory='') -> str:
    paths, _ = cls._select(parent, caption, directory, '', mode=cls.FileMode.Directory)
    return paths[0] if paths else ''

  @classmethod
  def getOpenFileNames(cls, parent=None, caption='', directory='', filter='') -> tuple[list[str], str]:
    return cls._select(parent, caption, directory, filter, mode=cls.FileMode.ExistingFiles)

  @classmethod
  def getOpenFileName(cls, parent=None, caption='', directory='', filter='') -> tuple[str, str]:
    paths, selected_filter = cls._select(parent, caption, directory, filter, mode=cls.FileMode.ExistingFile)
    return (paths[0] if paths else ''), selected_filter

  @classmethod
  def getSaveFileName(cls, parent=None, caption='', directory='', filter='') -> tuple[str, str]:
    paths, selected_filter = cls._select(parent, caption, directory, filter, mode=cls.FileMode.AnyFile, save=True)
    return (paths[0] if paths else ''), selected_filter
