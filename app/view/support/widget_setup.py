# app/view/support/widget_setup.py
from __future__ import annotations

from typing import Any, Callable, Literal, cast, overload

from PyQt5 import QtCore, QtWidgets

from app.view.support.widget_effects import enable_styled_background, repolish_widget
from app.view.ui_config import _DEFAULT_UI, UIConfig, ui


def connect_qt_signal(signal: Any, slot: Callable[..., Any]) -> None:
    """Connect a Qt signal to a callable slot."""
    signal.connect(slot)


class _SpinboxFocusProxy(QtCore.QObject):
    """Mirror focus state from the spinbox editor back to the styled host widget."""

    def __init__(self, spinbox: QtWidgets.QAbstractSpinBox) -> None:
        super().__init__(spinbox)
        self._spinbox = spinbox

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in {
            QtCore.QEvent.Type.FocusIn,
            QtCore.QEvent.Type.FocusOut,
            QtCore.QEvent.Type.EnabledChange,
            QtCore.QEvent.Type.Hide,
        }:
            QtCore.QTimer.singleShot(0, self._sync_focus_state)
        return super().eventFilter(obj, event)

    def _sync_focus_state(self) -> None:
        spinbox = self._spinbox
        focus_within = bool(spinbox.isEnabled() and spinbox.hasFocus())
        line_edit = spinbox.lineEdit()
        if line_edit is not None:
            focus_within = focus_within or bool(line_edit.hasFocus())
        if spinbox.property('focusWithin') != focus_within:
            spinbox.setProperty('focusWithin', focus_within)
            repolish_widget(spinbox)


def make_grid(columns: int, cfg: UIConfig | None = None) -> QtWidgets.QGridLayout:
    """Build a grid layout with the shared spacing defaults."""
    cfg = cfg or _DEFAULT_UI
    layout = QtWidgets.QGridLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(cfg.space_l)
    layout.setVerticalSpacing(cfg.space_s)
    for index in range(max(0, int(columns))):
        layout.setColumnStretch(index, 1)
    return layout


def set_widget_style_role(
    w: QtWidgets.QWidget,
    *,
    chrome: str | None = None,
    ui_role: str | None = None,
) -> None:
    """Assign shared style-role properties to a widget."""
    enable_styled_background(w)
    if chrome is not None:
        w.setProperty('chrome', str(chrome))
    if ui_role is not None:
        w.setProperty('role', str(ui_role))


def set_interactive_cursor(w: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """Apply the shared pointing-hand cursor to interactive widgets."""
    w.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    return w


def set_passive_cursor(w: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """Apply the shared arrow cursor to non-interactive container surfaces."""
    w.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
    return w


def setup_control(w: QtWidgets.QWidget, *, min_h: int | None = None, min_w: int | None = None) -> None:
    """Apply the shared minimum control sizing rules."""
    cfg = ui(w)
    height = int(min_h if min_h is not None else cfg.control_min_h)
    width = int(min_w if min_w is not None else cfg.control_min_w)
    w.setMinimumHeight(height)
    w.setMinimumWidth(width)


def setup_button(
    btn: QtWidgets.QAbstractButton,
    *,
    chrome: str | None = 'action',
    min_h: int | None = None,
    min_w: int | None = None,
) -> None:
    """Configure a button with the shared visual defaults."""
    set_widget_style_role(btn, chrome=chrome)
    btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    set_interactive_cursor(btn)
    setup_control(btn, min_h=min_h, min_w=min_w)


def setup_combo(cb: QtWidgets.QComboBox, *, min_h: int | None = None, min_w: int | None = None) -> None:
    """Configure a combo box with the shared field styling."""
    set_widget_style_role(cb, chrome='field', ui_role='combo')
    cb.setProperty('focusWithin', False)
    cb.setProperty('popupOpen', False)
    set_interactive_cursor(cb)
    setup_control(cb, min_h=min_h, min_w=min_w)
    line_edit = cb.lineEdit()
    if line_edit is not None:
        from app.view.components.text_context_menu import install_text_context_menu

        line_edit.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
        install_text_context_menu(line_edit)
    sync_visual_state = cast(Callable[[], None] | None, getattr(cb, 'sync_visual_state', None))
    if sync_visual_state is not None:
        QtCore.QTimer.singleShot(0, sync_visual_state)


def setup_spinbox(sp: QtWidgets.QAbstractSpinBox, *, min_h: int | None = None, min_w: int | None = None) -> None:
    """Configure a spin box with the shared field styling."""
    set_widget_style_role(sp, chrome='field', ui_role='spinbox')
    sp.setProperty('focusWithin', False)
    sp.setFrame(False)
    set_interactive_cursor(sp)

    focus_proxy = getattr(sp, '_focus_proxy', None)
    if not isinstance(focus_proxy, _SpinboxFocusProxy):
        focus_proxy = _SpinboxFocusProxy(sp)
        sp._focus_proxy = focus_proxy
        sp.installEventFilter(focus_proxy)
        line_edit = sp.lineEdit()
        if line_edit is not None:
            line_edit.setFrame(False)
            line_edit.setAttribute(QtCore.Qt.WidgetAttribute.WA_MacShowFocusRect, False)
            line_edit.setCursor(QtCore.Qt.CursorShape.IBeamCursor)
            line_edit.installEventFilter(focus_proxy)
            from app.view.components.text_context_menu import install_text_context_menu

            install_text_context_menu(line_edit)
    setup_control(sp, min_h=min_h, min_w=min_w)


def setup_input(edit: QtWidgets.QLineEdit, *, placeholder: str | None = None, min_h: int | None = None) -> None:
    """Configure a line edit with the shared input styling."""
    set_widget_style_role(edit, chrome='field', ui_role='input')
    if placeholder is not None:
        edit.setPlaceholderText(placeholder)
    setup_control(edit, min_h=min_h)
    from app.view.components.text_context_menu import install_text_context_menu

    install_text_context_menu(edit)


def setup_text_editor(
    edit: QtWidgets.QTextEdit | QtWidgets.QPlainTextEdit,
    *,
    placeholder: str | None = None,
) -> None:
    """Configure a text editor with the shared field styling."""
    set_widget_style_role(edit, chrome='field', ui_role='textEditor')
    if placeholder is not None:
        edit.setPlaceholderText(placeholder)
    from app.view.components.text_context_menu import install_text_context_menu

    install_text_context_menu(edit)


def setup_label(
    label: QtWidgets.QLabel,
    *,
    role: str = 'fieldLabel',
    buddy: QtWidgets.QWidget | None = None,
) -> QtWidgets.QLabel:
    """Apply the shared role and buddy wiring to a label."""
    label.setProperty('role', role)
    if buddy is not None:
        label.setBuddy(buddy)
    return label


def setup_option_checkbox(
    cb: QtWidgets.QCheckBox,
    *,
    min_h: int | None = None,
) -> QtWidgets.QCheckBox:
    """Configure an option checkbox row with shared sizing."""
    cfg = ui(cb)
    row_h = int(min_h if min_h is not None else cfg.option_row_min_h)
    setup_toggle_button(cb, min_h=row_h)
    cb.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    return cb


def setup_toggle_button(
    btn: QtWidgets.QAbstractButton,
    *,
    min_h: int | None = None,
) -> QtWidgets.QAbstractButton:
    """Configure a checkbox or radio button with shared interactive defaults."""
    if min_h is not None:
        btn.setMinimumHeight(int(min_h))
    btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    set_interactive_cursor(btn)
    return btn


def build_field_stack(
    parent: QtWidgets.QWidget,
    label_text: str,
    content: QtWidgets.QWidget | QtWidgets.QLayout,
    *,
    buddy: QtWidgets.QWidget | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    """Build a labeled vertical field stack."""
    cfg = ui(parent)
    host, lay = build_layout_host(
        parent=parent,
        layout="vbox",
        margins=(0, 0, 0, 0),
        spacing=cfg.space_s,
    )
    lay = cast(QtWidgets.QVBoxLayout, lay)

    label = QtWidgets.QLabel(label_text, host)
    setup_label(label, buddy=buddy)
    lay.addWidget(label)

    if isinstance(content, QtWidgets.QLayout):
        lay.addLayout(content)
    else:
        lay.addWidget(content)

    return host, label


def build_setting_row(
    *,
    label_text: str,
    control: QtWidgets.QWidget,
    tooltip: str = "",
    parent: QtWidgets.QWidget | None = None,
    cfg: UIConfig | None = None,
    control_host: QtWidgets.QWidget | None = None,
    include_info: bool = True,
    label_role: str | None = "settingsRowLabel",
    label_min_width: int | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    """Build the standard two-column settings row."""
    resolved_cfg = cfg or ui(parent or control)
    host, layout = build_layout_host(
        parent=parent,
        layout="grid",
        margins=(0, 0, 0, 0),
        hspacing=resolved_cfg.space_l,
        vspacing=0,
        column_stretches={1: 1},
    )
    grid = cast(QtWidgets.QGridLayout, layout)

    label = QtWidgets.QLabel(str(label_text or ""), host)
    label.setMinimumWidth(
        int(
            label_min_width
            if label_min_width is not None
            else resolved_cfg.control_min_w + resolved_cfg.space_l * 10
        )
    )
    label.setWordWrap(True)
    label.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Preferred)
    if label_role is not None:
        label.setProperty("role", str(label_role))

    grid.addWidget(label, 0, 0)
    grid.addWidget(control_host or control, 0, 1)

    if include_info:
        from app.view.components.hint_popup import InfoButton

        info = InfoButton(str(tooltip or ""))
        info.setFixedSize(int(resolved_cfg.control_min_h), int(resolved_cfg.control_min_h))
        grid.addWidget(info, 0, 2)
        grid.setColumnStretch(2, 0)

    return host, label


def setup_layout(
    layout: QtWidgets.QLayout,
    *,
    cfg: UIConfig | None = None,
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
) -> None:
    """Apply shared spacing and margin defaults to a layout."""
    cfg = cfg or _DEFAULT_UI
    resolved_margins = tuple(int(v) for v in (margins or (cfg.margin, cfg.margin, cfg.margin, cfg.margin)))
    layout.setContentsMargins(*resolved_margins)
    layout.setSpacing(int(cfg.space_m if spacing is None else spacing))
    if hspacing is not None and hasattr(layout, "setHorizontalSpacing"):
        cast(Any, layout).setHorizontalSpacing(int(hspacing))
    if vspacing is not None and hasattr(layout, "setVerticalSpacing"):
        cast(Any, layout).setVerticalSpacing(int(vspacing))
    if isinstance(layout, QtWidgets.QGridLayout):
        for index, stretch in (column_stretches or {}).items():
            layout.setColumnStretch(int(index), int(stretch))

@overload
def build_layout_host(
    *,
    parent: QtWidgets.QWidget | None = None,
    layout: Literal["hbox"],
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
    object_name: str | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QHBoxLayout]:
    ...

@overload
def build_layout_host(
    *,
    parent: QtWidgets.QWidget | None = None,
    layout: Literal["vbox"] = "vbox",
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
    object_name: str | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
    ...

@overload
def build_layout_host(
    *,
    parent: QtWidgets.QWidget | None = None,
    layout: Literal["grid"],
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
    object_name: str | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QGridLayout]:
    ...

@overload
def build_layout_host(
    *,
    parent: QtWidgets.QWidget | None = None,
    layout: Literal["form"],
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
    object_name: str | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QFormLayout]:
    ...


def build_layout_host(
    *,
    parent: QtWidgets.QWidget | None = None,
    layout: Literal["hbox", "vbox", "grid", "form"] = "vbox",
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
    object_name: str | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QLayout]:
    """Create a QWidget host with a configured child layout."""
    host = QtWidgets.QWidget(parent)
    if object_name:
        host.setObjectName(object_name)

    if layout == "hbox":
        root: QtWidgets.QLayout = QtWidgets.QHBoxLayout(host)
    elif layout == "grid":
        root = QtWidgets.QGridLayout(host)
    elif layout == "form":
        root = QtWidgets.QFormLayout(host)
    else:
        root = QtWidgets.QVBoxLayout(host)

    setup_layout(
        root,
        cfg=ui(parent or host),
        margins=margins,
        spacing=spacing,
        hspacing=hspacing,
        vspacing=vspacing,
        column_stretches=column_stretches,
    )
    return host, root
