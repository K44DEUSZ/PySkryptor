# ui/views/dialogs.py
from __future__ import annotations

from typing import Tuple
from PyQt5 import QtWidgets

from ui.i18n.translator import Translator


def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    title = Translator.tr("app.title")
    text = Translator.tr("dialog.cancel_confirm", detail="")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, title, text, parent=parent)
    yes_btn = box.addButton(Translator.tr("action.cancel_now"), QtWidgets.QMessageBox.AcceptRole)
    no_btn = box.addButton(Translator.tr("action.keep_working"), QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(no_btn)
    box.exec_()
    return box.clickedButton() is yes_btn


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> Tuple[str, str, bool]:
    """
    Return (action, new_stem, apply_all)
    action: "skip" | "overwrite" | "new"
    """
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(Translator.tr("dialog.conflict.title"))
    layout = QtWidgets.QVBoxLayout(dlg)

    layout.addWidget(QtWidgets.QLabel(Translator.tr("dialog.conflict.text", name=stem)))

    rb_skip = QtWidgets.QRadioButton(Translator.tr("dialog.conflict.skip"))
    rb_over = QtWidgets.QRadioButton(Translator.tr("dialog.conflict.overwrite"))
    rb_new = QtWidgets.QRadioButton(Translator.tr("dialog.conflict.new_name"))
    rb_skip.setChecked(True)

    layout.addWidget(rb_skip)
    layout.addWidget(rb_over)
    layout.addWidget(rb_new)

    name_edit = QtWidgets.QLineEdit()
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    def on_toggle():
        name_edit.setEnabled(rb_new.isChecked())

    rb_new.toggled.connect(on_toggle)

    cb_all = QtWidgets.QCheckBox(Translator.tr("dialog.conflict.apply_all"))
    layout.addWidget(cb_all)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    layout.addWidget(btns)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)

    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return "skip", "", False

    if rb_over.isChecked():
        return "overwrite", "", cb_all.isChecked()
    if rb_new.isChecked():
        return "new", name_edit.text().strip(), False  # 'apply_all' disabled for new names
    return "skip", "", cb_all.isChecked()
