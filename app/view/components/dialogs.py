# app/view/components/dialogs.py
from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.controller.support.localization import Translator
from app.model.config.app_config import AppConfig as Config
from app.model.helpers.string_utils import sanitize_filename
from app.view.support.theme_runtime import apply_windows_dark_titlebar
from app.view.support.widget_setup import setup_button, setup_input
from app.view.ui_config import ui


# ----- Internal helpers -----
class _NoCloseFilter(QtCore.QObject):
    """Event filter that blocks closing the dialog via window controls or ESC."""

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        try:
            et = event.type()
            if et == QtCore.QEvent.Type.Close:
                event.ignore()
                return True
            if et == QtCore.QEvent.Type.KeyPress and isinstance(event, QtGui.QKeyEvent):
                if event.key() == QtCore.Qt.Key.Key_Escape:
                    event.ignore()
                    return True
        except Exception:
            pass
        return False


def _sanitize_window_flags(w: QtWidgets.QWidget) -> None:
    """Remove the Windows '?' (Context Help) button."""
    flags = w.windowFlags()
    flags &= ~QtCore.Qt.WindowType.WindowContextHelpButtonHint
    w.setWindowFlags(flags)


def _lock_close(dlg: QtWidgets.QDialog) -> None:
    """Disable window close button and ignore close/ESC."""
    try:
        dlg.setWindowFlag(QtCore.Qt.WindowType.WindowCloseButtonHint, False)
        dlg.setWindowFlag(QtCore.Qt.WindowType.WindowSystemMenuHint, False)
    except Exception:
        pass

    flt = _NoCloseFilter(dlg)
    dlg.installEventFilter(flt)
    setattr(dlg, "_no_close_filter", flt)


def _tune_dialog_layout(layout: QtWidgets.QLayout, cfg) -> None:
    layout.setContentsMargins(cfg.margin, cfg.margin, cfg.margin, cfg.margin)
    layout.setSpacing(cfg.spacing)


def _tune_buttons(cfg, *buttons: QtWidgets.QAbstractButton) -> None:
    for b in buttons:
        setup_button(b, min_h=cfg.control_min_h, min_w=cfg.button_min_w)


def _tune_dialog_window(dlg: QtWidgets.QDialog, cfg) -> None:
    _sanitize_window_flags(dlg)
    dlg.setModal(True)
    dlg.setWindowTitle(Config.APP_NAME)
    try:
        dlg.setWindowIcon(QtWidgets.QApplication.windowIcon())
    except Exception:
        pass
    dlg.setMinimumWidth(cfg.dialog_min_w)
    dlg.setMaximumWidth(cfg.dialog_max_w)
    apply_windows_dark_titlebar(dlg)


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


# ----- Base dialog / button builders -----
def _message_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    message: str,
    header: str | None = None,
    ok_text: str | None = None,
    no_close: bool = False,
) -> None:
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)
    dlg.setWindowTitle(str(title or Config.APP_NAME))
    if no_close:
        _lock_close(dlg)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    if header:
        lay.addWidget(_bold_label(header))

    lay.addWidget(_wrap_label(message))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(ok_text or Translator.tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
        ok_btn.setDefault(True)
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
    no_close: bool = False,
) -> bool:
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)
    dlg.setWindowTitle(str(title or Config.APP_NAME))
    if no_close:
        _lock_close(dlg)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    if header:
        lay.addWidget(_bold_label(header))

    lay.addWidget(_wrap_label(message))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox()
    btn_accept = btns.addButton(accept_text, QtWidgets.QDialogButtonBox.AcceptRole)
    btn_reject = btns.addButton(reject_text, QtWidgets.QDialogButtonBox.RejectRole)
    _tune_buttons(cfg, btn_accept, btn_reject)

    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    lay.addWidget(btns)

    if default_accept:
        btn_accept.setDefault(True)
    else:
        btn_reject.setDefault(True)

    return dlg.exec_() == QtWidgets.QDialog.Accepted


def _terminate_application() -> None:
    app = cast(QtWidgets.QApplication | None, QtWidgets.QApplication.instance())
    if app is None:
        return

    try:
        app.closeAllWindows()
    except Exception:
        pass

    try:
        app.quit()
    except Exception:
        pass


# ----- Critical startup dialogs -----
def critical_defaults_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = Translator.tr("dialog.critical.application_error.title")
    text = Translator.tr("dialog.critical.defaults_missing.text")
    _message_dialog(parent, title=title, message=text, header=title, no_close=True)
    _terminate_application()


def critical_locales_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = Translator.tr("dialog.critical.localization_error.title")
    text = Translator.tr("dialog.critical.locales_missing.text")
    _message_dialog(parent, title=title, message=text, header=title, no_close=True)
    _terminate_application()


def critical_startup_error_and_exit(parent: QtWidgets.QWidget | None, details: str = "") -> None:
    title = Translator.tr("dialog.critical.pyskryptor_error.title")
    msg = Translator.tr("dialog.critical.config_load_failed.text", detail=str(details or "").strip())
    _message_dialog(parent, title=title, message=msg, header=title, no_close=True)
    _terminate_application()


def critical_config_load_failed_choice(parent: QtWidgets.QWidget | None, details: str = "") -> str:
    """Returns: 'exit' | 'restore_defaults'."""
    title = Translator.tr("dialog.critical.pyskryptor_error.title")
    msg = Translator.tr("dialog.critical.config_load_failed.text", detail=str(details or "").strip())
    ok_restore = _confirm_dialog(
        parent,
        title=title,
        message=msg,
        accept_text=Translator.tr("settings.buttons.restore_defaults"),
        reject_text=Translator.tr("ctrl.exit"),
        header=title,
        default_accept=False,
        no_close=True,
    )
    return "restore_defaults" if ok_restore else "exit"


# ----- Live / audio dialogs -----
def show_no_microphone_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    lay.addWidget(_bold_label(Translator.tr("live.dialog.no_devices.title")))
    lay.addWidget(_wrap_label(Translator.tr("live.dialog.no_devices.text")))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(Translator.tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
    btns.accepted.connect(dlg.accept)
    lay.addWidget(btns)

    dlg.exec_()


# ----- Downloader info dialogs -----
def info_playlist_not_supported(parent: QtWidgets.QWidget | None = None) -> None:
    _message_dialog(parent, title="", message=Translator.tr("down.dialog.playlist_not_supported.text"))


# ----- Availability / network dialogs -----
def _availability_dialog(parent: QtWidgets.QWidget | None, *, text_key: str) -> None:
    _message_dialog(
        parent,
        title=Translator.tr("dialog.availability.network_offline.title"),
        message=Translator.tr(text_key),
        header=Translator.tr("dialog.availability.network_offline.header"),
        ok_text=Translator.tr("ctrl.ok"),
    )


def show_downloader_offline_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    _availability_dialog(parent, text_key="dialog.availability.downloader_offline.text")


def show_files_url_offline_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    _availability_dialog(parent, text_key="dialog.availability.files_url_offline.text")


def show_files_only_url_offline_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    _availability_dialog(parent, text_key="dialog.availability.files_only_url_offline.text")


# ----- Runtime confirmation dialogs -----
def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    text = Translator.tr("dialog.cancel_confirm", detail="")
    return _confirm_dialog(
        parent,
        title="",
        message=text,
        accept_text=Translator.tr("action.cancel_now"),
        reject_text=Translator.tr("action.keep_working"),
        header=None,
        default_accept=False,
    )


def ask_save_settings(parent: QtWidgets.QWidget) -> bool:
    text = Translator.tr("dialog.settings_save_confirm")
    return _confirm_dialog(
        parent,
        title="",
        message=text,
        accept_text=Translator.tr("settings.buttons.save"),
        reject_text=Translator.tr("ctrl.cancel"),
        header=None,
        default_accept=False,
    )


def ask_restore_defaults(parent: QtWidgets.QWidget) -> bool:
    text = Translator.tr("dialog.settings_restore_confirm")
    return _confirm_dialog(
        parent,
        title="",
        message=text,
        accept_text=Translator.tr("settings.buttons.restore_defaults"),
        reject_text=Translator.tr("ctrl.cancel"),
        header=None,
        default_accept=False,
    )


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> tuple[str, str, bool]:
    """Transcript conflict dialog."""
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)
    _lock_close(dlg)

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout, cfg)

    layout.addWidget(_bold_label(Translator.tr("dialog.conflict.title")))
    layout.addWidget(_wrap_label(Translator.tr("dialog.conflict.text", name=stem)))

    rb_skip = QtWidgets.QRadioButton(Translator.tr("dialog.conflict.skip"))
    rb_over = QtWidgets.QRadioButton(Translator.tr("dialog.conflict.overwrite"))
    rb_new = QtWidgets.QRadioButton(Translator.tr("dialog.conflict.new_name"))
    rb_skip.setChecked(True)

    layout.addWidget(rb_skip)
    layout.addWidget(rb_over)
    layout.addWidget(rb_new)

    name_edit = QtWidgets.QLineEdit()
    setup_input(name_edit, min_h=cfg.control_min_h)
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    cb_all = QtWidgets.QCheckBox(Translator.tr("dialog.conflict.apply_all"))
    cb_all.setMinimumHeight(cfg.control_min_h)
    layout.addWidget(cb_all)

    def sync_ui() -> None:
        is_new = rb_new.isChecked()
        name_edit.setEnabled(is_new)
        if is_new:
            cb_all.setChecked(False)
            cb_all.setEnabled(False)
        else:
            cb_all.setEnabled(True)

    rb_new.toggled.connect(sync_ui)
    rb_skip.toggled.connect(sync_ui)
    rb_over.toggled.connect(sync_ui)
    sync_ui()

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    layout.addWidget(btns)

    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(Translator.tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
        ok_btn.setDefault(True)

    btns.accepted.connect(dlg.accept)

    dlg.exec_()

    if rb_over.isChecked():
        return "overwrite", "", cb_all.isChecked()
    if rb_new.isChecked():
        return "new", sanitize_filename(name_edit.text().strip()), False
    return "skip", "", cb_all.isChecked()


def ask_download_duplicate(
    parent: QtWidgets.QWidget,
    *,
    title: str,
    suggested_name: str,
) -> tuple[str, str, bool]:
    """Download duplicate dialog."""
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)
    _lock_close(dlg)

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout, cfg)

    layout.addWidget(_wrap_label(Translator.tr("down.dialog.exists.text", title=title)))

    rb_skip = QtWidgets.QRadioButton(Translator.tr("down.dialog.exists.skip"))
    rb_over = QtWidgets.QRadioButton(Translator.tr("down.dialog.exists.overwrite"))
    rb_ren = QtWidgets.QRadioButton(Translator.tr("down.dialog.exists.rename"))
    rb_skip.setChecked(True)

    row = QtWidgets.QHBoxLayout()
    row.setSpacing(cfg.spacing)
    row.addWidget(rb_skip)
    row.addWidget(rb_over)
    row.addWidget(rb_ren)
    layout.addLayout(row)

    name_edit = QtWidgets.QLineEdit(suggested_name)
    setup_input(name_edit, min_h=cfg.control_min_h)
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    cb_all = QtWidgets.QCheckBox(Translator.tr("down.dialog.exists.apply_all"))
    cb_all.setMinimumHeight(cfg.control_min_h)
    layout.addWidget(cb_all)

    def sync_ui() -> None:
        is_ren = rb_ren.isChecked()
        name_edit.setEnabled(is_ren)
        if is_ren:
            cb_all.setChecked(False)
            cb_all.setEnabled(False)
        else:
            cb_all.setEnabled(True)

    rb_ren.toggled.connect(sync_ui)
    rb_skip.toggled.connect(sync_ui)
    rb_over.toggled.connect(sync_ui)
    sync_ui()

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    layout.addWidget(btns)

    ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(Translator.tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
        ok_btn.setDefault(True)

    btns.accepted.connect(dlg.accept)

    dlg.exec_()

    if rb_over.isChecked():
        return "overwrite", "", cb_all.isChecked()
    if rb_ren.isChecked():
        return "rename", sanitize_filename(name_edit.text().strip()), False
    return "skip", "", cb_all.isChecked()


def ask_restart_required(parent: QtWidgets.QWidget) -> bool:
    """Restart decision dialog."""
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    lay.addWidget(_bold_label(Translator.tr("dialog.restart_required.title")))
    lay.addWidget(_wrap_label(Translator.tr("dialog.restart_required.text")))
    lay.addStretch(1)

    btns = QtWidgets.QDialogButtonBox()
    btn_restart = btns.addButton(Translator.tr("dialog.restart_required.restart"), QtWidgets.QDialogButtonBox.AcceptRole)
    btn_later = btns.addButton(Translator.tr("dialog.restart_required.later"), QtWidgets.QDialogButtonBox.RejectRole)
    _tune_buttons(cfg, btn_restart, btn_later)

    btn_restart.clicked.connect(dlg.accept)
    btn_later.clicked.connect(dlg.reject)

    lay.addWidget(btns)

    return dlg.exec_() == QtWidgets.QDialog.Accepted


# ----- Session finish helpers -----
def ask_open_transcripts_folder(parent: QtWidgets.QWidget, session_dir: str) -> bool:
    """Returns True if user wants to open the session folder."""
    base = Translator.tr("dialog.info.done")
    msg = f"{base}\n{session_dir}" if session_dir else base
    return _confirm_dialog(
        parent,
        title="",
        message=msg,
        accept_text=Translator.tr("files.open_output"),
        reject_text=Translator.tr("ctrl.ok"),
        header=Translator.tr("status.done"),
        default_accept=False,
    )


def ask_open_downloads_folder(parent: QtWidgets.QWidget, downloaded_path: str) -> bool:
    """Returns True if user wants to open downloads folder."""
    file_name = ""
    try:
        p = Path(downloaded_path)
        file_name = p.name
    except Exception:
        file_name = str(downloaded_path or "")

    msg = Translator.tr("dialog.info.downloaded_prefix")
    if file_name:
        msg = f"{msg} {file_name}"

    return _confirm_dialog(
        parent,
        title="",
        message=msg,
        accept_text=Translator.tr("down.open_folder"),
        reject_text=Translator.tr("ctrl.ok"),
        header=Translator.tr("status.done"),
        default_accept=False,
    )


# ----- Generic info / error wrappers -----
def show_info(parent: QtWidgets.QWidget | None, *, title: str, message: str, header: str | None = None) -> None:
    _message_dialog(parent, title=title, message=message, header=header, ok_text=Translator.tr("ctrl.ok"))


def show_error(
    parent: QtWidgets.QWidget | None,
    key: str | None = None,
    params: dict | None = None,
    *,
    title: str | None = None,
    message: str | None = None,
    header: str | None = None,
) -> None:
    """Standard runtime error dialog (closable)."""

    def _should_hide_detail(text: str) -> bool:
        s = str(text or "")
        if not s:
            return False
        low = s.lower()
        if "traceback" in low:
            return True
        if ".py" in low and ("line " in low or "file \"" in low or "file '" in low):
            return True
        if re.search(r"\.py\s*:\s*\d+", s):
            return True
        if re.search(r"file\s+\".*?\.py\"\s*,\s*line\s*\d+", low):
            return True
        return False

    def _sanitize_message(text: str) -> str:
        if _should_hide_detail(text):
            return Translator.tr("dialog.error.unexpected", msg=Translator.tr("dialog.error.details_hidden"))
        return text

    if title is not None or message is not None or header is not None:
        _message_dialog(
            parent,
            title=title or Translator.tr("dialog.error.title"),
            message=_sanitize_message(message or ""),
            header=header,
            ok_text=Translator.tr("ctrl.ok"),
        )
        return

    msg = _sanitize_message(Translator.tr(str(key or ""), **(params or {})))
    _message_dialog(
        parent,
        title=Translator.tr("dialog.error.title"),
        message=msg,
        header=Translator.tr("dialog.error.header"),
        ok_text=Translator.tr("ctrl.ok"),
    )
