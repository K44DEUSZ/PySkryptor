# view/views/dialogs.py
from __future__ import annotations

from pathlib import Path
from typing import Tuple, Optional, List
from PyQt5 import QtCore, QtWidgets

from view.utils.translating import Translator as T


BASE_H = 24
PAD = 12
SPACING = 8

DLG_MIN_W = 560
DLG_MAX_W = 760


def _sanitize_window_flags(w: QtWidgets.QWidget) -> None:
    """Remove the Windows '?' (Context Help) button for a cleaner title bar."""
    flags = w.windowFlags()
    flags &= ~QtCore.Qt.WindowContextHelpButtonHint
    w.setWindowFlags(flags)


def _tune_dialog_layout(layout: QtWidgets.QLayout) -> None:
    layout.setContentsMargins(PAD, PAD, PAD, PAD)
    layout.setSpacing(SPACING)


def _tune_buttons(*buttons: QtWidgets.QAbstractButton) -> None:
    for b in buttons:
        b.setMinimumHeight(BASE_H)
        b.setMinimumWidth(140)


def _tune_dialog_window(dlg: QtWidgets.QDialog) -> None:
    _sanitize_window_flags(dlg)
    dlg.setModal(True)
    # Keep the title bar minimal. We show context inside the dialog content.
    dlg.setWindowTitle("")
    dlg.setMinimumWidth(DLG_MIN_W)
    dlg.setMaximumWidth(DLG_MAX_W)


def _bold_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    f = lbl.font()
    f.setBold(True)
    lbl.setFont(f)
    lbl.setWordWrap(True)
    return lbl


def _wrap_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    return lbl


def _message_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    message: str,
    header: str | None = None,
    ok_text: str | None = None,
) -> None:
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg)
    if title:
        dlg.setWindowTitle(title)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay)

    if header:
        lay.addWidget(_bold_label(header))

    lay.addWidget(_wrap_label(message))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(ok_text or T.tr("ctrl.ok"))
        _tune_buttons(ok_btn)
    btns.accepted.connect(dlg.accept)

    lay.addWidget(btns)
    dlg.exec_()


def _confirm_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    message: str,
    accept_text: str,
    reject_text: str,
    header: str | None = None,
    default_accept: bool = False,
) -> bool:
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg)
    if title:
        dlg.setWindowTitle(title)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay)

    if header:
        lay.addWidget(_bold_label(header))

    lay.addWidget(_wrap_label(message))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox()
    btn_accept = btns.addButton(accept_text, QtWidgets.QDialogButtonBox.AcceptRole)
    btn_reject = btns.addButton(reject_text, QtWidgets.QDialogButtonBox.RejectRole)
    _tune_buttons(btn_accept, btn_reject)

    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    lay.addWidget(btns)

    if default_accept:
        btn_accept.setDefault(True)
        btn_accept.setFocus()
    else:
        btn_reject.setDefault(True)
        btn_reject.setFocus()

    return dlg.exec_() == QtWidgets.QDialog.Accepted


# ----- Critical startup dialogs -----

def critical_defaults_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = T.tr("dialog.critical.application_error.title")
    text = T.tr("dialog.critical.defaults_missing.text")
    _message_dialog(parent, title=title, message=text, header=title)


def critical_locales_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = T.tr("dialog.critical.localization_error.title")
    text = T.tr("dialog.critical.locales_missing.text")
    _message_dialog(parent, title=title, message=text, header=title)


def critical_config_load_failed_and_exit(parent: QtWidgets.QWidget | None, details: str = "") -> None:
    title = T.tr("dialog.critical.pyskryptor_error.title")
    text = T.tr("dialog.critical.config_load_failed.text")
    msg = text
    d = str(details or "").strip()
    if d:
        msg = f"{text}\n\n{d}"
    _message_dialog(parent, title=title, message=msg, header=title)


def info_settings_restored(parent: QtWidgets.QWidget | None = None) -> None:
    _message_dialog(parent, title="", message=T.tr("dialog.settings_restored"))


# ----- Live / audio dialogs -----

def show_no_microphone_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg)
    dlg.setWindowTitle("")

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay)

    lay.addWidget(_bold_label(T.tr("live.dialog.no_devices.title")))
    lay.addWidget(_wrap_label(T.tr("live.dialog.no_devices.text")))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        _tune_buttons(ok_btn)
    btns.accepted.connect(dlg.accept)
    lay.addWidget(btns)

    dlg.exec_()


# ----- Downloader info dialogs -----

def info_playlist_not_supported(parent: QtWidgets.QWidget | None = None) -> None:
    _message_dialog(parent, title="", message=T.tr("down.dialog.playlist_not_supported.text"))


# ----- Runtime confirmations -----

def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    text = T.tr("dialog.cancel_confirm", detail="")
    return _confirm_dialog(
        parent,
        title="",
        message=text,
        accept_text=T.tr("action.cancel_now"),
        reject_text=T.tr("action.keep_working"),
        header=None,
        default_accept=False,
    )


def ask_save_settings(parent: QtWidgets.QWidget) -> bool:
    text = T.tr("dialog.settings_save_confirm")
    return _confirm_dialog(
        parent,
        title="",
        message=text,
        accept_text=T.tr("settings.buttons.save"),
        reject_text=T.tr("ctrl.cancel"),
        header=None,
        default_accept=False,
    )


def ask_restore_defaults(parent: QtWidgets.QWidget) -> bool:
    text = T.tr("dialog.settings_restore_confirm")
    return _confirm_dialog(
        parent,
        title="",
        message=text,
        accept_text=T.tr("settings.buttons.restore_defaults"),
        reject_text=T.tr("ctrl.cancel"),
        header=None,
        default_accept=False,
    )


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> Tuple[str, str, bool]:
    """
    Transcript conflict dialog.
    Returns (action, new_stem, apply_all) where action ∈ {"skip","overwrite","new"}.
    'apply_all' is applicable to skip/overwrite.
    """
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg)
    dlg.setWindowTitle("")

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout)

    layout.addWidget(_bold_label(T.tr("dialog.conflict.title")))
    layout.addWidget(_wrap_label(T.tr("dialog.conflict.text", name=stem)))

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
        cancel_btn.setDefault(True)
        cancel_btn.setFocus()

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
    _tune_dialog_window(dlg)
    dlg.setWindowTitle("")

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout)

    layout.addWidget(_wrap_label(T.tr("down.dialog.exists.text", title=title)))

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
        cancel_btn.setDefault(True)
        cancel_btn.setFocus()

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
    _tune_dialog_window(dlg)
    dlg.setWindowTitle("")

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay)

    lay.addWidget(_bold_label(T.tr("dialog.restart_required.title")))
    lay.addWidget(_wrap_label(T.tr("dialog.restart_required.text")))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox()
    btn_restart = btns.addButton(T.tr("dialog.restart_required.restart"), QtWidgets.QDialogButtonBox.AcceptRole)
    btn_later = btns.addButton(T.tr("dialog.restart_required.later"), QtWidgets.QDialogButtonBox.RejectRole)
    _tune_buttons(btn_restart, btn_later)

    btn_restart.clicked.connect(dlg.accept)
    btn_later.clicked.connect(dlg.reject)

    lay.addWidget(btns)

    return dlg.exec_() == QtWidgets.QDialog.Accepted


# ----- Session finish helpers -----

def ask_open_transcripts_folder(parent: QtWidgets.QWidget, session_dir: str) -> bool:
    """Show a minimal 'done' dialog. Returns True if user wants to open the session folder."""
    # Reuse existing translations to avoid introducing new locale keys.
    msg = f"{T.tr('log.done')}\n{session_dir}" if session_dir else T.tr('log.done')
    return _confirm_dialog(
        parent,
        title="",
        message=msg,
        accept_text=T.tr("files.open_output"),
        reject_text="OK",
        header=T.tr("status.done"),
        default_accept=False,
    )


def ask_open_downloads_folder(parent: QtWidgets.QWidget, downloaded_path: str) -> bool:
    """Show a minimal 'download finished' dialog. Returns True if user wants to open downloads folder."""
    name = ""
    try:
        p = Path(downloaded_path)
        name = p.name
    except Exception:
        name = str(downloaded_path or "")

    msg = T.tr("down.log.downloaded_prefix")
    if name:
        msg = f"{msg} {name}"

    return _confirm_dialog(
        parent,
        title="",
        message=msg,
        accept_text=T.tr("down.open_folder"),
        reject_text="OK",
        header=T.tr("status.done"),
        default_accept=False,
    )


def show_info(parent: QtWidgets.QWidget | None, *, title: str, message: str, header: str | None = None) -> None:
    _message_dialog(parent, title=title, message=message, header=header, ok_text=T.tr("ctrl.ok"))


def show_error(parent: QtWidgets.QWidget | None, *, title: str, message: str, header: str | None = None) -> None:
    _message_dialog(parent, title=title, message=message, header=header, ok_text=T.tr("ctrl.ok"))
