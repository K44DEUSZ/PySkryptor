# ui/views/dialogs.py
from __future__ import annotations

from typing import Tuple, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from ui.utils.translating import Translator as T


def _app_title() -> str:
    title = T.tr("app.title")
    return title if title and title != "app.title" else "PySkryptor"


def _window_title(subtitle: Optional[str] = None) -> str:
    base = _app_title()
    if subtitle:
        return f"{base} — {subtitle}"
    return base


class _ForcedDecisionDialog(QtWidgets.QDialog):
    """Dialog that forces an explicit decision.

    - Close button / Alt+F4 are ignored
    - Escape and Enter keys are ignored
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        flags = self.windowFlags()
        flags &= ~QtCore.Qt.WindowCloseButtonHint
        self.setWindowFlags(flags)
        self.setModal(True)

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        e.ignore()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            e.ignore()
            return
        super().keyPressEvent(e)


# ----- Critical startup dialogs (hardcoded EN) -----


def critical_defaults_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    """Called when defaults.json is missing (before i18n is available)."""
    title = "PySkryptor Error"
    text = (
        "Required configuration file 'defaults.json' is missing.\n\n"
        "The application cannot start without it.\n"
        "Please restore 'defaults.json' and try again."
    )
    QtWidgets.QMessageBox.critical(parent, title, text)


def critical_locales_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    """Called when localization files are missing (before i18n is available)."""
    title = "PySkryptor Error"
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


# ----- Informational dialogs (localized) -----


def info_settings_restored(parent: QtWidgets.QWidget | None = None) -> None:
    title = _window_title()
    text = T.tr("dialog.settings_restored")
    QtWidgets.QMessageBox.information(parent, title, text)


def info_playlist_not_supported(parent: QtWidgets.QWidget | None = None) -> None:
    title = _window_title()
    text = T.tr("down.dialog.playlist_not_supported.text")
    QtWidgets.QMessageBox.information(parent, title, text)


def runtime_error(parent: QtWidgets.QWidget | None, message: str, *, subtitle: str | None = None) -> None:
    """Localized runtime error message."""
    title = _window_title(subtitle)
    QtWidgets.QMessageBox.critical(parent, title, message)


# ----- Runtime confirmations (forced decision) -----


def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    """Ask whether to cancel a long-running operation."""
    dlg = _ForcedDecisionDialog(parent)
    dlg.setWindowTitle(_window_title())

    root = QtWidgets.QVBoxLayout(dlg)
    root.setSpacing(10)

    lbl = QtWidgets.QLabel(T.tr("dialog.cancel_confirm", detail=""))
    lbl.setWordWrap(True)
    root.addWidget(lbl)

    buttons = QtWidgets.QHBoxLayout()
    buttons.addStretch(1)

    btn_keep = QtWidgets.QPushButton(T.tr("action.keep_working"))
    btn_cancel = QtWidgets.QPushButton(T.tr("action.cancel_now"))

    btn_keep.setDefault(False)
    btn_cancel.setDefault(False)

    buttons.addWidget(btn_keep)
    buttons.addWidget(btn_cancel)
    root.addLayout(buttons)

    result = {"cancel": False}

    def on_keep() -> None:
        result["cancel"] = False
        dlg.accept()

    def on_cancel() -> None:
        result["cancel"] = True
        dlg.accept()

    btn_keep.clicked.connect(on_keep)
    btn_cancel.clicked.connect(on_cancel)

    dlg.exec_()
    return bool(result["cancel"])


def ask_restart_required(parent: QtWidgets.QWidget) -> bool:
    """Inform user that restart is required after saving settings.

    Returns True if the user chose "Restart now".
    """

    dlg = _ForcedDecisionDialog(parent)
    dlg.setWindowTitle(_window_title(T.tr("dialog.restart_required.title")))

    root = QtWidgets.QVBoxLayout(dlg)
    root.setSpacing(10)

    lbl = QtWidgets.QLabel(T.tr("dialog.restart_required.text"))
    lbl.setWordWrap(True)
    root.addWidget(lbl)

    buttons = QtWidgets.QHBoxLayout()
    buttons.addStretch(1)

    btn_later = QtWidgets.QPushButton(T.tr("action.later"))
    btn_restart = QtWidgets.QPushButton(T.tr("action.restart_now"))

    btn_later.setDefault(False)
    btn_restart.setDefault(False)

    buttons.addWidget(btn_later)
    buttons.addWidget(btn_restart)
    root.addLayout(buttons)

    result = {"restart": False}

    def on_later() -> None:
        result["restart"] = False
        dlg.accept()

    def on_restart() -> None:
        result["restart"] = True
        dlg.accept()

    btn_later.clicked.connect(on_later)
    btn_restart.clicked.connect(on_restart)

    dlg.exec_()
    return bool(result["restart"])


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> Tuple[str, str, bool]:
    """Transcript conflict dialog.

    Returns (action, new_stem, apply_all)
    action ∈ {"skip", "overwrite", "new"}
    """

    dlg = _ForcedDecisionDialog(parent)
    dlg.setWindowTitle(_window_title(T.tr("dialog.conflict.title")))

    root = QtWidgets.QVBoxLayout(dlg)
    root.setSpacing(10)

    lbl = QtWidgets.QLabel(T.tr("dialog.conflict.text", name=stem))
    lbl.setWordWrap(True)
    root.addWidget(lbl)

    rb_skip = QtWidgets.QRadioButton(T.tr("dialog.conflict.skip"))
    rb_over = QtWidgets.QRadioButton(T.tr("dialog.conflict.overwrite"))
    rb_new = QtWidgets.QRadioButton(T.tr("dialog.conflict.new_name"))
    rb_skip.setChecked(True)

    root.addWidget(rb_skip)
    root.addWidget(rb_over)
    root.addWidget(rb_new)

    name_edit = QtWidgets.QLineEdit()
    name_edit.setEnabled(False)
    root.addWidget(name_edit)

    cb_all = QtWidgets.QCheckBox(T.tr("dialog.conflict.apply_all"))
    root.addWidget(cb_all)

    buttons = QtWidgets.QHBoxLayout()
    buttons.addStretch(1)

    btn_skip = QtWidgets.QPushButton(T.tr("dialog.conflict.skip"))
    btn_over = QtWidgets.QPushButton(T.tr("dialog.conflict.overwrite"))
    btn_new = QtWidgets.QPushButton(T.tr("dialog.conflict.new_name"))
    btn_new.setEnabled(False)

    for b in (btn_skip, btn_over, btn_new):
        b.setDefault(False)

    buttons.addWidget(btn_skip)
    buttons.addWidget(btn_over)
    buttons.addWidget(btn_new)
    root.addLayout(buttons)

    choice: dict = {"action": "skip", "new": "", "all": False}

    def update_state() -> None:
        is_new = rb_new.isChecked()
        name_edit.setEnabled(is_new)
        cb_all.setEnabled(not is_new)
        txt = name_edit.text().strip()
        btn_new.setEnabled(bool(is_new and txt))

    rb_new.toggled.connect(update_state)
    name_edit.textChanged.connect(lambda _t: update_state())
    update_state()

    def do_skip() -> None:
        choice["action"] = "skip"
        choice["new"] = ""
        choice["all"] = bool(cb_all.isChecked())
        dlg.accept()

    def do_over() -> None:
        choice["action"] = "overwrite"
        choice["new"] = ""
        choice["all"] = bool(cb_all.isChecked())
        dlg.accept()

    def do_new() -> None:
        choice["action"] = "new"
        choice["new"] = name_edit.text().strip()
        choice["all"] = False
        dlg.accept()

    btn_skip.clicked.connect(do_skip)
    btn_over.clicked.connect(do_over)
    btn_new.clicked.connect(do_new)

    dlg.exec_()
    return str(choice["action"]), str(choice["new"]), bool(choice["all"])


def ask_download_duplicate(parent: QtWidgets.QWidget, *, title: str, suggested_name: str) -> Tuple[str, str]:
    """Download duplicate dialog.

    Returns (action, new_name)
    action ∈ {"skip", "overwrite", "rename"}
    """

    dlg = _ForcedDecisionDialog(parent)
    dlg.setWindowTitle(_window_title())

    root = QtWidgets.QVBoxLayout(dlg)
    root.setSpacing(10)

    lbl = QtWidgets.QLabel(T.tr("down.dialog.exists.text", title=title))
    lbl.setWordWrap(True)
    root.addWidget(lbl)

    rb_skip = QtWidgets.QRadioButton(T.tr("down.dialog.exists.skip"))
    rb_over = QtWidgets.QRadioButton(T.tr("down.dialog.exists.overwrite"))
    rb_ren = QtWidgets.QRadioButton(T.tr("down.dialog.exists.rename"))
    rb_skip.setChecked(True)

    root.addWidget(rb_skip)
    root.addWidget(rb_over)
    root.addWidget(rb_ren)

    name_edit = QtWidgets.QLineEdit(suggested_name)
    name_edit.setEnabled(False)
    root.addWidget(name_edit)

    buttons = QtWidgets.QHBoxLayout()
    buttons.addStretch(1)

    btn_skip = QtWidgets.QPushButton(T.tr("down.dialog.exists.skip"))
    btn_over = QtWidgets.QPushButton(T.tr("down.dialog.exists.overwrite"))
    btn_ren = QtWidgets.QPushButton(T.tr("down.dialog.exists.rename"))
    btn_ren.setEnabled(False)

    for b in (btn_skip, btn_over, btn_ren):
        b.setDefault(False)

    buttons.addWidget(btn_skip)
    buttons.addWidget(btn_over)
    buttons.addWidget(btn_ren)
    root.addLayout(buttons)

    choice: dict = {"action": "skip", "name": ""}

    def update_state() -> None:
        is_ren = rb_ren.isChecked()
        name_edit.setEnabled(is_ren)
        btn_ren.setEnabled(bool(is_ren and name_edit.text().strip()))

    rb_ren.toggled.connect(update_state)
    name_edit.textChanged.connect(lambda _t: update_state())
    update_state()

    def do_skip() -> None:
        choice["action"] = "skip"
        choice["name"] = ""
        dlg.accept()

    def do_over() -> None:
        choice["action"] = "overwrite"
        choice["name"] = ""
        dlg.accept()

    def do_ren() -> None:
        choice["action"] = "rename"
        choice["name"] = name_edit.text().strip()
        dlg.accept()

    btn_skip.clicked.connect(do_skip)
    btn_over.clicked.connect(do_over)
    btn_ren.clicked.connect(do_ren)

    dlg.exec_()
    return str(choice["action"]), str(choice["name"])