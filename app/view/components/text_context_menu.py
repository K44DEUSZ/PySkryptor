# app/view/components/text_context_menu.py
from __future__ import annotations

from typing import Any, Callable, cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.model.core.runtime.localization import tr
from app.view.support.popup_host import PopupHostBinding, clamp_popup_geometry, hide_popup_widget
from app.view.support.widget_effects import (
    configure_floating_popup_surface,
    popup_host_root_margins,
    repolish_widget,
)
from app.view.support.widget_setup import set_interactive_cursor, set_passive_cursor, setup_layout
from app.view.ui_config import ui


def _text_menu_label(name: str) -> str:
    return tr(f"common.edit_menu.{name}")


def _text_menu_shortcut(shortcut: QtGui.QKeySequence | QtGui.QKeySequence.StandardKey) -> str:
    try:
        return QtGui.QKeySequence(shortcut).toString(QtGui.QKeySequence.NativeText)
    except (AttributeError, RuntimeError, TypeError):
        return ""


def _has_clipboard_text() -> bool:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    clipboard = cast(QtWidgets.QApplication, app).clipboard()
    if clipboard is None:
        return False
    mime = clipboard.mimeData()
    return bool(mime is not None and mime.hasText())


def _text_widget_has_content(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.text())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return bool(widget.toPlainText())
    return False


def _text_widget_has_selection(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.hasSelectedText())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return bool(widget.textCursor().hasSelection())
    return False


def _text_widget_can_undo(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.isUndoAvailable())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        try:
            return bool(widget.document().isUndoAvailable())
        except (AttributeError, RuntimeError, TypeError):
            return False
    return False


def _text_widget_can_redo(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.isRedoAvailable())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        try:
            return bool(widget.document().isRedoAvailable())
        except (AttributeError, RuntimeError, TypeError):
            return False
    return False


def _text_widget_is_read_only(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.isReadOnly())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return bool(widget.isReadOnly())
    return True


def _text_widget_can_paste(widget: QtWidgets.QWidget) -> bool:
    if _text_widget_is_read_only(widget):
        return False
    if isinstance(widget, QtWidgets.QLineEdit):
        return _has_clipboard_text()
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        try:
            return bool(widget.canPaste())
        except (AttributeError, RuntimeError, TypeError):
            return _has_clipboard_text()
    return False


def _delete_text_selection(widget: QtWidgets.QWidget) -> None:
    if _text_widget_is_read_only(widget) or not _text_widget_has_selection(widget):
        return
    if isinstance(widget, QtWidgets.QLineEdit):
        widget.del_()
        return
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        cursor = widget.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            widget.setTextCursor(cursor)


class _TextContextActionRow(QtWidgets.QFrame):
    """Single clickable action row rendered inside the custom text context menu."""

    triggered = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        label: str,
        shortcut_text: str = "",
        enabled: bool,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        cfg = ui(self)

        self._pressed = False
        self.setProperty("role", "textContextAction")
        self.setProperty("hovered", False)
        self.setProperty("pressed", False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(max(int(cfg.control_min_h) - 2, 28))

        lay = QtWidgets.QHBoxLayout(self)
        setup_layout(lay, cfg=cfg, margins=(cfg.pad_x_m, cfg.space_s, cfg.pad_x_m, cfg.space_s), spacing=cfg.space_l)

        self._label = QtWidgets.QLabel(label, self)
        self._label.setProperty("role", "textContextActionLabel")
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft)

        self._shortcut = QtWidgets.QLabel(shortcut_text, self)
        self._shortcut.setProperty("role", "textContextActionShortcut")
        self._shortcut.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight)
        self._shortcut.setVisible(bool(shortcut_text))

        lay.addWidget(self._label, 1)
        lay.addWidget(self._shortcut, 0)

        self._apply_enabled_state(bool(enabled))

    def _apply_enabled_state(self, enabled: bool) -> None:
        self.setProperty("enabledState", bool(enabled))
        self.setEnabled(bool(enabled))
        if enabled:
            set_interactive_cursor(self)
        else:
            set_passive_cursor(self)
        self._label.setEnabled(bool(enabled))
        self._shortcut.setEnabled(bool(enabled))
        repolish_widget(self)
        repolish_widget(self._label)
        repolish_widget(self._shortcut)

    def _set_state(self, *, hovered: bool | None = None, pressed: bool | None = None) -> None:
        changed = False
        if hovered is not None and self.property("hovered") != bool(hovered):
            self.setProperty("hovered", bool(hovered))
            changed = True
        if pressed is not None and self.property("pressed") != bool(pressed):
            self.setProperty("pressed", bool(pressed))
            changed = True
        if changed:
            repolish_widget(self)

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().enterEvent(event)
        if self.isEnabled():
            self._set_state(hovered=True)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._set_state(hovered=False, pressed=False)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self.isEnabled() and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._pressed = True
            self._set_state(pressed=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._pressed and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._pressed = False
            inside = self.rect().contains(event.pos())
            self._set_state(pressed=False, hovered=inside)
            if self.isEnabled() and inside:
                self.triggered.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TextContextSeparator(QtWidgets.QFrame):
    """One-pixel separator used inside the custom text context popup."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "textContextSeparator")
        self.setFixedHeight(1)


class _TextContextPopup(QtWidgets.QWidget):
    """Floating context menu popup shared by supported text editors."""

    def __init__(self) -> None:
        super().__init__(
            None,
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint,
        )

        cfg = ui(self)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*popup_host_root_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        configure_floating_popup_surface(self, self._body)

        self._content = QtWidgets.QVBoxLayout(self._body)
        setup_layout(
            self._content,
            cfg=cfg,
            margins=(cfg.space_s, cfg.space_s, cfg.space_s, cfg.space_s),
            spacing=cfg.space_s,
        )

        root.addWidget(self._body)

        self._popup_binding = PopupHostBinding(self)
        self._popup_binding.install_app_filter()

    def _bind_window(self, widget: QtWidgets.QWidget | None) -> None:
        self._popup_binding.bind_window(widget)

    def _contains_widget(self, widget: QtWidgets.QWidget | None) -> bool:
        return self._popup_binding.contains_widget(widget, self, self._body)

    def _clear_content(self) -> None:
        while self._content.count():
            item = self._content.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

    def _trigger_action(self, handler: Callable[[], None]) -> None:
        self.hide()
        if callable(handler):
            handler()

    def _add_action(self, label: str, shortcut_text: str, enabled: bool, handler: Callable[[], None]) -> None:
        row = _TextContextActionRow(label=label, shortcut_text=shortcut_text, enabled=enabled, parent=self._body)
        row.triggered.connect(lambda h=handler: self._trigger_action(h))
        self._content.addWidget(row)

    def _rebuild(self, widget: QtWidgets.QWidget) -> None:
        self._clear_content()
        for item in build_text_context_menu(widget):
            if item is None:
                self._content.addWidget(_TextContextSeparator(self._body))
                continue
            label, shortcut_text, enabled, handler = item
            self._add_action(label, shortcut_text, enabled, handler)

    def show_for_widget(self, widget: QtWidgets.QWidget, global_pos: QtCore.QPoint) -> None:
        self._bind_window(widget)
        self._rebuild(widget)
        self.adjustSize()

        geom = self.frameGeometry()
        geom.moveTopLeft(global_pos)
        geom = clamp_popup_geometry(geom, point=global_pos, fallback_widget=widget)

        self.move(geom.topLeft())
        self.show()
        self.raise_()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        handled = self._popup_binding.handle_dismiss_event(
            obj=obj,
            event=event,
            popup_widget=self,
            hide_popup=self.hide_popup,
            contains_widget=self._contains_widget,
            handle_escape=True,
        )
        if handled:
            return True
        return super().eventFilter(obj, event)

    def hide_popup(self) -> None:
        hide_popup_widget(self)

    def __del__(self) -> None:
        self._popup_binding.cleanup()


_TEXT_CONTEXT_POPUP: _TextContextPopup | None = None


def text_context_popup() -> _TextContextPopup:
    global _TEXT_CONTEXT_POPUP
    if _TEXT_CONTEXT_POPUP is None:
        _TEXT_CONTEXT_POPUP = _TextContextPopup()
    return _TEXT_CONTEXT_POPUP


def build_text_context_menu(widget: QtWidgets.QWidget) -> list[Any]:
    if not isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return []

    read_only = _text_widget_is_read_only(widget)
    has_selection = _text_widget_has_selection(widget)
    has_content = _text_widget_has_content(widget)
    can_paste = _text_widget_can_paste(widget)

    if read_only:
        return [
            (
                _text_menu_label("copy"),
                _text_menu_shortcut(QtGui.QKeySequence.Copy),
                has_selection,
                getattr(widget, "copy", None),
            ),
            (
                _text_menu_label("select_all"),
                _text_menu_shortcut(QtGui.QKeySequence.SelectAll),
                has_content,
                getattr(widget, "selectAll", None),
            ),
        ]

    return [
        (
            _text_menu_label("undo"),
            _text_menu_shortcut(QtGui.QKeySequence.Undo),
            _text_widget_can_undo(widget),
            getattr(widget, "undo", None),
        ),
        (
            _text_menu_label("redo"),
            _text_menu_shortcut(QtGui.QKeySequence.Redo),
            _text_widget_can_redo(widget),
            getattr(widget, "redo", None),
        ),
        None,
        (
            _text_menu_label("cut"),
            _text_menu_shortcut(QtGui.QKeySequence.Cut),
            has_selection,
            getattr(widget, "cut", None),
        ),
        (
            _text_menu_label("copy"),
            _text_menu_shortcut(QtGui.QKeySequence.Copy),
            has_selection,
            getattr(widget, "copy", None),
        ),
        (
            _text_menu_label("paste"),
            _text_menu_shortcut(QtGui.QKeySequence.Paste),
            can_paste,
            getattr(widget, "paste", None),
        ),
        (
            _text_menu_label("delete"),
            _text_menu_shortcut(QtGui.QKeySequence.Delete),
            has_selection,
            lambda: _delete_text_selection(widget),
        ),
        None,
        (
            _text_menu_label("select_all"),
            _text_menu_shortcut(QtGui.QKeySequence.SelectAll),
            has_content,
            getattr(widget, "selectAll", None),
        ),
    ]


class _TextContextMenuFilter(QtCore.QObject):
    """Intercepts the native context menu event for supported text widgets."""

    def __init__(self, widget: QtWidgets.QWidget) -> None:
        super().__init__(widget)
        self._widget = widget

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Type.ContextMenu and isinstance(event, QtGui.QContextMenuEvent):
            if not isinstance(self._widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
                return False
            text_context_popup().show_for_widget(self._widget, event.globalPos())
            return True
        return super().eventFilter(obj, event)


def install_text_context_menu(widget: QtWidgets.QWidget) -> None:
    if not isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return
    current = getattr(widget, "_text_context_menu_filter", None)
    if isinstance(current, _TextContextMenuFilter):
        return

    context_filter = _TextContextMenuFilter(widget)
    widget._text_context_menu_filter = context_filter
    widget.installEventFilter(context_filter)

    if isinstance(widget, QtWidgets.QAbstractScrollArea):
        viewport = widget.viewport()
        if viewport is not None:
            viewport.installEventFilter(context_filter)
