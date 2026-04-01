# app/view/components/popup_combo.py
from __future__ import annotations

from typing import Any, Callable, Iterable, TypeVar

from PyQt5 import QtCore, QtGui, QtWidgets, sip

from app.model.core.config.policy import LanguagePolicy
from app.model.core.runtime.localization import SpecialLanguageOptions, build_language_options
from app.model.core.utils.string_utils import normalize_lang_code
from app.view.components.hint_popup import hint_popup
from app.view.support.popup_host import (
    PopupHostBinding,
    PopupHostRegistry,
    anchored_popup_geometry,
    hide_popup_widget,
)
from app.view.support.widget_effects import (
    configure_floating_popup_surface,
    enable_styled_background,
    popup_host_root_margins,
    repolish_widget,
)
from app.view.support.widget_setup import set_interactive_cursor, setup_combo, setup_layout
from app.view.ui_config import ui

_POPUP_VISIBLE_ROWS_DEFAULT = 10
_POPUP_MULTISELECT_VISIBLE_ROWS_DEFAULT = 8
_POPUP_COMBO_EXTRA_W = 52
_POPUP_MULTISELECT_EXTRA_W = 60

PopupWidgetT = TypeVar("PopupWidgetT", bound=QtWidgets.QWidget)


def _popup_content_extra_h(cfg) -> int:
    return int(cfg.pad_y_l) + int(cfg.space_s)

def _popup_multiselect_inner_gap(cfg) -> int:
    return max(1, int(cfg.space_s) // 2)

def _widget_alive(w: QtWidgets.QWidget | None) -> bool:
    if not isinstance(w, QtWidgets.QWidget):
        return False
    if sip is None:
        return True
    try:
        return not bool(sip.isdeleted(w))
    except (AttributeError, RuntimeError, TypeError):
        return False

def _widget_visible(w: QtWidgets.QWidget | None) -> bool:
    return _widget_alive(w) and bool(w.isVisible())

def _popup_instance(owner: object, popup_type: type[PopupWidgetT]) -> PopupWidgetT | None:
    popup = getattr(owner, "_popup", None)
    return popup if isinstance(popup, popup_type) and _widget_alive(popup) else None

def _sum_list_row_heights(view: QtWidgets.QAbstractItemView, count: int, fallback: int) -> int:
    total = 0
    for row in range(max(0, int(count))):
        try:
            row_hint = int(view.sizeHintForRow(row) or 0)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            row_hint = 0
        total += max(int(fallback), int(row_hint), 1)
    return total

def _layout_vertical_extra(layout: QtWidgets.QLayout | None) -> int:
    if layout is None:
        return 0
    margins = layout.contentsMargins()
    return int(margins.top() + margins.bottom())

def _layout_contents_margins(layout: QtWidgets.QLayout | None) -> tuple[int, int, int, int]:
    if layout is None:
        return 0, 0, 0, 0
    margins = layout.contentsMargins()
    return int(margins.left()), int(margins.top()), int(margins.right()), int(margins.bottom())

def normalize_combo_code(code: str, *, default: str = "") -> str:
    raw = LanguagePolicy.normalize_choice_value(code)
    if raw in {
        LanguagePolicy.AUTO,
        LanguagePolicy.DEFAULT_UI,
        LanguagePolicy.LAST_USED,
        LanguagePolicy.PREFERRED,
        "-",
    }:
        return raw

    norm = normalize_lang_code(raw, drop_region=False)
    if norm:
        return norm

    fallback_raw = LanguagePolicy.normalize_choice_value(default)
    if fallback_raw in {
        LanguagePolicy.AUTO,
        LanguagePolicy.DEFAULT_UI,
        LanguagePolicy.LAST_USED,
        LanguagePolicy.PREFERRED,
        "-",
    }:
        return fallback_raw

    fallback = normalize_lang_code(fallback_raw, drop_region=False)
    return fallback or fallback_raw

def set_combo_data(
    combo: QtWidgets.QComboBox,
    value: Any,
    *,
    fallback_data: Any | None = None,
    fallback: Any | None = None,
) -> None:
    target = str(value or "").strip().lower()
    fallback_value = fallback_data if fallback_data is not None else fallback
    fallback = str(fallback_value or "").strip().lower() if fallback_value is not None else ""

    idx = -1
    for i in range(combo.count()):
        current = str(combo.itemData(i) or "").strip().lower()
        if current == target:
            idx = i
            break

    if idx < 0 and fallback_value is not None:
        for i in range(combo.count()):
            current = str(combo.itemData(i) or "").strip().lower()
            if current == fallback:
                idx = i
                break

    if idx < 0 < combo.count():
        idx = 0
    combo.setCurrentIndex(idx)

def set_combo_code(
    combo: QtWidgets.QComboBox,
    code: str,
    *,
    fallback_code: str,
) -> None:
    wanted = normalize_combo_code(code, default=fallback_code)
    idx = combo.findData(wanted)
    if idx < 0:
        idx = combo.findData(normalize_combo_code(fallback_code, default=fallback_code))
    if idx < 0 < combo.count():
        idx = 0
    combo.setCurrentIndex(idx)

def combo_current_code(combo: QtWidgets.QComboBox, *, default: str) -> str:
    data = combo.currentData()
    return normalize_combo_code(str(data or ""), default=default)

def rebuild_code_combo(
    combo: QtWidgets.QComboBox,
    items: Iterable[tuple[str, str]],
    *,
    desired_code: str,
    fallback_code: str,
) -> None:
    target = normalize_combo_code(desired_code, default=fallback_code)

    combo.blockSignals(True)
    try:
        combo.clear()
        for code, label in items:
            combo.addItem(label, code)
        set_combo_code(combo, target, fallback_code=fallback_code)
    finally:
        combo.blockSignals(False)

class _ComboPopupItemDelegate(QtWidgets.QStyledItemDelegate):
    """Ensure popup rows keep the shared minimum control height."""

    def sizeHint(
        self,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> QtCore.QSize:
        size = super().sizeHint(option, index)
        cfg = ui(self.parent() if isinstance(self.parent(), QtWidgets.QWidget) else None)
        size.setHeight(max(int(size.height()), int(cfg.control_min_h)))
        return size

class _PopupCheckItem(QtWidgets.QWidget):
    """Single checkable row rendered inside the multi-select popup."""

    toggled = QtCore.pyqtSignal()

    def __init__(self, text: str, *, checked: bool = False, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        cfg = ui(self)
        self.setProperty("role", "menuCheckItem")
        enable_styled_background(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setMinimumHeight(int(cfg.control_min_h))

        lay = QtWidgets.QHBoxLayout(self)
        setup_layout(lay, cfg=cfg, margins=(cfg.pad_x_m, 0, cfg.pad_x_m, 0), spacing=0)

        self.chk_item = QtWidgets.QCheckBox(str(text), self)
        self.chk_item.setProperty("role", "menuCheck")
        self.chk_item.setChecked(bool(checked))
        self.chk_item.setMinimumHeight(int(cfg.control_min_h))
        self.chk_item.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        set_interactive_cursor(self.chk_item)
        self.chk_item.installEventFilter(self)
        self.chk_item.toggled.connect(self._on_toggled)
        lay.addWidget(self.chk_item, 1)

        self._set_hovered(False)
        self._sync_state()

    def text(self) -> str:
        return str(self.chk_item.text() or "")

    def is_checked(self) -> bool:
        return bool(self.chk_item.isChecked())

    def _set_hovered(self, hovered: bool) -> None:
        self.setProperty("hovered", bool(hovered))
        repolish_widget(self)

    def _sync_state(self) -> None:
        self.setProperty("checkedState", bool(self.chk_item.isChecked()))
        repolish_widget(self)

    def _on_toggled(self) -> None:
        self._sync_state()
        self.toggled.emit()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.chk_item.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._set_hovered(False)
        super().leaveEvent(event)

    # noinspection PyPep8Naming
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self.chk_item:
            if event.type() == QtCore.QEvent.Type.Enter:
                self._set_hovered(True)
            elif event.type() == QtCore.QEvent.Type.Leave:
                self._set_hovered(False)
        return super().eventFilter(obj, event)

class _ComboPopupList(QtWidgets.QListView):
    """List view preconfigured for the custom combo popup chrome."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "comboPopupList")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setMouseTracking(True)
        set_interactive_cursor(self)
        self.setSpacing(0)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.setUniformItemSizes(True)
        self.setItemDelegate(_ComboPopupItemDelegate(self))
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        vp = self.viewport()
        if vp is not None:
            vp.setProperty("role", "comboPopupViewport")
            enable_styled_background(vp)
            vp.setMouseTracking(True)
            set_interactive_cursor(vp)

class ComboPopup(QtWidgets.QWidget):
    """Frameless popup list used as the custom dropdown for PopupComboBox."""
    closed = QtCore.pyqtSignal()
    index_chosen = QtCore.pyqtSignal(int)

    def __init__(self, combo: "PopupComboBox") -> None:
        super().__init__(
            None,
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint,
        )
        self._combo = combo
        cfg = ui(self)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*popup_host_root_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        configure_floating_popup_surface(self, self._body)

        body_lay = QtWidgets.QVBoxLayout(self._body)
        setup_layout(body_lay, cfg=cfg, margins=(cfg.space_s, cfg.space_s, cfg.space_s, cfg.space_s), spacing=0)

        self._list = _ComboPopupList(self._body)
        body_lay.addWidget(self._list)
        root.addWidget(self._body)

        self._hover_tip_row = -1
        self._hover_tip_text = ""
        viewport = self._list.viewport()
        if viewport is not None:
            viewport.installEventFilter(self)

        self._list.clicked.connect(self._on_index_clicked)
        self._list.activated.connect(self._on_index_clicked)

    def sync_from_combo(self) -> None:
        combo = self._combo
        self._list.setModel(combo.model())
        self._list.setRootIndex(combo.rootModelIndex())
        self._list.setModelColumn(combo.modelColumn())
        self._list.setIconSize(combo.iconSize())
        self._sync_current_index()

    def show_for(self, combo: "PopupComboBox") -> None:
        self.sync_from_combo()
        self._apply_geometry(combo)
        self.show()
        self.raise_()

    def refresh_geometry(self) -> None:
        if self.isVisible():
            self._apply_geometry(self._combo)

    def move_selection(self, step: int) -> None:
        count = self._combo.count()
        if count <= 0:
            return
        current = self._list.currentIndex()
        row = current.row() if current.isValid() else self._combo.currentIndex()
        row = max(0, min(count - 1, row + int(step)))
        self._select_row(row)

    def jump_selection(self, row: int) -> None:
        if self._combo.count() <= 0:
            return
        self._select_row(max(0, min(self._combo.count() - 1, int(row))))

    def page_selection(self, direction: int) -> None:
        visible_rows = max(1, int(self._combo.maxVisibleItems() or _POPUP_VISIBLE_ROWS_DEFAULT))
        step = max(1, min(visible_rows, max(self._combo.count(), 1)) - 1)
        self.move_selection(step if direction > 0 else -step)

    def current_row(self) -> int:
        current = self._list.currentIndex()
        return int(current.row()) if current.isValid() else int(self._combo.currentIndex())

    def _sync_current_index(self) -> None:
        self._select_row(int(self._combo.currentIndex()), allow_clear=True)

    def _select_row(self, row: int, *, allow_clear: bool = False) -> None:
        model = self._list.model()
        root = self._list.rootIndex()
        if model is None or row < 0 or row >= self._combo.count():
            if allow_clear:
                self._list.clearSelection()
                self._list.setCurrentIndex(QtCore.QModelIndex())
            return

        idx = model.index(row, self._combo.modelColumn(), root)
        if idx.isValid():
            sel = self._list.selectionModel()
            self._list.setCurrentIndex(idx)
            if sel is not None:
                sel.setCurrentIndex(idx, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self._list.scrollTo(idx, QtWidgets.QAbstractItemView.PositionAtCenter)

    def _apply_geometry(self, combo: "PopupComboBox") -> None:
        left_margin, top_margin, right_margin, bottom_margin = _layout_contents_margins(self.layout())
        width = self._popup_width(combo) + left_margin + right_margin
        height = self._popup_height(combo)
        self.setGeometry(
            anchored_popup_geometry(
                combo,
                width=width,
                height=height,
                host_margins=(left_margin, top_margin, right_margin, bottom_margin),
            )
        )

    @staticmethod
    def _visible_row_count(combo: "PopupComboBox") -> int:
        return max(1, min(int(combo.maxVisibleItems() or _POPUP_VISIBLE_ROWS_DEFAULT), max(combo.count(), 1)))

    def _popup_width(self, combo: "PopupComboBox") -> int:
        fm = combo.fontMetrics()
        text_w = 0
        for row in range(combo.count()):
            text_w = max(text_w, fm.horizontalAdvance(combo.itemText(row)))

        icon_w = combo.iconSize().width() if combo.iconSize().width() > 0 else 0
        needs_scroll = combo.count() > self._visible_row_count(combo)
        scroll_w = 0
        if needs_scroll:
            scroll_w = self._list.style().pixelMetric(
                QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent,
                None,
                self._list,
            )
        content_w = text_w + icon_w + scroll_w + int(_POPUP_COMBO_EXTRA_W)
        return max(int(combo.width()), int(content_w))

    def _popup_height(self, combo: "PopupComboBox") -> int:
        cfg = ui(combo)
        visible_rows = self._visible_row_count(combo)
        row_h = int(cfg.control_min_h)
        content_h = _sum_list_row_heights(self._list, visible_rows, row_h)
        self._list.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if combo.count() <= visible_rows
            else QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        body_h = content_h + _layout_vertical_extra(self._body.layout())
        host_h = body_h + _layout_vertical_extra(self.layout())
        return max(int(host_h), int(visible_rows * row_h + _popup_content_extra_h(cfg)))

    @staticmethod
    def _tooltip_text_for_index(index: QtCore.QModelIndex) -> str:
        if not index.isValid():
            return ""
        return str(index.data(QtCore.Qt.ItemDataRole.ToolTipRole) or "").strip()

    def _hide_hover_hint(self) -> None:
        self._hover_tip_row = -1
        self._hover_tip_text = ""
        hint_popup().hide()

    def _update_hover_hint(self, pos: QtCore.QPoint) -> None:
        index = self._list.indexAt(pos)
        text = self._tooltip_text_for_index(index)
        if not index.isValid() or not text:
            self._hide_hover_hint()
            return

        row = int(index.row())
        if row == self._hover_tip_row and text == self._hover_tip_text:
            return

        self._hover_tip_row = row
        self._hover_tip_text = text
        hint_popup().show_for_rect(self._list.viewport(), self._list.visualRect(index), text)

    # noinspection PyPep8Naming
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self._list.viewport():
            if event.type() == QtCore.QEvent.Type.MouseMove and isinstance(event, QtGui.QMouseEvent):
                self._update_hover_hint(event.pos())
            elif event.type() in {QtCore.QEvent.Type.Leave, QtCore.QEvent.Type.Hide}:
                self._hide_hover_hint()
        return super().eventFilter(obj, event)

    def _on_index_clicked(self, index: QtCore.QModelIndex) -> None:
        if index.isValid():
            self.index_chosen.emit(int(index.row()))

    # noinspection PyPep8Naming
    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self._hide_hover_hint()
        super().hideEvent(event)
        self.closed.emit()

class MultiSelectPopup(QtWidgets.QWidget):
    """Frameless popup list of check items for multi-select fields."""
    closed = QtCore.pyqtSignal()
    selection_changed = QtCore.pyqtSignal()

    def __init__(self, field: "PopupMultiSelectField") -> None:
        super().__init__(
            None,
            QtCore.Qt.WindowType.ToolTip
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint,
        )
        self._field = field
        self._rows: list[_PopupCheckItem] = []
        cfg = ui(self)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*popup_host_root_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        configure_floating_popup_surface(self, self._body)

        body_lay = QtWidgets.QVBoxLayout(self._body)
        setup_layout(body_lay, cfg=cfg, margins=(cfg.space_s, cfg.space_s, cfg.space_s, cfg.space_s), spacing=0)

        self._scroll = QtWidgets.QScrollArea(self._body)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._scroll.viewport().setProperty("role", "comboPopupViewport")
        enable_styled_background(self._scroll.viewport())

        self._content = QtWidgets.QWidget(self._scroll)
        self._content.setProperty("role", "comboPopupViewport")
        enable_styled_background(self._content)

        self._content_lay = QtWidgets.QVBoxLayout(self._content)
        cfg = ui(self)
        inner_gap = _popup_multiselect_inner_gap(cfg)
        self._content_lay.setContentsMargins(inner_gap, 0, inner_gap, 0)
        self._content_lay.setSpacing(inner_gap)
        self._content_lay.addStretch(1)

        self._scroll.setWidget(self._content)
        body_lay.addWidget(self._scroll)
        root.addWidget(self._body)

    def set_items(self, items: list[str], selected: list[str]) -> None:
        prev = self._scroll.verticalScrollBar().value()
        while self._rows:
            row = self._rows.pop()
            self._content_lay.removeWidget(row)
            row.deleteLater()

        selected_set = {str(x) for x in (selected or [])}
        for text in items or []:
            row = _PopupCheckItem(str(text), checked=str(text) in selected_set, parent=self._content)
            row.toggled.connect(self.selection_changed.emit)
            self._content_lay.insertWidget(self._content_lay.count() - 1, row)
            self._rows.append(row)

        QtCore.QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(prev))

    def selected_items(self) -> list[str]:
        return [row.text() for row in self._rows if row.is_checked()]

    def show_for(self, field: "PopupMultiSelectField") -> None:
        self._apply_geometry(field)
        self.show()
        self.raise_()

    def refresh_geometry(self) -> None:
        if self.isVisible():
            self._apply_geometry(self._field)

    def _visible_row_count(self) -> int:
        return max(1, min(len(self._rows), int(_POPUP_MULTISELECT_VISIBLE_ROWS_DEFAULT)))

    def _popup_width(self, field: "PopupMultiSelectField") -> int:
        fm = field.fontMetrics()
        text_w = max((fm.horizontalAdvance(row.text()) for row in self._rows), default=0)
        needs_scroll = len(self._rows) > self._visible_row_count()
        scroll_w = 0
        if needs_scroll:
            scroll_w = self._scroll.style().pixelMetric(
                QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent,
                None,
                self._scroll,
            )
        return max(int(field.width()), int(text_w + scroll_w + int(_POPUP_MULTISELECT_EXTRA_W)))

    def _popup_height(self, field: "PopupMultiSelectField") -> int:
        cfg = ui(field)
        row_h = int(cfg.control_min_h)
        visible_rows = self._visible_row_count()
        content_h = sum(max(int(row_h), int(row.sizeHint().height()), 1) for row in self._rows[:visible_rows])
        if visible_rows > 1:
            content_h += int(self._content_lay.spacing()) * int(visible_rows - 1)
        content_h += _layout_vertical_extra(self._content_lay)
        self._scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if len(self._rows) <= visible_rows
            else QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        body_h = content_h + _layout_vertical_extra(self._body.layout())
        host_h = body_h + _layout_vertical_extra(self.layout())
        return max(int(host_h), int(visible_rows * row_h + _popup_content_extra_h(cfg)))

    def _apply_geometry(self, field: "PopupMultiSelectField") -> None:
        left_margin, top_margin, right_margin, bottom_margin = _layout_contents_margins(self.layout())
        width = self._popup_width(field) + left_margin + right_margin
        height = self._popup_height(field)
        self.setGeometry(
            anchored_popup_geometry(
                field,
                width=width,
                height=height,
                host_margins=(left_margin, top_margin, right_margin, bottom_margin),
            )
        )

    # noinspection PyPep8Naming
    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.closed.emit()

class _PopupHostComboMixin:
    """Shared popup-host lifecycle used by custom combo-like controls."""

    _visual_state_sync_pending: bool
    _popup_binding: PopupHostBinding

    def _owner_widget(self) -> QtWidgets.QWidget:
        if isinstance(self, QtWidgets.QWidget):
            return self
        raise TypeError(f"{type(self).__name__} must also inherit QWidget")

    def _register_popup_host(self) -> None:
        PopupHostRegistry.register(self)

    def _close_other_popups(self) -> None:
        PopupHostRegistry.close_others(self)

    def _popup_widget(self) -> QtWidgets.QWidget | None:
        return None

    def _popup_focus_widget(self) -> QtWidgets.QWidget | None:
        return None

    def _init_popup_host(self) -> None:
        owner = self._owner_widget()
        self._popup_binding = PopupHostBinding(owner)
        self._visual_state_sync_pending = False
        owner.destroyed.connect(lambda *_args: self._dispose_popup_host())
        self._bind_popup_focus_widget()
        self._register_popup_host()

    def _bind_popup_focus_widget(self) -> None:
        focus_widget = self._popup_focus_widget()
        owner = self._owner_widget()
        if focus_widget is not None:
            focus_widget.installEventFilter(owner)

    def _activate_popup_host(self) -> None:
        self._popup_binding.bind_window(self._owner_widget())
        self._popup_binding.install_app_filter()

    def _schedule_visual_state_sync(self) -> None:
        if self._visual_state_sync_pending:
            return

        self._visual_state_sync_pending = True

        def _sync() -> None:
            self._visual_state_sync_pending = False
            self.sync_visual_state()

        QtCore.QTimer.singleShot(0, _sync)

    def hide_popup(self) -> None:  # type: ignore[override]
        hide_popup_widget(self._popup_widget())
        self._popup_binding.cleanup()
        self.sync_visual_state()

    def sync_visual_state(self) -> None:
        popup = self._popup_widget()
        owner = self._owner_widget()
        popup_open = owner.isEnabled() and _widget_visible(popup)
        focus_within = popup_open

        changed = False
        if owner.property("popupOpen") != popup_open:
            owner.setProperty("popupOpen", popup_open)
            changed = True
        if owner.property("focusWithin") != focus_within:
            owner.setProperty("focusWithin", focus_within)
            changed = True
        if changed:
            repolish_widget(owner)

    def _contains_widget(self, widget: QtWidgets.QWidget | None) -> bool:
        popup = self._popup_widget()
        return self._popup_binding.contains_widget(widget, self._owner_widget(), popup)

    # noinspection PyPep8Naming
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        owner = self._owner_widget()
        handled = self._popup_binding.handle_dismiss_event(
            obj=obj,
            event=event,
            popup_widget=self._popup_widget(),
            hide_popup=self.hide_popup,
            contains_widget=self._contains_widget,
            popup_focus_widget=self._popup_focus_widget(),
            on_state_change=self._schedule_visual_state_sync,
        )
        if handled:
            return True
        return QtWidgets.QWidget.eventFilter(owner, obj, event)

    # noinspection PyPep8Naming
    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        QtWidgets.QWidget.showEvent(owner, event)
        self._schedule_visual_state_sync()

    # noinspection PyPep8Naming
    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        self.hide_popup()
        QtWidgets.QWidget.hideEvent(owner, event)
        self._schedule_visual_state_sync()

    # noinspection PyPep8Naming
    def moveEvent(self, event: QtGui.QMoveEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        QtWidgets.QWidget.moveEvent(owner, event)
        self._popup_binding.refresh_geometry(self._popup_widget())

    # noinspection PyPep8Naming
    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        QtWidgets.QWidget.resizeEvent(owner, event)
        self._popup_binding.refresh_geometry(self._popup_widget())

    # noinspection PyPep8Naming
    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        QtWidgets.QWidget.focusInEvent(owner, event)
        self._schedule_visual_state_sync()

    # noinspection PyPep8Naming
    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        QtWidgets.QWidget.focusOutEvent(owner, event)
        self._schedule_visual_state_sync()

    # noinspection PyPep8Naming
    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        owner = self._owner_widget()
        QtWidgets.QWidget.changeEvent(owner, event)
        if event.type() in {QtCore.QEvent.Type.EnabledChange, QtCore.QEvent.Type.ParentChange}:
            if not owner.isEnabled():
                self.hide_popup()
            self._schedule_visual_state_sync()

    def _on_popup_closed(self) -> None:
        owner = self._owner_widget()
        owner.setProperty("popupOpen", False)
        owner.setProperty("focusWithin", False)
        self._popup_binding.cleanup()
        repolish_widget(owner)
        self._schedule_visual_state_sync()

    def _dispose_popup_host(self) -> None:
        popup = self._popup_widget()
        if popup is not None:
            try:
                popup.hide()
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                popup.deleteLater()
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                setattr(self, "_popup", None)
            except (AttributeError, RuntimeError, TypeError):
                pass
        binding = getattr(self, "_popup_binding", None)
        if isinstance(binding, PopupHostBinding):
            binding.cleanup()

    def __del__(self) -> None:
        self._dispose_popup_host()


class PopupComboBox(_PopupHostComboMixin, QtWidgets.QComboBox):
    """Combo box that renders and controls the custom popup list widget."""
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("focusWithin", False)
        self.setProperty("popupOpen", False)
        self._popup: ComboPopup | None = None
        self._init_popup_host()

    def _popup_widget(self) -> ComboPopup | None:
        return _popup_instance(self, ComboPopup)

    def _ensure_popup(self) -> ComboPopup:
        popup = self._popup_widget()
        if popup is None:
            popup = ComboPopup(self)
            popup.index_chosen.connect(self._on_popup_index_chosen)
            popup.closed.connect(self._on_popup_closed)
            self._popup = popup
        return popup

    def _popup_focus_widget(self) -> QtWidgets.QWidget | None:
        return self.lineEdit()

    def setEditable(self, editable: bool) -> None:  # type: ignore[override]
        super().setEditable(editable)
        self._bind_popup_focus_widget()
        self._schedule_visual_state_sync()

    def setLineEdit(self, edit: QtWidgets.QLineEdit) -> None:  # type: ignore[override]
        super().setLineEdit(edit)
        self._bind_popup_focus_widget()
        self._schedule_visual_state_sync()

    def showPopup(self) -> None:  # type: ignore[override]
        if not self.isEnabled() or self.count() <= 0:
            return
        popup = self._ensure_popup()
        self._close_other_popups()
        self._activate_popup_host()
        popup.show_for(self)
        self.sync_visual_state()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        popup = self._popup_widget()
        if _widget_visible(popup):
            if key in {QtCore.Qt.Key.Key_Escape, QtCore.Qt.Key.Key_F4}:
                self.hide_popup()
                event.accept()
                return
            if key in {QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter, QtCore.Qt.Key.Key_Space}:
                row = popup.current_row()
                self._on_popup_index_chosen(row)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_Up:
                popup.move_selection(-1)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_Down:
                popup.move_selection(1)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_PageUp:
                popup.page_selection(-1)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_PageDown:
                popup.page_selection(1)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_Home:
                popup.jump_selection(0)
                event.accept()
                return
            if key == QtCore.Qt.Key.Key_End:
                popup.jump_selection(self.count() - 1)
                event.accept()
                return
        elif key in {QtCore.Qt.Key.Key_F4, QtCore.Qt.Key.Key_Space} or (
            key == QtCore.Qt.Key.Key_Down and bool(event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier)
        ):
            self.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)
        self._schedule_visual_state_sync()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton and not self.isEditable():
            if _widget_visible(self._popup_widget()):
                self.hide_popup()
            else:
                self.showPopup()
            self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)
        self._schedule_visual_state_sync()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        if _widget_visible(self._popup_widget()):
            self.hide_popup()
            event.accept()
            return
        super().wheelEvent(event)

    def _on_popup_index_chosen(self, row: int) -> None:
        row = int(row)
        if 0 <= row < self.count():
            if row != self.currentIndex():
                self.setCurrentIndex(row)
            self.activated[int].emit(row)
            self.textActivated.emit(self.currentText())
        self.hide_popup()

class LanguageCombo(PopupComboBox):
    """Popup combo box preconfigured for normalized language-code options."""
    def __init__(
        self,
        *,
        special_first: SpecialLanguageOptions = None,
        codes_provider: Callable[[], Iterable[str]] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._special_first = special_first
        self._codes_provider = codes_provider
        default_code = LanguagePolicy.DEFAULT_UI
        fallback_value = default_code
        if isinstance(special_first, tuple) and len(special_first) == 2 and isinstance(special_first[0], str):
            fallback_value = special_first[1]
        elif isinstance(special_first, (list, tuple)) and special_first:
            first = special_first[0]
            if isinstance(first, (list, tuple)) and len(first) == 2:
                fallback_value = first[1]
        self._fallback_code = str(fallback_value or default_code).strip().lower() or default_code
        setup_combo(self)
        self.rebuild()

    def rebuild(self) -> None:
        provider = self._codes_provider
        try:
            provider_fn: Callable[[], Iterable[str]] | None = provider if callable(provider) else None
            codes = list(provider_fn()) if provider_fn is not None else []
        except (RuntimeError, TypeError, ValueError):
            codes = []
        items = build_language_options(codes, special_first=self._special_first)
        desired = self.code()
        rebuild_code_combo(self, items, desired_code=desired, fallback_code=self._fallback_code)

    def code(self) -> str:
        return combo_current_code(self, default=self._fallback_code)

class PopupMultiSelectField(_PopupHostComboMixin, QtWidgets.QComboBox):
    """Read-only field that opens a popup for multi-select choices."""
    selection_changed = QtCore.pyqtSignal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "multiSelectField")
        self.setProperty("placeholderVisible", True)
        self.setProperty("selected_items", [])
        setup_combo(self)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)

        self._items: list[str] = []
        self._placeholder = ""
        self._display_text = ""
        self._popup: MultiSelectPopup | None = None
        self._init_popup_host()
        self._sync_display_item("")
        self._apply_selected_items([])

    def _popup_widget(self) -> MultiSelectPopup | None:
        return _popup_instance(self, MultiSelectPopup)

    def _ensure_popup(self) -> MultiSelectPopup:
        popup = self._popup_widget()
        if popup is None:
            popup = MultiSelectPopup(self)
            popup.selection_changed.connect(self._on_popup_selection_changed)
            popup.closed.connect(self._on_popup_closed)
            self._popup = popup
        return popup

    def _sync_display_item(self, text: str) -> None:
        blocked = self.blockSignals(True)
        try:
            if self.count() <= 0:
                self.addItem(str(text or ""))
            else:
                self.setItemText(0, str(text or ""))
                while self.count() > 1:
                    self.removeItem(self.count() - 1)
            if self.currentIndex() != 0:
                self.setCurrentIndex(0)
        finally:
            self.blockSignals(blocked)

    def set_items(self, items: list[str]) -> None:
        self._items = [str(x) for x in (items or [])]
        popup = self._popup_widget()
        if popup is not None:
            popup.set_items(self._items, self.selected_items())
        self._update_display_text()

    def set_placeholder(self, placeholder: str) -> None:
        self._placeholder = str(placeholder or "")
        self._update_display_text()

    def selected_items(self) -> list[str]:
        return [str(x) for x in (self.property("selected_items") or [])]

    def set_selected_items(self, selected: list[str]) -> None:
        self._apply_selected_items(selected)
        popup = self._popup_widget()
        if popup is not None:
            popup.set_items(self._items, self.selected_items())

    def showPopup(self) -> None:  # type: ignore[override]
        if not self.isEnabled() or not self._items:
            return
        popup = self._ensure_popup()
        self._close_other_popups()
        popup.set_items(self._items, self.selected_items())
        self._activate_popup_host()
        popup.show_for(self)
        self.sync_visual_state()

    def _apply_selected_items(self, selected: list[str], *, emit_signal: bool = False) -> None:
        selected_set = {str(x) for x in (selected or [])}
        out = [str(x) for x in self._items if str(x) in selected_set]
        self.setProperty("selected_items", out)
        self.setProperty("placeholderVisible", not bool(out))
        self._display_text = ", ".join(out) if out else self._placeholder
        self._update_display_text()
        repolish_widget(self)
        if emit_signal:
            self.selection_changed.emit(out)

    def _update_display_text(self) -> None:
        text = self._display_text or self._placeholder
        self._sync_display_item(text)
        self.setToolTip("")

    def _toggle_popup(self) -> None:
        if _widget_visible(self._popup_widget()):
            self.hide_popup()
        else:
            self.showPopup()
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)

    def _on_popup_selection_changed(self) -> None:
        popup = self._popup_widget()
        if popup is None:
            return
        self._apply_selected_items(popup.selected_items(), emit_signal=True)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._toggle_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        if _widget_visible(self._popup_widget()):
            self.hide_popup()
            event.accept()
            return
        event.ignore()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        popup_visible = _widget_visible(self._popup_widget())
        if popup_visible and key in {QtCore.Qt.Key.Key_Escape, QtCore.Qt.Key.Key_F4}:
            self.hide_popup()
            event.accept()
            return
        if key in {
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Enter,
            QtCore.Qt.Key.Key_Space,
            QtCore.Qt.Key.Key_F4,
        } or (
            key == QtCore.Qt.Key.Key_Down and bool(event.modifiers() & QtCore.Qt.KeyboardModifier.AltModifier)
        ):
            self._toggle_popup()
            event.accept()
            return
        super().keyPressEvent(event)
