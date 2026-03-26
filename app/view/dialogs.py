# app/view/dialogs.py
from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.model.services.localization_service import tr
from app.model.config.app_meta import AppMeta
from app.model.helpers.string_utils import sanitize_filename
from app.view.support.theme_runtime import apply_windows_dark_titlebar
from app.view.support.widget_setup import (
    build_layout_host,
    setup_label,
    setup_button,
    setup_input,
    setup_spinbox,
    setup_text_editor,
)
from app.view.ui_config import ui


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
        except (AttributeError, RuntimeError, TypeError):
            return False
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
    except (AttributeError, RuntimeError, TypeError):
        return

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
    dlg.setWindowTitle(AppMeta.NAME)
    try:
        dlg.setWindowIcon(QtWidgets.QApplication.windowIcon())
    except (AttributeError, RuntimeError, TypeError):
        return
    dlg.setMinimumWidth(cfg.dialog_min_w)
    dlg.setMaximumWidth(cfg.dialog_max_w)
    apply_windows_dark_titlebar(dlg)

def _wrap_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    return lbl


def _section_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    setup_label(lbl, role="sectionTitle")
    lbl.setWordWrap(True)
    return lbl

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
    dlg.setWindowTitle(str(title or AppMeta.NAME))
    if no_close:
        _lock_close(dlg)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    if header:
        header_label = QtWidgets.QLabel(header)
        setup_label(header_label, role="sectionTitle")
        header_label.setWordWrap(True)
        lay.addWidget(header_label)

    lay.addWidget(_wrap_label(message))
    lay.addStretch(1)

    button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    ok_btn = button_box.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(ok_text or tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
        ok_btn.setDefault(True)
    button_box.accepted.connect(dlg.accept)

    lay.addWidget(button_box)
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
    dlg.setWindowTitle(str(title or AppMeta.NAME))
    if no_close:
        _lock_close(dlg)

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    if header:
        header_label = QtWidgets.QLabel(header)
        setup_label(header_label, role="sectionTitle")
        header_label.setWordWrap(True)
        lay.addWidget(header_label)

    lay.addWidget(_wrap_label(message))
    lay.addStretch(1)

    button_box = QtWidgets.QDialogButtonBox()
    btn_accept = button_box.addButton(accept_text, QtWidgets.QDialogButtonBox.AcceptRole)
    btn_reject = button_box.addButton(reject_text, QtWidgets.QDialogButtonBox.RejectRole)
    _tune_buttons(cfg, btn_accept, btn_reject)

    button_box.accepted.connect(dlg.accept)
    button_box.rejected.connect(dlg.reject)
    lay.addWidget(button_box)

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
    except (AttributeError, RuntimeError, TypeError):
        return

    try:
        app.quit()
    except (AttributeError, RuntimeError, TypeError):
        return


def critical_defaults_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = tr("dialog.critical.application_error.title")
    text = tr("dialog.critical.defaults_missing.text")
    _message_dialog(parent, title=title, message=text, header=title, no_close=True)
    _terminate_application()

def critical_locales_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = tr("dialog.critical.localization_error.title")
    text = tr("dialog.critical.locales_missing.text")
    _message_dialog(parent, title=title, message=text, header=title, no_close=True)
    _terminate_application()

def critical_startup_error_and_exit(parent: QtWidgets.QWidget | None, details: str = "") -> None:
    title = tr("dialog.critical.pyskryptor_error.title")
    msg = tr("dialog.critical.config_load_failed.text", detail=str(details or "").strip())
    _message_dialog(parent, title=title, message=msg, header=title, no_close=True)
    _terminate_application()

def critical_config_load_failed_choice(parent: QtWidgets.QWidget | None, details: str = "") -> str:
    """Returns: 'exit' | 'restore_defaults'."""
    title = tr("dialog.critical.pyskryptor_error.title")
    msg = tr("dialog.critical.config_load_failed.text", detail=str(details or "").strip())
    ok_restore = _confirm_dialog(
        parent,
        title=title,
        message=msg,
        accept_text=tr("settings.buttons.restore_defaults"),
        reject_text=tr("ctrl.exit"),
        header=title,
        default_accept=False,
        no_close=True,
    )
    return "restore_defaults" if ok_restore else "exit"


def show_no_microphone_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)
    dlg.setWindowTitle(tr("dialog.live.no_devices.title"))

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)

    lay.addWidget(_section_label(tr("dialog.live.no_devices.header")))
    lay.addWidget(_wrap_label(tr("dialog.live.no_devices.text")))
    lay.addStretch(1)

    button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    ok_btn = button_box.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
    button_box.accepted.connect(dlg.accept)
    lay.addWidget(button_box)

    dlg.exec_()

def ask_bulk_add_plan(
    parent: QtWidgets.QWidget | None,
    *,
    origin_kind: str,
    count: int,
    origin_label: str = "",
    sample_titles: list[str] | None = None,
    default_limit: int = 0,
    target_label: str = "",
) -> tuple[str, int]:
    kind = str(origin_kind or "selection").strip().lower()
    key_map = {
        "playlist": "dialog.bulk_add.playlist",
        "folder": "dialog.bulk_add.folder",
        "file_selection": "dialog.bulk_add.selection",
        "drop": "dialog.bulk_add.drop",
        "manual_input": "dialog.bulk_add.selection",
    }
    total = int(max(0, int(count or 0)))
    default_n = int(max(1, int(default_limit or 1)))
    default_n = min(default_n, max(1, total))

    details: list[str] = [tr(key_map.get(kind, "dialog.bulk_add.selection"), count=total)]
    label = str(origin_label or "").strip()
    if label:
        details.append(tr("dialog.bulk_add.origin", origin=label))
    target = str(target_label or "").strip()
    if target:
        details.append(tr("dialog.bulk_add.target", target=target))
    preview = [str(item or "").strip() for item in list(sample_titles or []) if str(item or "").strip()]
    preview = preview[:5]

    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg, cfg)
    dlg.setWindowTitle(tr("dialog.bulk_add.title"))
    dlg.setMinimumWidth(max(cfg.dialog_min_w, 640))
    dlg.setMaximumWidth(max(cfg.dialog_max_w, 860))

    lay = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(lay, cfg)
    lay.addWidget(_section_label(tr("dialog.bulk_add.header")))
    lay.addWidget(_wrap_label("\n".join(details)))

    if preview:
        preview_label = QtWidgets.QLabel(tr("dialog.bulk_add.preview_header"), dlg)
        preview_label.setProperty("role", "fieldLabel")
        lay.addWidget(preview_label)

        preview_box = QtWidgets.QPlainTextEdit(dlg)
        preview_box.setReadOnly(True)
        preview_box.setPlainText("\n".join(f"• {item}" for item in preview))
        preview_box.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        preview_box.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        preview_box.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        setup_text_editor(preview_box)
        visible_rows = max(1, len(preview))
        line_h = preview_box.fontMetrics().lineSpacing()
        frame_h = preview_box.frameWidth() * 2
        doc_margin = int(preview_box.document().documentMargin() * 2)
        preview_h = (line_h * visible_rows) + frame_h + doc_margin + int(cfg.space_s)
        preview_box.setFixedHeight(max(cfg.control_min_h * 2, preview_h))
        lay.addWidget(preview_box)

    rb_all = QtWidgets.QRadioButton(tr("dialog.bulk_add.option_all"))
    rb_first = QtWidgets.QRadioButton(tr("dialog.bulk_add.option_first_n"))

    rb_first.setChecked(total > default_n)
    if not rb_first.isChecked():
        rb_all.setChecked(True)

    lay.addWidget(rb_all)

    first_row_host, first_row = build_layout_host(layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)
    first_row.addWidget(rb_first)
    sp_count = QtWidgets.QSpinBox(dlg)
    sp_count.setMinimum(1)
    sp_count.setMaximum(max(1, total))
    sp_count.setValue(default_n)
    sp_count.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
    setup_spinbox(sp_count, min_h=cfg.control_min_h)
    first_row.addWidget(sp_count)
    first_row.addWidget(QtWidgets.QLabel(tr("dialog.bulk_add.option_first_suffix", total=total)))
    first_row.addStretch(1)
    lay.addWidget(first_row_host)
    lay.addStretch(1)

    def _sync_spinbox() -> None:
        sp_count.setEnabled(rb_first.isChecked())

    rb_all.toggled.connect(_sync_spinbox)
    rb_first.toggled.connect(_sync_spinbox)
    _sync_spinbox()

    button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    ok_btn = button_box.button(QtWidgets.QDialogButtonBox.Ok)
    cancel_btn = button_box.button(QtWidgets.QDialogButtonBox.Cancel)
    if ok_btn and cancel_btn:
        ok_btn.setText(tr("ctrl.add"))
        cancel_btn.setText(tr("ctrl.cancel"))
        _tune_buttons(cfg, ok_btn, cancel_btn)
        ok_btn.setDefault(True)
    button_box.accepted.connect(dlg.accept)
    button_box.rejected.connect(dlg.reject)
    lay.addWidget(button_box)

    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return "cancel", 0
    if rb_first.isChecked():
        return "first_n", int(sp_count.value())
    return "all", total


class ExpansionProgressDialog(QtWidgets.QDialog):
    """Lightweight cancellable progress dialog for source expansion."""

    cancel_requested = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        cfg = ui(parent)
        _tune_dialog_window(self, cfg)
        self.setWindowTitle(tr("dialog.expansion_progress.title"))
        self.setModal(False)
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        _lock_close(self)

        lay = QtWidgets.QVBoxLayout(self)
        _tune_dialog_layout(lay, cfg)
        lay.addWidget(_section_label(tr("dialog.expansion_progress.header")))

        self._message_label = _wrap_label(tr("dialog.expansion_progress.generic"))
        lay.addWidget(self._message_label)

        self._bar = QtWidgets.QProgressBar(self)
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        self._bar.setMinimumHeight(cfg.control_min_h)
        lay.addWidget(self._bar)
        lay.addStretch(1)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        cancel_btn = button_box.button(QtWidgets.QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setText(tr("ctrl.cancel"))
            _tune_buttons(cfg, cancel_btn)
            cancel_btn.setDefault(True)
        button_box.rejected.connect(self.cancel_requested.emit)
        lay.addWidget(button_box)

    def set_message(self, text: str) -> None:
        self._message_label.setText(str(text or tr("dialog.expansion_progress.generic")))


def _availability_dialog(parent: QtWidgets.QWidget | None, *, text_key: str) -> None:
    _message_dialog(
        parent,
        title=tr("dialog.availability.network_offline.title"),
        message=tr(text_key),
        header=tr("dialog.availability.network_offline.header"),
        ok_text=tr("ctrl.ok"),
    )

def show_downloader_offline_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    _availability_dialog(parent, text_key="dialog.availability.downloader_offline.text")

def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    text = tr("dialog.cancel_confirm")
    return _confirm_dialog(
        parent,
        title=tr("dialog.confirm.title"),
        message=text,
        accept_text=tr("action.cancel_now"),
        reject_text=tr("action.keep_working"),
        header=None,
        default_accept=False,
    )

def ask_save_settings(parent: QtWidgets.QWidget) -> bool:
    text = tr("dialog.settings_save_confirm")
    return _confirm_dialog(
        parent,
        title=tr("dialog.settings.title"),
        message=text,
        accept_text=tr("settings.buttons.save"),
        reject_text=tr("ctrl.cancel"),
        header=None,
        default_accept=False,
    )

def ask_restore_defaults(parent: QtWidgets.QWidget) -> bool:
    text = tr("dialog.settings_restore_confirm")
    return _confirm_dialog(
        parent,
        title=tr("dialog.settings.title"),
        message=text,
        accept_text=tr("settings.buttons.restore_defaults"),
        reject_text=tr("ctrl.cancel"),
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

    dlg.setWindowTitle(tr("dialog.conflict.title"))
    layout.addWidget(_section_label(tr("dialog.conflict.header")))
    layout.addWidget(_wrap_label(tr("dialog.conflict.text", name=stem)))

    rb_skip = QtWidgets.QRadioButton(tr("dialog.conflict.skip"))
    rb_over = QtWidgets.QRadioButton(tr("dialog.conflict.overwrite"))
    rb_new = QtWidgets.QRadioButton(tr("dialog.conflict.new_name"))
    rb_skip.setChecked(True)

    layout.addWidget(rb_skip)
    layout.addWidget(rb_over)
    layout.addWidget(rb_new)

    name_edit = QtWidgets.QLineEdit()
    setup_input(name_edit, min_h=cfg.control_min_h)
    name_edit.setEnabled(False)
    layout.addWidget(name_edit)

    cb_all = QtWidgets.QCheckBox(tr("dialog.conflict.apply_all"))
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

    button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    layout.addWidget(button_box)

    ok_btn = button_box.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
        ok_btn.setDefault(True)

    button_box.accepted.connect(dlg.accept)

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

    dlg.setWindowTitle(tr("dialog.down.exists.title"))
    layout.addWidget(_section_label(tr("dialog.down.exists.header")))
    layout.addWidget(_wrap_label(tr("dialog.down.exists.text", title=title)))

    rb_skip = QtWidgets.QRadioButton(tr("dialog.down.exists.skip"))
    rb_over = QtWidgets.QRadioButton(tr("dialog.down.exists.overwrite"))
    rb_ren = QtWidgets.QRadioButton(tr("dialog.down.exists.rename"))
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

    cb_all = QtWidgets.QCheckBox(tr("dialog.down.exists.apply_all"))
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

    button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
    layout.addWidget(button_box)

    ok_btn = button_box.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setText(tr("ctrl.ok"))
        _tune_buttons(cfg, ok_btn)
        ok_btn.setDefault(True)

    button_box.accepted.connect(dlg.accept)

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

    dlg.setWindowTitle(tr("dialog.restart_required.title"))
    lay.addWidget(_section_label(tr("dialog.restart_required.header")))
    lay.addWidget(_wrap_label(tr("dialog.restart_required.text")))
    lay.addStretch(1)

    button_box = QtWidgets.QDialogButtonBox()
    btn_restart = button_box.addButton(tr("dialog.restart_required.restart"), QtWidgets.QDialogButtonBox.AcceptRole)
    btn_later = button_box.addButton(tr("dialog.restart_required.later"), QtWidgets.QDialogButtonBox.RejectRole)
    _tune_buttons(cfg, btn_restart, btn_later)

    btn_restart.clicked.connect(dlg.accept)
    btn_later.clicked.connect(dlg.reject)

    lay.addWidget(button_box)

    return dlg.exec_() == QtWidgets.QDialog.Accepted

def ask_open_transcripts_folder(parent: QtWidgets.QWidget, session_dir: str) -> bool:
    """Returns True if user wants to open the session folder."""
    msg = str(session_dir or "")
    return _confirm_dialog(
        parent,
        title=tr("dialog.info.title"),
        message=msg,
        accept_text=tr("files.open_output"),
        reject_text=tr("ctrl.ok"),
        header=tr("dialog.info.done_header"),
        default_accept=False,
    )

def ask_open_downloads_folder(parent: QtWidgets.QWidget, downloaded_path: str) -> bool:
    """Returns True if user wants to open downloads folder."""
    try:
        file_name = Path(downloaded_path).name
    except (TypeError, ValueError):
        file_name = str(downloaded_path or "")

    msg = tr("dialog.info.downloaded_prefix")
    if file_name:
        msg = f"{msg} {file_name}"

    return _confirm_dialog(
        parent,
        title=tr("dialog.info.title"),
        message=msg,
        accept_text=tr("down.open_folder"),
        reject_text=tr("ctrl.ok"),
        header=tr("dialog.info.done_header"),
        default_accept=False,
    )


def show_info(parent: QtWidgets.QWidget | None, *, title: str, message: str, header: str | None = None) -> None:
    _message_dialog(parent, title=title, message=message, header=header, ok_text=tr("ctrl.ok"))

def show_error(
    parent: QtWidgets.QWidget | None,
    key: str | None = None,
    params: dict | None = None,
    *,
    title: str | None = None,
    message: str | None = None,
    header: str | None = None,
) -> None:
    """Show a runtime error through the standard dialog path."""

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
            return tr("dialog.error.unexpected", msg=tr("dialog.error.details_hidden"))
        return text

    if title is not None or message is not None or header is not None:
        _message_dialog(
            parent,
            title=title or tr("dialog.error.title"),
            message=_sanitize_message(message or ""),
            header=header,
            ok_text=tr("ctrl.ok"),
        )
        return

    msg = _sanitize_message(tr(str(key or ""), **(params or {})))
    _message_dialog(
        parent,
        title=tr("dialog.error.title"),
        message=msg,
        header=tr("dialog.error.header"),
        ok_text=tr("ctrl.ok"),
    )
