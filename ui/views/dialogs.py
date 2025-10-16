# ui/views/dialogs.py
from __future__ import annotations

from typing import Tuple

from PyQt5 import QtWidgets


def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    msg = QtWidgets.QMessageBox(parent)
    msg.setIcon(QtWidgets.QMessageBox.Warning)
    msg.setWindowTitle("Potwierdzenie anulowania")
    msg.setText("Czy na pewno chcesz natychmiast przerwać bieżącą transkrypcję?")
    msg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
    msg.setDefaultButton(QtWidgets.QMessageBox.No)
    ret = msg.exec_()
    return ret == QtWidgets.QMessageBox.Yes


class _ConflictDialog(QtWidgets.QDialog):
    def __init__(self, stem: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Konflikt: element już istnieje")
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            f"Folder docelowy dla „{stem}” już istnieje.\nWybierz działanie:"
        )
        layout.addWidget(info)

        self.rb_skip = QtWidgets.QRadioButton("Pomiń ten element")
        self.rb_overwrite = QtWidgets.QRadioButton("Nadpisz istniejącą zawartość")
        self.rb_new = QtWidgets.QRadioButton("Utwórz nową wersję z inną nazwą")
        self.rb_new.setChecked(False)
        self.rb_skip.setChecked(True)

        layout.addWidget(self.rb_skip)
        layout.addWidget(self.rb_overwrite)
        layout.addWidget(self.rb_new)

        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Nowa nazwa:"))
        self.le_new = QtWidgets.QLineEdit(stem)
        name_row.addWidget(self.le_new, 1)
        layout.addLayout(name_row)

        self.cb_apply_all = QtWidgets.QCheckBox("Zastosuj dla pozostałych")
        layout.addWidget(self.cb_apply_all)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        def _toggle_controls() -> None:
            is_new = self.rb_new.isChecked()
            self.le_new.setEnabled(is_new)
            # Gdy wybrano „nową nazwę”, uniemożliwiamy „zastosuj dla pozostałych”
            self.cb_apply_all.setEnabled(not is_new)
            if is_new:
                self.cb_apply_all.setChecked(False)

        self.rb_skip.toggled.connect(_toggle_controls)
        self.rb_overwrite.toggled.connect(_toggle_controls)
        self.rb_new.toggled.connect(_toggle_controls)
        _toggle_controls()

    def result_values(self) -> Tuple[str, str, bool]:
        if self.rb_skip.isChecked():
            return "skip", "", self.cb_apply_all.isChecked()
        if self.rb_overwrite.isChecked():
            return "overwrite", "", self.cb_apply_all.isChecked()
        return "new", self.le_new.text().strip(), False  # apply_all zawsze False dla „nowej nazwy”


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> Tuple[str, str, bool]:
    """
    Show conflict dialog.
    Returns: (action, new_stem, apply_all)
    action in {"skip","overwrite","new"}
    """
    dlg = _ConflictDialog(stem, parent)
    if dlg.exec_() == QtWidgets.QDialog.Accepted:
        return dlg.result_values()
    return "skip", "", False
