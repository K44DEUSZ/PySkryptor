# app/view/dialogs.py
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, cast

from PyQt5 import QtCore, QtGui, QtSvg, QtWidgets

from app.model.core.config.config import AppConfig
from app.model.core.config.meta import AppMeta
from app.model.core.runtime.localization import tr
from app.model.core.utils.string_utils import sanitize_filename
from app.model.download.domain import SourceAccessInterventionResolution
from app.model.download.policy import DownloadPolicy
from app.view.components.popup_combo import PopupComboBox
from app.view.support.theme_runtime import active_theme_key, apply_windows_dark_titlebar
from app.view.support.widget_setup import (
    connect_qt_signal,
    set_passive_cursor,
    setup_button,
    setup_combo,
    setup_input,
    setup_label,
    setup_option_checkbox,
    setup_spinbox,
    setup_text_editor,
    setup_toggle_button,
)
from app.view.ui_config import UIConfig, ui


@dataclass(frozen=True)
class NoticeDecision:
    """Normalized result returned by one-off notice dialogs."""

    accepted: bool
    dont_show_again: bool


class _DialogWidthClass(Enum):
    """Named width families shared by all standard dialogs."""

    COMPACT = "compact"
    STANDARD = "standard"
    WIDE = "wide"


class _DialogClosePolicy(Enum):
    """Dialog close behavior shared across all dialog families."""

    ALLOW_CLOSE = "allow_close"
    REQUIRE_EXPLICIT_ACTION = "require_explicit_action"


@dataclass(frozen=True)
class _DialogFrameSpec:
    """Shared frame description for standard dialog families."""

    title: str
    header: str = ""
    lead: str = ""
    body_lines: tuple[str, ...] = ()
    detail_lines: tuple[str, ...] = ()
    width_class: _DialogWidthClass = _DialogWidthClass.STANDARD
    close_policy: _DialogClosePolicy = _DialogClosePolicy.ALLOW_CLOSE


@dataclass(frozen=True)
class _DialogActionSpec:
    """Single bottom action shown by a standard dialog."""

    key: str
    text: str
    role: QtWidgets.QDialogButtonBox.ButtonRole
    is_default: bool = False


@dataclass(frozen=True)
class _ChoiceOptionSpec:
    """Single selectable option rendered by a choice decision dialog."""

    key: str
    label: str
    description: str = ""
    editor_kind: str = ""
    editor_text: str = ""
    editor_placeholder: str = ""
    editor_suffix: str = ""
    editor_min: int = 0
    editor_max: int = 0
    editor_value: int = 0
    disable_apply_all: bool = False
    default: bool = False


@dataclass(frozen=True)
class _ChoiceDialogResult:
    """Normalized internal result returned by choice-style dialog helpers."""

    action: str = "cancel"
    text_value: str = ""
    int_value: int = 0
    apply_all: bool = False


class _NoCloseFilter(QtCore.QObject):
    """Event filter that blocks closing the dialog via window controls or ESC."""

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        try:
            event_type = event.type()
            if event_type == QtCore.QEvent.Type.Close:
                event.ignore()
                return True
            if event_type == QtCore.QEvent.Type.KeyPress and isinstance(event, QtGui.QKeyEvent):
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


def _tune_dialog_layout(layout: QtWidgets.QLayout, cfg: UIConfig) -> None:
    layout.setContentsMargins(cfg.margin, cfg.margin, cfg.margin, cfg.margin)
    layout.setSpacing(cfg.spacing)


def _tune_buttons(cfg: UIConfig, *buttons: QtWidgets.QAbstractButton) -> None:
    for button in buttons:
        setup_button(button, min_h=cfg.control_min_h, min_w=cfg.button_min_w)


def _tune_dialog_window(dlg: QtWidgets.QDialog) -> None:
    _sanitize_window_flags(dlg)
    dlg.setModal(True)
    set_passive_cursor(dlg)
    dlg.setWindowTitle(AppMeta.NAME)
    try:
        dlg.setWindowIcon(QtWidgets.QApplication.windowIcon())
    except (AttributeError, RuntimeError, TypeError):
        return
    apply_windows_dark_titlebar(dlg)


def _dialog_width_limits(cfg: UIConfig, width_class: _DialogWidthClass) -> tuple[int, int]:
    """Return the lower and upper width bound for a dialog family."""
    dialog_min = int(cfg.dialog_min_w)
    dialog_max = max(dialog_min, int(cfg.dialog_max_w))
    control_min = int(cfg.control_min_w)

    if width_class is _DialogWidthClass.COMPACT:
        lower = max(control_min * 3, dialog_min - control_min)
        return lower, dialog_min

    if width_class is _DialogWidthClass.WIDE:
        return dialog_min, dialog_max

    span = max(0, dialog_max - dialog_min)
    return dialog_min, max(dialog_min, dialog_min + (span // 2))



def _dialog_option_min_h(cfg: UIConfig) -> int:
    return max(int(cfg.option_row_min_h), int(cfg.control_min_h) - int(cfg.space_s) - 1)



def _dialog_option_spacing(cfg: UIConfig) -> int:
    return max(4, int(cfg.space_s))



def _setup_dialog_toggle(btn: QtWidgets.QAbstractButton, cfg: UIConfig) -> QtWidgets.QAbstractButton:
    return setup_toggle_button(btn, min_h=_dialog_option_min_h(cfg))



def _setup_dialog_option_checkbox(btn: QtWidgets.QCheckBox, cfg: UIConfig) -> QtWidgets.QCheckBox:
    return setup_option_checkbox(btn, min_h=_dialog_option_min_h(cfg))



def _finalize_dialog_window(dlg: QtWidgets.QDialog, cfg: UIConfig, *, width_class: _DialogWidthClass) -> None:
    """Finalize a stable dialog size bounded by the active screen geometry."""
    layout = dlg.layout()
    if layout is not None:
        layout.activate()
    dlg.adjustSize()

    lower, upper = _dialog_width_limits(cfg, width_class)
    hint = dlg.sizeHint()
    minimum_hint = dlg.minimumSizeHint()

    try:
        screen = dlg.screen()
    except (AttributeError, RuntimeError, TypeError):
        screen = None
    if screen is None:
        app = cast(QtWidgets.QApplication | None, QtWidgets.QApplication.instance())
        screen = app.primaryScreen() if app is not None else None

    if screen is not None:
        try:
            screen_rect = screen.availableGeometry()
            screen_w = int(screen_rect.width())
            screen_h = int(screen_rect.height())
        except (AttributeError, RuntimeError, TypeError):
            screen_w = int(cfg.window_min_w)
            screen_h = int(cfg.window_min_h)
    else:
        screen_w = int(cfg.window_min_w)
        screen_h = int(cfg.window_min_h)

    max_w = max(lower, min(upper, screen_w - (int(cfg.margin) * 6)))
    target_w = max(int(hint.width()), int(minimum_hint.width()), lower)
    target_w = min(target_w, max_w)

    dlg.setMinimumWidth(target_w)
    dlg.setMaximumWidth(target_w)

    if layout is not None and layout.hasHeightForWidth():
        target_h = int(layout.totalHeightForWidth(target_w))
    else:
        dlg.resize(target_w, max(1, int(minimum_hint.height())))
        if layout is not None:
            layout.activate()
        dlg.adjustSize()
        hint = dlg.sizeHint()
        minimum_hint = dlg.minimumSizeHint()
        target_h = max(int(hint.height()), int(minimum_hint.height()))

    max_h = max(int(minimum_hint.height()), screen_h - (int(cfg.margin) * 8))
    target_h = max(int(minimum_hint.height()), min(int(target_h), max_h))

    dlg.setMinimumHeight(target_h)
    dlg.setMaximumHeight(max_h)
    dlg.resize(target_w, target_h)


def _wrap_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    return lbl



def _section_label(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    setup_label(lbl, role="sectionTitle")
    lbl.setWordWrap(True)
    return lbl


def _build_dialog_frame(
    parent: QtWidgets.QWidget | None,
    frame: _DialogFrameSpec,
) -> tuple[QtWidgets.QDialog, UIConfig, QtWidgets.QVBoxLayout]:
    """Create a standard dialog shell shared by message and decision dialogs."""
    cfg = ui(parent)
    dlg = QtWidgets.QDialog(parent)
    _tune_dialog_window(dlg)
    if frame.close_policy is _DialogClosePolicy.REQUIRE_EXPLICIT_ACTION:
        _lock_close(dlg)
    dlg.setWindowTitle(str(frame.title or AppMeta.NAME))

    layout = QtWidgets.QVBoxLayout(dlg)
    _tune_dialog_layout(layout, cfg)
    layout.setSpacing(0)

    def add_widget(widget: QtWidgets.QWidget, *, space_before: int = 0) -> None:
        if layout.count() > 0 and space_before > 0:
            layout.addSpacing(int(space_before))
        layout.addWidget(widget)

    header = str(frame.header or "").strip()
    lead = str(frame.lead or "").strip()
    body_lines = tuple(str(body or "").strip() for body in tuple(frame.body_lines or ()) if str(body or "").strip())
    detail_lines = tuple(
        str(detail or "").strip() for detail in tuple(frame.detail_lines or ()) if str(detail or "").strip()
    )

    if header:
        add_widget(_section_label(header))
    if lead:
        add_widget(_wrap_label(lead), space_before=cfg.space_s if header else 0)
    for body in body_lines:
        add_widget(_wrap_label(body), space_before=cfg.space_s)
    for detail in detail_lines:
        add_widget(_wrap_label(detail), space_before=cfg.space_s)

    return dlg, cfg, layout


def _build_dialog_button_box(
    parent: QtWidgets.QWidget,
    cfg: UIConfig,
    actions: tuple[_DialogActionSpec, ...],
    on_action: Callable[[str], None],
) -> tuple[QtWidgets.QDialogButtonBox, dict[str, QtWidgets.QAbstractButton]]:
    """Create a standard button box for message and decision dialogs."""
    box = QtWidgets.QDialogButtonBox(parent)
    buttons: dict[str, QtWidgets.QAbstractButton] = {}
    default_button: QtWidgets.QAbstractButton | None = None

    for spec in actions:
        button = box.addButton(spec.text, spec.role)
        buttons[spec.key] = button
        if spec.is_default and default_button is None:
            default_button = button
        connect_qt_signal(button.clicked, lambda checked=False, action_key=spec.key: on_action(action_key))

    if buttons:
        _tune_buttons(cfg, *buttons.values())
    if default_button is not None:
        default_button.setDefault(True)
    return box, buttons


def _normalize_dialog_lines(lines: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(str(line or "").strip() for line in tuple(lines or ()) if str(line or "").strip())


def _split_dialog_lines(lines: tuple[str, ...] | list[str] | None) -> tuple[str, tuple[str, ...]]:
    normalized = _normalize_dialog_lines(lines)
    if not normalized:
        return "", ()
    return normalized[0], normalized[1:]


def _run_message_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    message: str,
    header: str | None = None,
    ok_text: str | None = None,
    no_close: bool = False,
    width_class: _DialogWidthClass = _DialogWidthClass.STANDARD,
) -> None:
    """Show a standard informational message dialog."""
    frame = _DialogFrameSpec(
        title=str(title or AppMeta.NAME),
        header=str(header or "").strip(),
        lead=str(message or "").strip(),
        width_class=width_class,
        close_policy=(
            _DialogClosePolicy.REQUIRE_EXPLICIT_ACTION if no_close else _DialogClosePolicy.ALLOW_CLOSE
        ),
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)

    selected_action = {"name": "ok"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "ok")
        dlg.accept()

    box, _ = _build_dialog_button_box(
        dlg,
        cfg,
        (
            _DialogActionSpec(
                key="ok",
                text=str(ok_text or tr("controls.ok")),
                role=QtWidgets.QDialogButtonBox.AcceptRole,
                is_default=True,
            ),
        ),
        finish,
    )
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()


def _run_simple_decision_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    header: str = "",
    body_lines: tuple[str, ...],
    accept_text: str,
    reject_text: str,
    default_action: str = "reject",
    width_class: _DialogWidthClass = _DialogWidthClass.STANDARD,
) -> bool:
    """Show a simple explicit-decision dialog and return True for accept."""
    lead, remaining_body = _split_dialog_lines(body_lines)
    frame = _DialogFrameSpec(
        title=str(title or AppMeta.NAME),
        header=str(header or "").strip(),
        lead=lead,
        body_lines=remaining_body,
        width_class=width_class,
        close_policy=_DialogClosePolicy.REQUIRE_EXPLICIT_ACTION,
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)
    selected_action = {"name": "reject"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "reject")
        if selected_action["name"] == "accept":
            dlg.accept()
        else:
            dlg.reject()

    actions = (
        _DialogActionSpec(
            key="accept",
            text=accept_text,
            role=QtWidgets.QDialogButtonBox.AcceptRole,
            is_default=(default_action == "accept"),
        ),
        _DialogActionSpec(
            key="reject",
            text=reject_text,
            role=QtWidgets.QDialogButtonBox.RejectRole,
            is_default=(default_action != "accept"),
        ),
    )
    box, _ = _build_dialog_button_box(dlg, cfg, actions, finish)
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()
    return str(selected_action.get("name") or "reject") == "accept"


def _run_notice_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    header: str,
    paragraphs: tuple[str, ...],
    checkbox_text: str,
    accept_text: str,
    reject_text: str,
    detail_lines: tuple[str, ...] = (),
) -> NoticeDecision:
    """Show the shared explicit-decision notice dialog."""
    lead, remaining_body = _split_dialog_lines(paragraphs)
    frame = _DialogFrameSpec(
        title=str(title or AppMeta.NAME),
        header=str(header or "").strip(),
        lead=lead,
        body_lines=remaining_body,
        detail_lines=_normalize_dialog_lines(detail_lines),
        width_class=_DialogWidthClass.STANDARD,
        close_policy=_DialogClosePolicy.REQUIRE_EXPLICIT_ACTION,
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)

    chk_dont_show = QtWidgets.QCheckBox(checkbox_text, dlg)
    _setup_dialog_option_checkbox(chk_dont_show, cfg)
    if layout.count() > 0:
        layout.addSpacing(cfg.space_m)
    layout.addWidget(chk_dont_show)

    selected_action = {"name": "reject"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "reject")
        if selected_action["name"] == "accept":
            dlg.accept()
        else:
            dlg.reject()

    box, _ = _build_dialog_button_box(
        dlg,
        cfg,
        (
            _DialogActionSpec(
                key="accept",
                text=accept_text,
                role=QtWidgets.QDialogButtonBox.AcceptRole,
                is_default=True,
            ),
            _DialogActionSpec(
                key="reject",
                text=reject_text,
                role=QtWidgets.QDialogButtonBox.RejectRole,
            ),
        ),
        finish,
    )
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()

    accepted = str(selected_action.get("name") or "reject") == "accept"
    return NoticeDecision(accepted=accepted, dont_show_again=accepted and chk_dont_show.isChecked())


def _run_completion_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    message: str,
    action_text: str,
    dismiss_text: str,
    header: str = "",
    width_class: _DialogWidthClass = _DialogWidthClass.COMPACT,
) -> bool:
    """Show a completion dialog with an optional follow-up action."""
    frame = _DialogFrameSpec(
        title=str(title or AppMeta.NAME),
        header=str(header or "").strip(),
        lead=str(message or "").strip(),
        width_class=width_class,
        close_policy=_DialogClosePolicy.ALLOW_CLOSE,
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)
    selected_action = {"name": "dismiss"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "dismiss")
        dlg.accept()

    box, _ = _build_dialog_button_box(
        dlg,
        cfg,
        (
            _DialogActionSpec(
                key="action",
                text=action_text,
                role=QtWidgets.QDialogButtonBox.ActionRole,
            ),
            _DialogActionSpec(
                key="dismiss",
                text=dismiss_text,
                role=QtWidgets.QDialogButtonBox.AcceptRole,
                is_default=True,
            ),
        ),
        finish,
    )
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()
    return str(selected_action.get("name") or "dismiss") == "action"


def _build_preview_block(
    parent: QtWidgets.QWidget,
    layout: QtWidgets.QVBoxLayout,
    cfg,
    *,
    title: str,
    lines: tuple[str, ...],
) -> None:
    """Add a shared read-only preview block used by wide decision dialogs."""
    normalized_lines = tuple(str(item or "").strip() for item in lines if str(item or "").strip())
    if not normalized_lines:
        return

    if layout.count() > 0:
        layout.addSpacing(cfg.space_m)
    layout.addWidget(_section_label(title))
    layout.addSpacing(cfg.space_s)

    preview_box = QtWidgets.QPlainTextEdit(parent)
    preview_box.setReadOnly(True)
    preview_box.setPlainText("\n".join(f"• {item}" for item in normalized_lines))
    preview_box.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    preview_box.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    preview_box.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    setup_text_editor(preview_box)

    visible_rows = max(1, min(5, len(normalized_lines)))
    line_height = preview_box.fontMetrics().lineSpacing()
    frame_height = preview_box.frameWidth() * 2
    document_margin = int(preview_box.document().documentMargin() * 2)
    preview_height = (line_height * visible_rows) + frame_height + document_margin + int(cfg.space_s)
    preview_box.setFixedHeight(max(cfg.control_min_h * 2, preview_height))
    layout.addWidget(preview_box)


def _build_choice_option_editor(
    parent: QtWidgets.QWidget,
    cfg,
    spec: _ChoiceOptionSpec,
) -> QtWidgets.QWidget | None:
    """Create the optional editor displayed below a choice option."""
    kind = str(spec.editor_kind or "").strip().lower()
    if not kind:
        return None

    host = QtWidgets.QWidget(parent)
    row = QtWidgets.QHBoxLayout(host)
    row.setContentsMargins(int(cfg.margin), 0, 0, 0)
    row.setSpacing(_dialog_option_spacing(cfg))

    if kind == "line_edit":
        editor = QtWidgets.QLineEdit(host)
        editor.setText(str(spec.editor_text or ""))
        if spec.editor_placeholder:
            editor.setPlaceholderText(spec.editor_placeholder)
        setup_input(editor, min_h=cfg.control_min_h)
        row.addWidget(editor, 1)
        setattr(host, "_editor_widget", editor)
        return host

    if kind == "spinbox":
        editor = QtWidgets.QSpinBox(host)
        editor.setMinimum(max(1, int(spec.editor_min or 1)))
        editor.setMaximum(max(int(spec.editor_max or editor.minimum()), editor.minimum()))
        editor.setValue(max(editor.minimum(), min(int(spec.editor_value or editor.minimum()), editor.maximum())))
        editor.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
        setup_spinbox(editor, min_h=cfg.control_min_h)
        row.addWidget(editor)
        if spec.editor_suffix:
            row.addWidget(QtWidgets.QLabel(spec.editor_suffix, host))
        row.addStretch(1)
        setattr(host, "_editor_widget", editor)
        return host

    return None


def _run_choice_dialog(
    parent: QtWidgets.QWidget | None,
    *,
    title: str,
    header: str,
    body_lines: tuple[str, ...],
    options: tuple[_ChoiceOptionSpec, ...],
    accept_text: str,
    reject_text: str | None = None,
    detail_lines: tuple[str, ...] = (),
    preview_title: str = "",
    preview_lines: tuple[str, ...] = (),
    apply_all_text: str = "",
    width_class: _DialogWidthClass = _DialogWidthClass.STANDARD,
) -> _ChoiceDialogResult:
    """Run the shared decision dialog used by all choice-style prompts."""
    lead, remaining_body = _split_dialog_lines(body_lines)
    frame = _DialogFrameSpec(
        title=str(title or AppMeta.NAME),
        header=str(header or "").strip(),
        lead=lead,
        body_lines=remaining_body,
        detail_lines=_normalize_dialog_lines(detail_lines),
        width_class=width_class,
        close_policy=_DialogClosePolicy.REQUIRE_EXPLICIT_ACTION,
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)

    if preview_lines:
        _build_preview_block(
            dlg,
            layout,
            cfg,
            title=str(preview_title or tr("dialog.bulk_add.preview_header")),
            lines=tuple(preview_lines or ()),
        )

    options_host = QtWidgets.QWidget(dlg)
    options_layout = QtWidgets.QVBoxLayout(options_host)
    options_layout.setContentsMargins(0, 0, 0, 0)
    options_layout.setSpacing(_dialog_option_spacing(cfg))
    if layout.count() > 0:
        layout.addSpacing(cfg.space_m)
    layout.addWidget(options_host)

    radios_by_key: dict[str, QtWidgets.QRadioButton] = {}
    editors_by_key: dict[str, QtWidgets.QWidget] = {}
    apply_all_checkbox: QtWidgets.QCheckBox | None = None

    default_key = next((spec.key for spec in options if spec.default), options[0].key if options else "")

    for spec in options:
        radio = QtWidgets.QRadioButton(spec.label, options_host)
        _setup_dialog_toggle(radio, cfg)
        radios_by_key[spec.key] = radio
        options_layout.addWidget(radio)

        if spec.description:
            description_label = _wrap_label(spec.description)
            description_label.setContentsMargins(int(cfg.margin), 0, 0, 0)
            options_layout.addWidget(description_label)

        editor_host = _build_choice_option_editor(options_host, cfg, spec)
        if editor_host is not None:
            editor_host.setEnabled(False)
            editors_by_key[spec.key] = editor_host
            options_layout.addWidget(editor_host)

        if spec.key == default_key:
            radio.setChecked(True)

    if apply_all_text:
        apply_all_checkbox = QtWidgets.QCheckBox(apply_all_text, dlg)
        _setup_dialog_option_checkbox(apply_all_checkbox, cfg)
        if layout.count() > 0:
            layout.addSpacing(cfg.space_m)
        layout.addWidget(apply_all_checkbox)

    def sync_choice_state() -> None:
        active_key = next(
            (option_key for option_key, option_radio in radios_by_key.items() if option_radio.isChecked()),
            "",
        )
        for option_key, option_editor_host in editors_by_key.items():
            option_editor_host.setEnabled(option_key == active_key)
        if apply_all_checkbox is None:
            return
        selected_spec = next((option_spec for option_spec in options if option_spec.key == active_key), None)
        disable_apply_all = bool(selected_spec.disable_apply_all) if selected_spec is not None else False
        if disable_apply_all:
            apply_all_checkbox.setChecked(False)
            apply_all_checkbox.setEnabled(False)
        else:
            apply_all_checkbox.setEnabled(True)

    for radio in radios_by_key.values():
        connect_qt_signal(radio.toggled, sync_choice_state)
    sync_choice_state()

    selected_action = {"name": "cancel"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "cancel")
        if selected_action["name"] == "accept":
            dlg.accept()
        else:
            dlg.reject()

    actions: list[_DialogActionSpec] = [
        _DialogActionSpec(
            key="accept",
            text=accept_text,
            role=QtWidgets.QDialogButtonBox.AcceptRole,
            is_default=True,
        )
    ]
    if reject_text is not None:
        actions.append(
            _DialogActionSpec(
                key="reject",
                text=reject_text,
                role=QtWidgets.QDialogButtonBox.RejectRole,
            )
        )

    button_box, _ = _build_dialog_button_box(dlg, cfg, tuple(actions), finish)
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(button_box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()

    if str(selected_action.get("name") or "cancel") != "accept":
        return _ChoiceDialogResult(action="cancel")

    selected_key = next((key for key, radio in radios_by_key.items() if radio.isChecked()), "")
    text_value = ""
    int_value = 0
    editor_host = editors_by_key.get(selected_key)
    if editor_host is not None:
        editor = getattr(editor_host, "_editor_widget", None)
        if isinstance(editor, QtWidgets.QLineEdit):
            text_value = str(editor.text() or "").strip()
        elif isinstance(editor, QtWidgets.QSpinBox):
            int_value = int(editor.value())

    return _ChoiceDialogResult(
        action=selected_key,
        text_value=text_value,
        int_value=int_value,
        apply_all=bool(apply_all_checkbox.isChecked()) if apply_all_checkbox is not None else False,
    )


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


def _welcome_icon_svg_path() -> Path | None:
    """Return the themed SVG path used by the welcome dialog icon."""
    resolved = active_theme_key()
    candidates = [
        AppConfig.PATHS.ICONS_DIR / f"app_icon_{resolved}.svg",
        AppConfig.PATHS.ICONS_DIR / "app_icon_light.svg",
        AppConfig.PATHS.ICONS_DIR / "app_icon_dark.svg",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _render_welcome_icon(size: int) -> QtGui.QPixmap:
    """Render the welcome dialog icon directly from SVG for a crisp result."""
    side = max(1, int(size))
    path = _welcome_icon_svg_path()
    if path is None:
        return QtGui.QPixmap()

    renderer = QtSvg.QSvgRenderer(str(path))
    if not renderer.isValid():
        return QtGui.QPixmap()

    view_box = renderer.viewBoxF()
    src_w = float(view_box.width()) if view_box.width() > 0 else float(side)
    src_h = float(view_box.height()) if view_box.height() > 0 else float(side)
    ratio = min(float(side) / src_w, float(side) / src_h)
    draw_w = max(1.0, src_w * ratio)
    draw_h = max(1.0, src_h * ratio)

    app = cast(QtWidgets.QApplication | None, QtWidgets.QApplication.instance())
    try:
        dpr = max(1.0, float(app.devicePixelRatio())) if app is not None else 1.0
    except (AttributeError, RuntimeError, TypeError):
        dpr = 1.0
    pm = QtGui.QPixmap(max(1, int(round(side * dpr))), max(1, int(round(side * dpr))))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    x = (float(side) - draw_w) / 2.0
    y = (float(side) - draw_h) / 2.0
    renderer.render(painter, QtCore.QRectF(x, y, draw_w, draw_h))
    painter.end()
    return pm


class _WelcomeDialog(QtWidgets.QDialog):
    """Startup dialog shown once after the main window is ready."""

    def __init__(self, parent: QtWidgets.QWidget | None) -> None:
        super().__init__(parent)
        self._ui = ui(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        cfg = self._ui
        _tune_dialog_window(self)
        _lock_close(self)
        self.setWindowTitle(tr("dialog.welcome.title"))

        root = QtWidgets.QVBoxLayout(self)
        _tune_dialog_layout(root, cfg)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(cfg.space_s)
        content.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        root.addLayout(content)

        icon_label = QtWidgets.QLabel(self)
        icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop)
        icon_size = max(88, int(cfg.control_min_w * 3 / 2), int(cfg.button_big_h * 3))
        pixmap = _render_welcome_icon(icon_size)
        if pixmap.isNull():
            icon_label.setText(AppMeta.NAME)
            icon_label.setMinimumWidth(icon_size)
        else:
            icon_label.setPixmap(pixmap)
            icon_label.setFixedSize(icon_size, icon_size)

        icon_col = QtWidgets.QVBoxLayout()
        icon_col.setContentsMargins(cfg.margin, cfg.margin, cfg.space_s, 0)
        icon_col.setSpacing(0)
        icon_col.addWidget(icon_label, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        content.addLayout(icon_col)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        content.addLayout(text_col, 1)

        title_label = QtWidgets.QLabel(AppMeta.NAME, self)
        setup_label(title_label, role="sectionTitle")
        title_label.setWordWrap(True)
        title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        text_col.addWidget(title_label)

        meta_col = QtWidgets.QVBoxLayout()
        meta_col.setContentsMargins(0, 0, 0, 0)
        meta_col.setSpacing(0)
        meta_col.addWidget(_wrap_label(tr("dialog.welcome.meta_version", version=AppMeta.VERSION)))
        meta_col.addWidget(_wrap_label(tr("dialog.welcome.meta_author", author=AppMeta.AUTHOR)))
        meta_col.addWidget(_wrap_label(tr("dialog.welcome.meta_years", years=AppMeta.DEVELOPMENT_YEARS)))
        text_col.addLayout(meta_col)

        text_col.addSpacing(cfg.space_s)
        text_col.addWidget(_wrap_label(tr("dialog.welcome.text", name=AppMeta.NAME)))
        text_col.addWidget(_wrap_label(tr("dialog.welcome.disclaimer")))

        selected_action = {"name": "accept"}

        def finish(action_name: str) -> None:
            selected_action["name"] = str(action_name or "reject")
            if selected_action["name"] == "accept":
                self.accept()
            else:
                self.reject()

        button_box, _ = _build_dialog_button_box(
            self,
            cfg,
            (
                _DialogActionSpec(
                    key="accept",
                    text=tr("dialog.welcome.accept"),
                    role=QtWidgets.QDialogButtonBox.AcceptRole,
                    is_default=True,
                ),
                _DialogActionSpec(
                    key="reject",
                    text=tr("dialog.welcome.reject"),
                    role=QtWidgets.QDialogButtonBox.RejectRole,
                ),
            ),
            finish,
        )
        root.addWidget(button_box)
        _finalize_dialog_window(self, cfg, width_class=_DialogWidthClass.WIDE)


def ask_welcome_dialog(parent: QtWidgets.QWidget | None) -> bool:
    """Show the one-off startup welcome dialog."""
    dlg = _WelcomeDialog(parent)
    return dlg.exec_() == QtWidgets.QDialog.Accepted


def ask_source_rights_notice(parent: QtWidgets.QWidget | None) -> NoticeDecision:
    """Show the one-off notice displayed before adding network sources."""
    return _run_notice_dialog(
        parent,
        title=tr("dialog.source_rights_notice.title"),
        header=tr("dialog.source_rights_notice.header"),
        paragraphs=(
            tr("dialog.source_rights_notice.text"),
            tr("dialog.source_rights_notice.disclaimer"),
        ),
        checkbox_text=tr("dialog.source_rights_notice.dont_show_again"),
        accept_text=tr("controls.add"),
        reject_text=tr("controls.cancel"),
    )


def critical_defaults_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = tr("dialog.critical.application_error.title")
    text = tr("dialog.critical.defaults_missing.text")
    _run_message_dialog(parent, title=title, message=text, header=title, no_close=True)
    _terminate_application()


def critical_locales_missing_and_exit(parent: QtWidgets.QWidget | None = None) -> None:
    title = tr("dialog.critical.localization_error.title")
    text = tr("dialog.critical.locales_missing.text")
    _run_message_dialog(parent, title=title, message=text, header=title, no_close=True)
    _terminate_application()


def critical_startup_error_and_exit(parent: QtWidgets.QWidget | None, details: str = "") -> None:
    title = tr("dialog.critical.pyskryptor_error.title")
    msg = tr("dialog.critical.config_load_failed.text", detail=str(details or "").strip())
    _run_message_dialog(parent, title=title, message=msg, header=title, no_close=True)
    _terminate_application()


def critical_config_load_failed_choice(parent: QtWidgets.QWidget | None, details: str = "") -> str:
    """Returns: 'exit' | 'restore_defaults'."""
    title = tr("dialog.critical.pyskryptor_error.title")
    msg = tr("dialog.critical.config_load_failed.text", detail=str(details or "").strip())
    restore = _run_simple_decision_dialog(
        parent,
        title=title,
        header=title,
        body_lines=(msg,),
        accept_text=tr("settings.buttons.restore_defaults"),
        reject_text=tr("controls.exit"),
        default_action="reject",
    )
    return "restore_defaults" if restore else "exit"


def show_no_microphone_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    _run_message_dialog(
        parent,
        title=tr("dialog.live.no_devices.title"),
        header=tr("dialog.live.no_devices.header"),
        message=tr("dialog.live.no_devices.text"),
        ok_text=tr("controls.ok"),
    )


def _availability_dialog(parent: QtWidgets.QWidget | None, *, text_key: str) -> None:
    _run_message_dialog(
        parent,
        title=tr("dialog.availability.network_offline.title"),
        header=tr("dialog.availability.network_offline.header"),
        message=tr(text_key),
        ok_text=tr("controls.ok"),
    )


def show_downloader_offline_dialog(parent: QtWidgets.QWidget | None = None) -> None:
    _availability_dialog(parent, text_key="dialog.availability.downloader_offline.text")


def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    return _run_simple_decision_dialog(
        parent,
        title=tr("dialog.confirm.title"),
        body_lines=(tr("dialog.cancel_confirm"),),
        accept_text=tr("action.cancel_now"),
        reject_text=tr("action.keep_working"),
        default_action="reject",
        width_class=_DialogWidthClass.COMPACT,
    )


def ask_save_settings(parent: QtWidgets.QWidget) -> bool:
    return _run_simple_decision_dialog(
        parent,
        title=tr("dialog.settings.title"),
        body_lines=(tr("dialog.settings_save_confirm"),),
        accept_text=tr("settings.buttons.save"),
        reject_text=tr("controls.cancel"),
        default_action="reject",
        width_class=_DialogWidthClass.COMPACT,
    )


def ask_restore_defaults(parent: QtWidgets.QWidget) -> bool:
    return _run_simple_decision_dialog(
        parent,
        title=tr("dialog.settings.title"),
        body_lines=(tr("dialog.settings_restore_confirm"),),
        accept_text=tr("settings.buttons.restore_defaults"),
        reject_text=tr("controls.cancel"),
        default_action="reject",
        width_class=_DialogWidthClass.COMPACT,
    )


def ask_restart_required(parent: QtWidgets.QWidget) -> bool:
    """Restart decision dialog."""
    return _run_simple_decision_dialog(
        parent,
        title=tr("dialog.restart_required.title"),
        header=tr("dialog.restart_required.header"),
        body_lines=(tr("dialog.restart_required.text"),),
        accept_text=tr("dialog.restart_required.restart"),
        reject_text=tr("dialog.restart_required.later"),
        default_action="accept",
    )


def ask_open_transcripts_folder(parent: QtWidgets.QWidget, session_dir: str) -> bool:
    """Return True if the user wants to open the session folder."""
    return _run_completion_dialog(
        parent,
        title=tr("dialog.info.title"),
        message=str(session_dir or ""),
        action_text=tr("files.open_output"),
        dismiss_text=tr("controls.ok"),
    )


def ask_open_downloads_folder(parent: QtWidgets.QWidget, downloaded_path: str) -> bool:
    """Return True if the user wants to open the downloads folder."""
    try:
        file_name = Path(downloaded_path).name
    except (TypeError, ValueError):
        file_name = str(downloaded_path or "")

    message = tr("dialog.info.downloaded_prefix")
    if file_name:
        message = f"{message} {file_name}"

    return _run_completion_dialog(
        parent,
        title=tr("dialog.info.title"),
        message=message,
        action_text=tr("download.open_folder"),
        dismiss_text=tr("controls.ok"),
    )


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> tuple[str, str, bool]:
    """Transcript conflict dialog."""
    result = _run_choice_dialog(
        parent,
        title=tr("dialog.conflict.title"),
        header=tr("dialog.conflict.header"),
        body_lines=(tr("dialog.conflict.text", name=stem),),
        options=(
            _ChoiceOptionSpec(key="skip", label=tr("dialog.conflict.skip"), default=True),
            _ChoiceOptionSpec(key="overwrite", label=tr("dialog.conflict.overwrite")),
            _ChoiceOptionSpec(
                key="new",
                label=tr("dialog.conflict.new_name"),
                editor_kind="line_edit",
                disable_apply_all=True,
            ),
        ),
        accept_text=tr("controls.ok"),
        apply_all_text=tr("dialog.conflict.apply_all"),
        width_class=_DialogWidthClass.STANDARD,
    )
    if result.action == "overwrite":
        return "overwrite", "", result.apply_all
    if result.action == "new":
        return "new", sanitize_filename(result.text_value), False
    return "skip", "", result.apply_all


def ask_download_duplicate(
    parent: QtWidgets.QWidget,
    *,
    title: str,
    suggested_name: str,
) -> tuple[str, str, bool]:
    """Download duplicate dialog."""
    result = _run_choice_dialog(
        parent,
        title=tr("dialog.download.exists.title"),
        header=tr("dialog.download.exists.header"),
        body_lines=(tr("dialog.download.exists.text", title=title),),
        options=(
            _ChoiceOptionSpec(key="skip", label=tr("dialog.download.exists.skip"), default=True),
            _ChoiceOptionSpec(key="overwrite", label=tr("dialog.download.exists.overwrite")),
            _ChoiceOptionSpec(
                key="rename",
                label=tr("dialog.download.exists.rename"),
                editor_kind="line_edit",
                editor_text=str(suggested_name or ""),
                disable_apply_all=True,
            ),
        ),
        accept_text=tr("controls.ok"),
        apply_all_text=tr("dialog.download.exists.apply_all"),
        width_class=_DialogWidthClass.STANDARD,
    )
    if result.action == "overwrite":
        return "overwrite", "", result.apply_all
    if result.action == "rename":
        return "rename", sanitize_filename(result.text_value), False
    return "skip", "", result.apply_all


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
    total = int(max(0, int(count or 0)))
    default_n = int(max(1, int(default_limit or 1)))
    default_n = min(default_n, max(1, total))

    kind = str(origin_kind or "selection").strip().lower()
    key_map = {
        "playlist": "dialog.bulk_add.playlist",
        "folder": "dialog.bulk_add.folder",
        "file_selection": "dialog.bulk_add.selection",
        "drop": "dialog.bulk_add.drop",
        "manual_input": "dialog.bulk_add.selection",
    }
    body_lines = (tr(key_map.get(kind, "dialog.bulk_add.selection"), count=total),)

    detail_lines: list[str] = []
    label = str(origin_label or "").strip()
    if label:
        detail_lines.append(tr("dialog.bulk_add.origin", origin=label))
    target = str(target_label or "").strip()
    if target:
        detail_lines.append(tr("dialog.bulk_add.target", target=target))

    preview_lines = tuple(
        str(item or "").strip()
        for item in list(sample_titles or [])
        if str(item or "").strip()
    )[:5]

    result = _run_choice_dialog(
        parent,
        title=tr("dialog.bulk_add.title"),
        header=tr("dialog.bulk_add.header"),
        body_lines=body_lines,
        detail_lines=tuple(detail_lines),
        preview_title=tr("dialog.bulk_add.preview_header"),
        preview_lines=preview_lines,
        options=(
            _ChoiceOptionSpec(key="all", label=tr("dialog.bulk_add.option_all"), default=(total <= default_n)),
            _ChoiceOptionSpec(
                key="first_n",
                label=tr("dialog.bulk_add.option_first_n"),
                editor_kind="spinbox",
                editor_min=1,
                editor_max=max(1, total),
                editor_value=default_n,
                editor_suffix=tr("dialog.bulk_add.option_first_suffix", total=total),
                default=(total > default_n),
            ),
        ),
        accept_text=tr("controls.add"),
        reject_text=tr("controls.cancel"),
        width_class=_DialogWidthClass.WIDE,
    )
    if result.action == "cancel":
        return "cancel", 0
    if result.action == "first_n":
        return "first_n", int(result.int_value)
    return "all", total


class ExpansionProgressDialog(QtWidgets.QDialog):
    """Lightweight cancellable progress dialog for source expansion."""

    cancel_requested = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        cfg = ui(parent)
        _tune_dialog_window(self)
        self.setWindowTitle(tr("dialog.expansion_progress.title"))
        self.setModal(False)
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        _lock_close(self)

        layout = QtWidgets.QVBoxLayout(self)
        _tune_dialog_layout(layout, cfg)
        layout.addWidget(_section_label(tr("dialog.expansion_progress.header")))

        self._message_label = _wrap_label(tr("dialog.expansion_progress.generic"))
        layout.addWidget(self._message_label)

        self._bar = QtWidgets.QProgressBar(self)
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        self._bar.setMinimumHeight(cfg.control_min_h)
        layout.addWidget(self._bar)

        selected_action = {"name": "cancel"}

        def finish(action_name: str) -> None:
            selected_action["name"] = str(action_name or "cancel")
            self.cancel_requested.emit()

        button_box, _ = _build_dialog_button_box(
            self,
            cfg,
            (
                _DialogActionSpec(
                    key="cancel",
                    text=tr("controls.cancel"),
                    role=QtWidgets.QDialogButtonBox.RejectRole,
                    is_default=True,
                ),
            ),
            finish,
        )
        layout.addWidget(button_box)
        _finalize_dialog_window(self, cfg, width_class=_DialogWidthClass.STANDARD)

    def set_message(self, text: str) -> None:
        self._message_label.setText(str(text or tr("dialog.expansion_progress.generic")))


def _choose_cookie_file(parent: QtWidgets.QWidget | None) -> str:
    """Open the shared cookie-file picker and return the selected path."""
    file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent,
        tr("dialog.download.cookies.file_title"),
        "",
        tr("dialog.download.cookies.file_filter"),
    )
    return str(file_path or "").strip()


def _cookie_browser_label(browser_policy: str) -> str:
    """Return the localized label used for a cookie-browser policy."""
    normalized = DownloadPolicy.normalize_cookie_browser_policy(browser_policy)
    if normalized == DownloadPolicy.COOKIE_BROWSER_AUTO:
        return tr("common.auto")
    if DownloadPolicy.is_supported_cookie_browser(normalized):
        return tr(f"settings.browser_cookies.browser.{normalized}")
    fallback = str(browser_policy or "").strip()
    return fallback[:1].upper() + fallback[1:] if fallback else tr("common.auto")


def _ordered_cookie_browser_policies(
    browser_policy: str,
    available_browser_policies: tuple[str, ...],
) -> tuple[str, ...]:
    """Return ordered, unique browser policies shown by the cookies dialog."""
    ordered: list[str] = []
    seen: set[str] = set()

    preferred = str(browser_policy or "").strip().lower()
    if DownloadPolicy.is_supported_cookie_browser(preferred) and preferred not in seen:
        ordered.append(preferred)
        seen.add(preferred)

    for item in available_browser_policies or ():
        normalized = DownloadPolicy.normalize_cookie_browser_policy(item)
        if not DownloadPolicy.is_supported_cookie_browser(normalized) or normalized in seen:
            continue
        ordered.append(normalized)
        seen.add(normalized)
    return tuple(ordered)


def _ask_enhanced_access_intervention(
    parent: QtWidgets.QWidget,
    *,
    source_kind: str,
    source_label: str,
    detail: str,
    state: str,
    provider_state: str,
    can_retry_enhanced: bool,
    can_continue_basic: bool,
    can_continue_degraded: bool,
) -> SourceAccessInterventionResolution:
    label = str(source_label or source_kind or tr("dialog.download.access.source_fallback")).strip()
    state_key = str(state or "").strip().lower()
    text_key = "dialog.download.access.text"
    if state_key == DownloadPolicy.EXTRACTOR_ACCESS_STATE_ENHANCED_RECOMMENDED:
        text_key = "dialog.download.access.text_enhanced_recommended"
    elif state_key == DownloadPolicy.EXTRACTOR_ACCESS_STATE_ENHANCED_REQUIRED:
        text_key = "dialog.download.access.text_enhanced_required"
    elif state_key == DownloadPolicy.EXTRACTOR_ACCESS_STATE_PROVIDER_MISSING:
        text_key = "dialog.download.access.text_provider_missing"
    elif state_key == DownloadPolicy.EXTRACTOR_ACCESS_STATE_UNAVAILABLE:
        text_key = "dialog.download.access.text_unavailable"

    access_detail_lines: list[str] = []
    detail_text = str(detail or "").strip()
    if detail_text:
        access_detail_lines.append(detail_text)
    provider_text = str(provider_state or "").strip()
    if provider_text and provider_text not in {"none", "available"}:
        access_detail_lines.append(tr("dialog.download.access.provider_state", state=provider_text))

    frame = _DialogFrameSpec(
        title=tr("dialog.download.access.title"),
        header=tr("dialog.download.access.header", source=label),
        lead=tr(text_key, source=label),
        detail_lines=tuple(access_detail_lines),
        width_class=_DialogWidthClass.WIDE,
        close_policy=_DialogClosePolicy.REQUIRE_EXPLICIT_ACTION,
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)
    selected_action = {"name": "cancel"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "cancel")
        if selected_action["name"] == "cancel":
            dlg.reject()
        else:
            dlg.accept()

    actions: list[_DialogActionSpec] = []
    if can_retry_enhanced:
        actions.append(
            _DialogActionSpec(
                key=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED,
                text=tr("dialog.download.access.retry_enhanced"),
                role=QtWidgets.QDialogButtonBox.AcceptRole,
                is_default=True,
            )
        )
    if can_continue_basic:
        actions.append(
            _DialogActionSpec(
                key=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC,
                text=tr("dialog.download.access.continue_basic"),
                role=QtWidgets.QDialogButtonBox.ActionRole,
                is_default=not actions,
            )
        )
    if can_continue_degraded:
        actions.append(
            _DialogActionSpec(
                key=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_DEGRADED,
                text=tr("dialog.download.access.continue_degraded"),
                role=QtWidgets.QDialogButtonBox.ActionRole,
            )
        )
    actions.append(
        _DialogActionSpec(
            key="cancel",
            text=tr("controls.cancel"),
            role=QtWidgets.QDialogButtonBox.RejectRole,
        )
    )

    button_box, _ = _build_dialog_button_box(dlg, cfg, tuple(actions), finish)
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(button_box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()
    return SourceAccessInterventionResolution(action=str(selected_action.get("name") or "cancel"))



def _ask_cookie_access_intervention(
    parent: QtWidgets.QWidget,
    *,
    source_kind: str,
    source_label: str,
    detail: str,
    can_retry: bool,
    can_choose_cookie_file: bool,
    can_continue_without_cookies: bool,
    browser_policy: str,
    available_browser_policies: tuple[str, ...],
) -> SourceAccessInterventionResolution:
    source_mode = str(source_kind or "browser").strip().lower()
    if source_mode == "file":
        label = str(source_label or "").strip() or tr("dialog.download.cookies.source_file_fallback")
        header = tr("dialog.download.cookies.header_file", file=label)
        body = tr("dialog.download.cookies.text_file", file=label)
    else:
        label = str(source_label or "").strip() or tr("dialog.download.cookies.source_browser_fallback")
        header = tr("dialog.download.cookies.header_browser", browser=label)
        body = tr("dialog.download.cookies.text_browser", browser=label)

    frame = _DialogFrameSpec(
        title=tr("dialog.download.cookies.title"),
        header=header,
        lead=body,
        detail_lines=_normalize_dialog_lines((str(detail or "").strip(),)),
        width_class=_DialogWidthClass.WIDE,
        close_policy=_DialogClosePolicy.REQUIRE_EXPLICIT_ACTION,
    )
    dlg, cfg, layout = _build_dialog_frame(parent, frame)

    ordered_browser_policies = _ordered_cookie_browser_policies(browser_policy, available_browser_policies)
    selected_browser_policy = str(browser_policy or "").strip().lower()
    if selected_browser_policy not in ordered_browser_policies and ordered_browser_policies:
        selected_browser_policy = ordered_browser_policies[0]

    browser_combo: PopupComboBox | None = None
    if source_mode == "browser" and can_retry and len(ordered_browser_policies) > 1:
        if layout.count() > 0:
            layout.addSpacing(cfg.space_m)
        layout.addWidget(_wrap_label(tr("dialog.download.cookies.browser_picker_hint")))

        browser_host = QtWidgets.QWidget(dlg)
        browser_row = QtWidgets.QHBoxLayout(browser_host)
        browser_row.setContentsMargins(0, 0, 0, 0)
        browser_row.setSpacing(_dialog_option_spacing(cfg))

        lbl_browser = QtWidgets.QLabel(tr("dialog.download.cookies.browser_picker_label"), browser_host)
        setup_label(lbl_browser, role="fieldLabel")
        browser_row.addWidget(lbl_browser)

        browser_combo = PopupComboBox(browser_host)
        setup_combo(browser_combo, min_h=_dialog_option_min_h(cfg))
        browser_combo.setMinimumWidth(max(int(cfg.control_min_w), 180))
        for policy in ordered_browser_policies:
            browser_combo.addItem(_cookie_browser_label(policy), policy)
        current_index = browser_combo.findData(selected_browser_policy)
        if current_index < 0:
            current_index = 0
        browser_combo.setCurrentIndex(current_index)
        lbl_browser.setBuddy(browser_combo)
        browser_row.addWidget(browser_combo, 1)
        layout.addWidget(browser_host)

    selected_action = {"name": "cancel"}

    def finish(action_name: str) -> None:
        selected_action["name"] = str(action_name or "cancel")
        if selected_action["name"] == "cancel":
            dlg.reject()
        else:
            dlg.accept()

    actions: list[_DialogActionSpec] = []
    if can_retry:
        actions.append(
            _DialogActionSpec(
                key="retry",
                text=tr("dialog.download.cookies.retry"),
                role=QtWidgets.QDialogButtonBox.AcceptRole,
                is_default=True,
            )
        )
    if can_choose_cookie_file:
        actions.append(
            _DialogActionSpec(
                key="use_cookie_file",
                text=tr("dialog.download.cookies.use_file"),
                role=QtWidgets.QDialogButtonBox.ActionRole,
                is_default=not actions,
            )
        )
    if can_continue_without_cookies:
        actions.append(
            _DialogActionSpec(
                key="without_cookies",
                text=tr("dialog.download.cookies.without"),
                role=QtWidgets.QDialogButtonBox.ActionRole,
            )
        )
    actions.append(
        _DialogActionSpec(
            key="cancel",
            text=tr("controls.cancel"),
            role=QtWidgets.QDialogButtonBox.RejectRole,
        )
    )

    button_box, _ = _build_dialog_button_box(dlg, cfg, tuple(actions), finish)
    if layout.count() > 0:
        layout.addSpacing(cfg.space_l)
    layout.addWidget(button_box)
    _finalize_dialog_window(dlg, cfg, width_class=frame.width_class)
    dlg.exec_()

    selected_name = str(selected_action.get("name") or "cancel")
    if selected_name == "retry":
        chosen_browser_policy = ""
        if browser_combo is not None:
            chosen_browser_policy = str(browser_combo.currentData() or "").strip().lower()
        return SourceAccessInterventionResolution(action="retry", browser_policy=chosen_browser_policy)
    if selected_name == "without_cookies":
        return SourceAccessInterventionResolution(action="without_cookies")
    if selected_name == "use_cookie_file":
        selected_path = _choose_cookie_file(parent)
        if selected_path:
            return SourceAccessInterventionResolution(action="use_cookie_file", cookie_file_path=selected_path)
    return SourceAccessInterventionResolution()



def ask_source_access_intervention(
    parent: QtWidgets.QWidget,
    *,
    kind: str,
    source_kind: str = "",
    source_label: str = "",
    detail: str = "",
    state: str = "",
    provider_state: str = "",
    can_retry: bool = True,
    can_choose_cookie_file: bool = True,
    can_continue_without_cookies: bool = True,
    can_retry_enhanced: bool = False,
    can_continue_basic: bool = False,
    can_continue_degraded: bool = False,
    browser_policy: str = "",
    available_browser_policies: tuple[str, ...] = (),
) -> SourceAccessInterventionResolution:
    """Return the next action for a user-actionable source-access failure."""
    normalized_kind = str(kind or "cookies").strip().lower() or "cookies"
    if normalized_kind == "enhanced_access":
        return _ask_enhanced_access_intervention(
            parent,
            source_kind=source_kind,
            source_label=source_label,
            detail=detail,
            state=state,
            provider_state=provider_state,
            can_retry_enhanced=can_retry_enhanced,
            can_continue_basic=can_continue_basic,
            can_continue_degraded=can_continue_degraded,
        )
    return _ask_cookie_access_intervention(
        parent,
        source_kind=source_kind,
        source_label=source_label,
        detail=detail,
        can_retry=can_retry,
        can_choose_cookie_file=can_choose_cookie_file,
        can_continue_without_cookies=can_continue_without_cookies,
        browser_policy=browser_policy,
        available_browser_policies=available_browser_policies,
    )


def show_info(parent: QtWidgets.QWidget | None, *, title: str, message: str) -> None:
    _run_message_dialog(
        parent,
        title=title,
        message=message,
        ok_text=tr("controls.ok"),
        width_class=_DialogWidthClass.COMPACT,
    )


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
        normalized = str(text or "")
        if not normalized:
            return False
        lowered = normalized.lower()
        if "traceback" in lowered:
            return True
        if ".py" in lowered and ("line " in lowered or "file \"" in lowered or "file '" in lowered):
            return True
        if re.search(r"\.py\s*:\s*\d+", normalized):
            return True
        if re.search(r"file\s+\".*?\.py\"\s*,\s*line\s*\d+", lowered):
            return True
        return False

    def _sanitize_message(text: str) -> str:
        if _should_hide_detail(text):
            return tr("dialog.error.unexpected", msg=tr("dialog.error.details_hidden"))
        return text

    if title is not None or message is not None or header is not None:
        _run_message_dialog(
            parent,
            title=title or tr("dialog.error.title"),
            message=_sanitize_message(message or ""),
            header=header,
            ok_text=tr("controls.ok"),
        )
        return

    msg = _sanitize_message(tr(str(key or ""), **(params or {})))
    _run_message_dialog(
        parent,
        title=tr("dialog.error.title"),
        message=msg,
        header=tr("dialog.error.header"),
        ok_text=tr("controls.ok"),
    )
