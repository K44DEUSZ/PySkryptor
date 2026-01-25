# ui/views/dialogs.py
from __future__ import annotations

from typing import Tuple
from PyQt5 import QtCore, QtWidgets

from ui.utils.translating import Translator as T


BASE_H = 24
PAD = 12
SPACING = 8


def _sanitize_window_flags(w: QtWidgets.QWidget) -> None:
    """Remove the Windows '?' (Context Help) button for a cleaner title bar."""
    flags = w.windowFlags()
    flags &= ~QtCore.Qt.WindowContextHelpButtonHint
    w.setWindowFlags(flags)


def _tune_dialog_layout(layout: QtWidgets.QLayout) -> None:
    """Keep dialogs visually consistent with FilesPanel spacing."""
    layout.setContentsMargins(PAD, PAD, PAD, PAD)
    layout.setSpacing(SPACING)


def _tune_buttons(*buttons: QtWidgets.QAbstractButton) -> None:
    """Unify button height / proportions across all dialogs."""
    for b in buttons:
        b.setMinimumHeight(BASE_H)
        b.setMinimumWidth(140)


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


def critical_config_load_failed_and_exit(parent: QtWidgets.QWidget | None, details: str) -> None:
    """Generic config error before i18n is available."""
    title = "PySkryptor Error"
    text = f"Cannot load configuration.\n\nDetails: {details}"
    QtWidgets.QMessageBox.critical(parent, title, text)


def info_settings_restored(parent: QtWidgets.QWidget | None = None) -> None:
    """
    Localized message (after Translator has been loaded using the restored settings).
    Single 'OK' continues into the app.
    """
    title = T.tr("app.title")
    text = T.tr("dialog.settings_restored")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, title, text, parent=parent)
    _sanitize_window_flags(box)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    box.exec_()


# ----- Downloader info dialogs -----

def info_playlist_not_supported(parent: QtWidgets.QWidget | None = None) -> None:
    """Shown when a playlist URL (or video-in-playlist context) is detected."""
    title = T.tr("app.title")
    text = T.tr("down.dialog.playlist_not_supported.text")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, title, text, parent=parent)
    _sanitize_window_flags(box)
    box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    box.exec_()


# ----- Runtime confirmations -----

def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    """Ask whether to cancel a long-running operation."""
    title = T.tr("app.title")
    text = T.tr("dialog.cancel_confirm", detail="")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, title, text, parent=parent)
    _sanitize_window_flags(box)

    yes_btn = box.addButton(T.tr("action.cancel_now"), QtWidgets.QMessageBox.AcceptRole)
    no_btn = box.addButton(T.tr("action.keep_working"), QtWidgets.QMessageBox.RejectRole)
    _tune_buttons(yes_btn, no_btn)

    box.setDefaultButton(no_btn)
    box.exec_()
    return box.clickedButton() is yes_btn


def ask_save_settings(parent: QtWidgets.QWidget) -> bool:
    """Confirm saving settings changes."""
    title = T.tr("app.title")
    text = T.tr("dialog.settings_save_confirm")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, title, text, parent=parent)
    _sanitize_window_flags(box)

    yes_btn = box.addButton(T.tr("settings.buttons.save"), QtWidgets.QMessageBox.AcceptRole)
    no_btn = box.addButton(T.tr("ctrl.cancel"), QtWidgets.QMessageBox.RejectRole)
    _tune_buttons(yes_btn, no_btn)

    box.setDefaultButton(no_btn)
    box.exec_()
    return box.clickedButton() is yes_btn


def ask_restore_defaults(parent: QtWidgets.QWidget) -> bool:
    """Confirm restoring defaults (overwrites current settings)."""
    title = T.tr("app.title")
    text = T.tr("dialog.settings_restore_confirm")
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, title, text, parent=parent)
    _sanitize_window_flags(box)

    yes_btn = box.addButton(T.tr("settings.buttons.restore_defaults"), QtWidgets.QMessageBox.AcceptRole)
    no_btn = box.addButton(T.tr("ctrl.cancel"), QtWidgets.QMessageBox.RejectRole)
    _tune_buttons(yes_btn, no_btn)

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
    _sanitize_window_flags(dlg)
    dlg.setWindowTitle(T.tr("dialog.conflict.title"))

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout)

    lbl = QtWidgets.QLabel(T.tr("dialog.conflict.text", name=stem))
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    rb_skip = QtWidgets.QRadioButton(T.tr("dialog.conflict.skip"))
    rb_over = QtWidgets.QRadioButton(T.tr("dialog.conflict.overwrite"))
    rb_new = QtWidgets.QRadioButton(T.tr("dialog.conflict.new_name"))
    rb_skip.setChecked(True)

    layout.addWidget(rb_skip)
    layout.addWidget(rb_over)
    layout.addWidget(rb_new)

    name_edit = QtWidgets.QLineEdit()
    name_edit.setMinimumHeight(BASE_H)
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    def on_toggle() -> None:
        name_edit.setEnabled(rb_new.isChecked())

    rb_new.toggled.connect(on_toggle)

    cb_all = QtWidgets.QCheckBox(T.tr("dialog.conflict.apply_all"))
    cb_all.setMinimumHeight(BASE_H)
    layout.addWidget(cb_all)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    layout.addWidget(btns)

    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    cancel_btn = btns.button(QtWidgets.QDialogButtonBox.Cancel)
    if ok_btn and cancel_btn:
        _tune_buttons(ok_btn, cancel_btn)

    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)

    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return "skip", "", False

    if rb_over.isChecked():
        return "overwrite", "", cb_all.isChecked()
    if rb_new.isChecked():
        return "new", name_edit.text().strip(), False
    return "skip", "", cb_all.isChecked()


def ask_download_duplicate(parent: QtWidgets.QWidget, *, title: str, suggested_name: str) -> Tuple[str, str]:
    """
    Download duplicate dialog.
    Returns (action, new_name) where action ∈ {"skip","overwrite","rename"}.
    """
    dlg = QtWidgets.QDialog(parent)
    _sanitize_window_flags(dlg)
    dlg.setWindowTitle(T.tr("app.title"))

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout)

    lbl = QtWidgets.QLabel(T.tr("down.dialog.exists.text", title=title))
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    rb_skip = QtWidgets.QRadioButton(T.tr("down.dialog.exists.skip"))
    rb_over = QtWidgets.QRadioButton(T.tr("down.dialog.exists.overwrite"))
    rb_ren = QtWidgets.QRadioButton(T.tr("down.dialog.exists.rename"))
    rb_skip.setChecked(True)

    row = QtWidgets.QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(rb_skip)
    row.addWidget(rb_over)
    row.addWidget(rb_ren)
    layout.addLayout(row)

    name_edit = QtWidgets.QLineEdit(suggested_name)
    name_edit.setMinimumHeight(BASE_H)
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    def on_toggle() -> None:
        name_edit.setEnabled(rb_ren.isChecked())

    rb_ren.toggled.connect(on_toggle)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    layout.addWidget(btns)

    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    cancel_btn = btns.button(QtWidgets.QDialogButtonBox.Cancel)
    if ok_btn and cancel_btn:
        _tune_buttons(ok_btn, cancel_btn)

    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)

    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return "skip", ""

    if rb_over.isChecked():
        return "overwrite", ""
    if rb_ren.isChecked():
        return "rename", name_edit.text().strip()
    return "skip", ""


def ask_restart_required(parent: QtWidgets.QWidget) -> bool:
    """
    Restart decision dialog.
    Returns True if restart requested.
    """

    dlg = QtWidgets.QDialog(parent)
    _sanitize_window_flags(dlg)
    dlg.setWindowTitle(T.tr("dialog.restart_required.title"))
    dlg.setModal(True)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay)

    lbl = QtWidgets.QLabel(T.tr("dialog.restart_required.text"))
    lbl.setWordWrap(True)
    lay.addWidget(lbl)

    btns = QtWidgets.QHBoxLayout()
    btns.setSpacing(6)
    btns.addStretch(1)

    btn_restart = QtWidgets.QPushButton(T.tr("dialog.restart_required.restart"))
    btn_later = QtWidgets.QPushButton(T.tr("dialog.restart_required.later"))
    _tune_buttons(btn_restart, btn_later)

    btns.addWidget(btn_restart)
    btns.addWidget(btn_later)
    lay.addLayout(btns)

    result = {"restart": False}

    def _do_restart() -> None:
        result["restart"] = True
        dlg.accept()

    def _do_later() -> None:
        result["restart"] = False
        dlg.reject()

    btn_restart.clicked.connect(_do_restart)
    btn_later.clicked.connect(_do_later)

    dlg.exec_()
    return bool(result["restart"])