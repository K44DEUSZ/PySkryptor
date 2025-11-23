# ui/views/dialogs.py
from __future__ import annotations

from typing import Tuple
from PyQt5 import QtWidgets

from ui.utils.translating import Translator as T


# ----- Critical startup dialogs -----

def critical_defaults_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    """
    Hardcoded EN message. Called when defaults.json is missing.
    Single 'OK' closes the app (caller should exit after return).
    """
    title = "Application Error"
    text = (
        "Required configuration file 'defaults.json' is missing.\n\n"
        "The application cannot start without it.\n"
        "Please restore 'defaults.json' and try again."
    )
    QtWidgets.QMessageBox.critical(parent, title, text)


def critical_locales_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    """
    Hardcoded EN message. Called after settings are verified, but locale file is missing.
    Single 'OK' closes the app (caller should exit after return).
    """
    title = "Localization Error"
    text = (
        "Required localization file is missing.\n\n"
        "The application cannot start without language resources.\n"
        "Please restore the locale file in 'resources/locales' and try again."
    )
    QtWidgets.QMessageBox.critical(parent, title, text)


def info_settings_restored(parent: QtWidgets.QWidget | None = None) -> None:
    """
    Localized message (after Translator has been loaded using the restored settings).
    Single 'OK' continues into the app.
    """
    title = T.tr("app.title")
    text = T.tr("dialog.settings_restored")  # e.g. "Settings file was missing. Defaults have been restored."
    QtWidgets.QMessageBox.information(parent, title, text)


# ----- Runtime confirmations -----

def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    """Ask whether to cancel a long-running operation."""
    title = T.tr("app.title")
    text = T.tr("dialog.cancel_confirm", detail="")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, title, text, parent=parent)
    yes_btn = box.addButton(T.tr("action.cancel_now"), QtWidgets.QMessageBox.AcceptRole)
    no_btn = box.addButton(T.tr("action.keep_working"), QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(no_btn)
    box.exec_()
    return box.clickedButton() is yes_btn


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> Tuple[str, str, bool]:
    """
    Transcript conflict dialog.
    Returns (action, new_stem, apply_all) where action ∈ {"skip","overwrite","new"}.
    'apply_all' is applicable to skip/overwrite.
    """
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(T.tr("dialog.conflict.title"))
    layout = QtWidgets.QVBoxLayout(dlg)

    layout.addWidget(QtWidgets.QLabel(T.tr("dialog.conflict.text", name=stem)))

    rb_skip = QtWidgets.QRadioButton(T.tr("dialog.conflict.skip"))
    rb_over = QtWidgets.QRadioButton(T.tr("dialog.conflict.overwrite"))
    rb_new = QtWidgets.QRadioButton(T.tr("dialog.conflict.new_name"))
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

    cb_all = QtWidgets.QCheckBox(T.tr("dialog.conflict.apply_all"))
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


def ask_download_duplicate(parent: QtWidgets.QWidget, *, title: str, suggested_name: str) -> Tuple[str, str]:
    """
    Download duplicate dialog.
    Returns (action, new_name) where action ∈ {"skip","overwrite","rename"}.
    """
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle(T.tr("app.title"))
    layout = QtWidgets.QVBoxLayout(dlg)

    layout.addWidget(QtWidgets.QLabel(T.tr("down.dialog.exists.text", title=title)))

    rb_skip = QtWidgets.QRadioButton(T.tr("down.dialog.exists.skip"))
    rb_over = QtWidgets.QRadioButton(T.tr("down.dialog.exists.overwrite"))
    rb_ren = QtWidgets.QRadioButton(T.tr("down.dialog.exists.rename"))
    rb_skip.setChecked(True)

    row = QtWidgets.QHBoxLayout()
    row.addWidget(rb_skip)
    row.addWidget(rb_over)
    row.addWidget(rb_ren)
    layout.addLayout(row)

    name_edit = QtWidgets.QLineEdit(suggested_name)
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    def on_toggle():
        name_edit.setEnabled(rb_ren.isChecked())

    rb_ren.toggled.connect(on_toggle)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    layout.addWidget(btns)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)

    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return "skip", ""

    if rb_over.isChecked():
        return "overwrite", ""
    if rb_ren.isChecked():
        return "rename", name_edit.text().strip()
    return "skip", ""
