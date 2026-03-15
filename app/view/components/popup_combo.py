# app/view/components/popup_combo.py
from __future__ import annotations

from typing import Any, Iterable, Optional

from PyQt5 import QtCore, QtGui, QtWidgets
try:
    import sip
except Exception:
    sip = None

from app.model.helpers.string_utils import normalize_lang_code
from app.view.components.hint_popup import hint_popup
from app.view.ui_config import apply_floating_shadow, bind_tracked_window, contains_widget_chain, enable_styled_background, floating_shadow_margins, install_app_event_filter, repolish_widget, setup_combo, setup_layout, ui

# ----- Style helpers -----
def _widget_alive(w: Optional[QtWidgets.QWidget]) -> bool:
    if not isinstance(w, QtWidgets.QWidget):
        return False
    if sip is None:
        return True
    try:
        return not bool(sip.isdeleted(w))
    except Exception:
        return False

def _widget_visible(w: Optional[QtWidgets.QWidget]) -> bool:
    return _widget_alive(w) and bool(w.isVisible())

def _sum_list_row_heights(view: QtWidgets.QAbstractItemView, count: int, fallback: int) -> int:
    total = 0
    for row in range(max(0, int(count))):
        hint = 0
        try:
            hint = int(view.sizeHintForRow(row) or 0)
        except Exception:
            hint = 0
        total += max(int(fallback), int(hint), 1)
    return total

def _layout_vertical_extra(layout: Optional[QtWidgets.QLayout]) -> int:
    if layout is None:
        return 0
    margins = layout.contentsMargins()
    return int(margins.top() + margins.bottom())

# ----- Combo code helpers -----
def normalize_combo_code(code: str, *, default: str = "") -> str:
    norm = normalize_lang_code(str(code or "").strip(), drop_region=False)
    if norm:
        return norm
    fallback = normalize_lang_code(default, drop_region=False)
    return fallback or str(default or "").strip().lower()

# ----- Combo data helpers -----
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

    if idx < 0 and combo.count() > 0:
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
    if idx < 0 and combo.count() > 0:
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
    toggled = QtCore.pyqtSignal()

    def __init__(self, text: str, *, checked: bool = False, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        cfg = ui(self)
        self.setProperty("role", "menuCheckItem")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        enable_styled_background(self)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setMinimumHeight(int(cfg.control_min_h))

        lay = QtWidgets.QHBoxLayout(self)
        setup_layout(lay, cfg=cfg, margins=(cfg.combo_text_pad_x, 0, cfg.combo_text_pad_x, 0), spacing=0)

        self.checkbox = QtWidgets.QCheckBox(str(text), self)
        self.checkbox.setProperty("role", "menuCheck")
        self.checkbox.setChecked(bool(checked))
        self.checkbox.setMinimumHeight(int(cfg.control_min_h))
        self.checkbox.setFocusPolicy(QtCore.Qt.NoFocus)
        self.checkbox.installEventFilter(self)
        self.checkbox.toggled.connect(self._on_toggled)
        lay.addWidget(self.checkbox, 1)

        self._set_hovered(False)
        self._sync_state()

    def text(self) -> str:
        return str(self.checkbox.text() or "")

    def is_checked(self) -> bool:
        return bool(self.checkbox.isChecked())

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(bool(checked))

    def _set_hovered(self, hovered: bool) -> None:
        self.setProperty("hovered", bool(hovered))
        repolish_widget(self)

    def _sync_state(self) -> None:
        self.setProperty("checkedState", bool(self.checkbox.isChecked()))
        repolish_widget(self)

    def _on_toggled(self) -> None:
        self._sync_state()
        self.toggled.emit()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.pos()):
            self.checkbox.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        self._set_hovered(False)
        super().leaveEvent(event)

    # ----- Event handling -----

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self.checkbox:
            if event.type() == QtCore.QEvent.Enter:
                self._set_hovered(True)
            elif event.type() == QtCore.QEvent.Leave:
                self._set_hovered(False)
        return super().eventFilter(obj, event)

# ----- Popup widgets -----
class _ComboPopupList(QtWidgets.QListView):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "comboPopupList")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setTextElideMode(QtCore.Qt.ElideRight)
        self.setUniformItemSizes(True)
        self.setItemDelegate(_ComboPopupItemDelegate(self))
        self.setFocusPolicy(QtCore.Qt.NoFocus)

        vp = self.viewport()
        if vp is not None:
            vp.setProperty("role", "comboPopupViewport")
            vp.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            enable_styled_background(vp)
            vp.setMouseTracking(True)

class ComboPopup(QtWidgets.QWidget):
    closed = QtCore.pyqtSignal()
    index_chosen = QtCore.pyqtSignal(int)

    def __init__(self, combo: "PopupComboBox") -> None:
        super().__init__(
            None,
            QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint,
        )
        self._combo = combo
        cfg = ui(self)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setProperty("role", "comboPopupHost")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*floating_shadow_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        self._body.setProperty("role", "comboPopup")
        enable_styled_background(self._body)
        apply_floating_shadow(self._body)

        body_lay = QtWidgets.QVBoxLayout(self._body)
        setup_layout(body_lay, cfg=cfg, margins=(cfg.option_spacing, cfg.option_spacing, cfg.option_spacing, cfg.option_spacing), spacing=0)

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

    @property
    def list_view(self) -> _ComboPopupList:
        return self._list

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
        step = max(1, min(int(self._combo.maxVisibleItems() or 10), max(self._combo.count(), 1)) - 1)
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
                sel.setCurrentIndex(idx, QtCore.QItemSelectionModel.ClearAndSelect)
            self._list.scrollTo(idx, QtWidgets.QAbstractItemView.PositionAtCenter)

    def _apply_geometry(self, combo: "PopupComboBox") -> None:
        screen_pos = combo.mapToGlobal(QtCore.QPoint(0, combo.height()))
        screen = QtWidgets.QApplication.screenAt(screen_pos) or combo.screen() or QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1920, 1080)

        width = self._popup_width(combo)
        height = self._popup_height(combo)
        x = combo.mapToGlobal(QtCore.QPoint(0, 0)).x()
        cfg = ui(combo)
        popup_gap_y = int(cfg.popup_anchor_gap_y)
        y_below = combo.mapToGlobal(QtCore.QPoint(0, combo.height() + popup_gap_y)).y()
        y_above = combo.mapToGlobal(QtCore.QPoint(0, -height - popup_gap_y)).y()

        if y_below + height <= avail.bottom() + 1 or y_above < avail.top():
            y = y_below
        else:
            y = y_above

        edge = int(cfg.popup_edge_margin)
        if x + width > avail.right() - edge:
            x = max(avail.left() + edge, avail.right() - width - edge)
        if x < avail.left() + edge:
            x = avail.left() + edge

        self.setGeometry(x, y, width, height)

    def _visible_row_count(self, combo: "PopupComboBox") -> int:
        return max(1, min(int(combo.maxVisibleItems() or 10), max(combo.count(), 1)))

    def _popup_width(self, combo: "PopupComboBox") -> int:
        fm = combo.fontMetrics()
        text_w = 0
        for row in range(combo.count()):
            text_w = max(text_w, fm.horizontalAdvance(combo.itemText(row)))

        icon_w = combo.iconSize().width() if combo.iconSize().width() > 0 else 0
        needs_scroll = combo.count() > self._visible_row_count(combo)
        scroll_w = 0
        if needs_scroll:
            scroll_w = self._list.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent, None, self._list)
        content_w = text_w + icon_w + scroll_w + 52
        return max(int(combo.width()), int(content_w))

    def _popup_height(self, combo: "PopupComboBox") -> int:
        cfg = ui(combo)
        visible_rows = self._visible_row_count(combo)
        row_h = max(int(cfg.control_min_h), 28)
        content_h = _sum_list_row_heights(self._list, visible_rows, row_h)
        self._list.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff if combo.count() <= visible_rows else QtCore.Qt.ScrollBarAsNeeded
        )
        body_h = content_h + _layout_vertical_extra(self._body.layout())
        host_h = body_h + _layout_vertical_extra(self.layout())
        return max(int(host_h), int(visible_rows * row_h + int(cfg.popup_content_extra_h))) + int(cfg.popup_anchor_gap_y)

    def _tooltip_text_for_index(self, index: QtCore.QModelIndex) -> str:
        if not index.isValid():
            return ""
        return str(index.data(QtCore.Qt.ToolTipRole) or "").strip()

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

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self._list.viewport():
            if event.type() == QtCore.QEvent.MouseMove and isinstance(event, QtGui.QMouseEvent):
                self._update_hover_hint(event.pos())
            elif event.type() in {QtCore.QEvent.Leave, QtCore.QEvent.Hide}:
                self._hide_hover_hint()
        return super().eventFilter(obj, event)

    def _on_index_clicked(self, index: QtCore.QModelIndex) -> None:
        if index.isValid():
            self.index_chosen.emit(int(index.row()))

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self._hide_hover_hint()
        super().hideEvent(event)
        self.closed.emit()

class MultiSelectPopup(QtWidgets.QWidget):
    closed = QtCore.pyqtSignal()
    selection_changed = QtCore.pyqtSignal()

    def __init__(self, field: "PopupMultiSelectField") -> None:
        super().__init__(
            None,
            QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint,
        )
        self._field = field
        self._rows: list[_PopupCheckItem] = []
        cfg = ui(self)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setProperty("role", "comboPopupHost")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*floating_shadow_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        self._body.setProperty("role", "comboPopup")
        enable_styled_background(self._body)
        apply_floating_shadow(self._body)

        body_lay = QtWidgets.QVBoxLayout(self._body)
        setup_layout(body_lay, cfg=cfg, margins=(cfg.option_spacing, cfg.option_spacing, cfg.option_spacing, cfg.option_spacing), spacing=0)

        self._scroll = QtWidgets.QScrollArea(self._body)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._scroll.setFocusPolicy(QtCore.Qt.NoFocus)
        self._scroll.viewport().setProperty("role", "comboPopupViewport")
        self._scroll.viewport().setAttribute(QtCore.Qt.WA_StyledBackground, True)
        enable_styled_background(self._scroll.viewport())

        self._content = QtWidgets.QWidget(self._scroll)
        self._content.setProperty("role", "comboPopupViewport")
        self._content.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        enable_styled_background(self._content)

        self._content_lay = QtWidgets.QVBoxLayout(self._content)
        cfg = ui(self)
        margin_x = int(cfg.popup_multiselect_content_margin_x)
        self._content_lay.setContentsMargins(margin_x, 0, margin_x, 0)
        self._content_lay.setSpacing(int(cfg.popup_multiselect_content_spacing))
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
        return max(1, min(len(self._rows), 8))

    def _popup_width(self, field: "PopupMultiSelectField") -> int:
        fm = field.fontMetrics()
        text_w = max((fm.horizontalAdvance(row.text()) for row in self._rows), default=0)
        needs_scroll = len(self._rows) > self._visible_row_count()
        scroll_w = 0
        if needs_scroll:
            scroll_w = self._scroll.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent, None, self._scroll)
        return max(int(field.width()), int(text_w + scroll_w + 60))

    def _popup_height(self, field: "PopupMultiSelectField") -> int:
        cfg = ui(field)
        row_h = max(int(cfg.control_min_h), 28)
        visible_rows = self._visible_row_count()
        content_h = sum(max(int(row_h), int(row.sizeHint().height()), 1) for row in self._rows[:visible_rows])
        if visible_rows > 1:
            content_h += int(self._content_lay.spacing()) * int(visible_rows - 1)
        content_h += _layout_vertical_extra(self._content_lay)
        self._scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff if len(self._rows) <= visible_rows else QtCore.Qt.ScrollBarAsNeeded
        )
        body_h = content_h + _layout_vertical_extra(self._body.layout())
        host_h = body_h + _layout_vertical_extra(self.layout())
        return max(int(host_h), int(visible_rows * row_h + int(cfg.popup_content_extra_h))) + int(cfg.popup_anchor_gap_y)

    def _apply_geometry(self, field: "PopupMultiSelectField") -> None:
        screen_pos = field.mapToGlobal(QtCore.QPoint(0, field.height()))
        screen = QtWidgets.QApplication.screenAt(screen_pos) or field.screen() or QtWidgets.QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QtCore.QRect(0, 0, 1920, 1080)

        width = self._popup_width(field)
        height = self._popup_height(field)
        x = field.mapToGlobal(QtCore.QPoint(0, 0)).x()
        cfg = ui(field)
        popup_gap_y = int(cfg.popup_anchor_gap_y)
        y_below = field.mapToGlobal(QtCore.QPoint(0, field.height() + popup_gap_y)).y()
        y_above = field.mapToGlobal(QtCore.QPoint(0, -height - popup_gap_y)).y()

        if y_below + height <= avail.bottom() + 1 or y_above < avail.top():
            y = y_below
        else:
            y = y_above

        edge = int(cfg.popup_edge_margin)
        if x + width > avail.right() - edge:
            x = max(avail.left() + edge, avail.right() - width - edge)
        if x < avail.left() + edge:
            x = avail.left() + edge

        self.setGeometry(x, y, width, height)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.closed.emit()


# ----- Popup host mixin -----

class _PopupHostComboMixin:
    _tracked_window: Optional[QtWidgets.QWidget]
    _app_filter_installed: bool

    # ----- Popup plumbing -----

    def _popup_widget(self) -> Optional[QtWidgets.QWidget]:
        return None

    def _popup_focus_widget(self) -> Optional[QtWidgets.QWidget]:
        return None

    def _bind_popup_focus_widget(self) -> None:
        focus_widget = self._popup_focus_widget()
        if focus_widget is not None:
            focus_widget.installEventFilter(self)

    def _install_app_filter(self) -> None:
        self._app_filter_installed = install_app_event_filter(self, installed=self._app_filter_installed)

    def _bind_window(self) -> None:
        self._tracked_window = bind_tracked_window(self, self._tracked_window, self)

    def hidePopup(self) -> None:  # type: ignore[override]
        popup = self._popup_widget()
        if _widget_visible(popup):
            popup.hide()
        else:
            self.sync_visual_state()

    def sync_visual_state(self) -> None:
        popup = self._popup_widget()
        popup_open = self.isEnabled() and _widget_visible(popup)
        focus_within = popup_open

        changed = False
        if self.property("popupOpen") != popup_open:
            self.setProperty("popupOpen", popup_open)
            changed = True
        if self.property("focusWithin") != focus_within:
            self.setProperty("focusWithin", focus_within)
            changed = True
        if changed:
            repolish_widget(self)

    def _contains_widget(self, widget: Optional[QtWidgets.QWidget]) -> bool:
        popup = self._popup_widget()
        return contains_widget_chain(widget, self, popup)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        popup = self._popup_widget()
        if _widget_visible(popup):
            if event.type() in {QtCore.QEvent.ApplicationDeactivate, QtCore.QEvent.WindowDeactivate}:
                self.hidePopup()
            elif event.type() in {QtCore.QEvent.MouseButtonPress, QtCore.QEvent.Wheel} and isinstance(
                event, (QtGui.QMouseEvent, QtGui.QWheelEvent)
            ):
                target = obj if isinstance(obj, QtWidgets.QWidget) else QtWidgets.QApplication.widgetAt(event.globalPos())
                if not self._contains_widget(target):
                    self.hidePopup()

        if obj is self._tracked_window:
            if event.type() in {
                QtCore.QEvent.Move,
                QtCore.QEvent.Resize,
                QtCore.QEvent.Hide,
                QtCore.QEvent.WindowDeactivate,
            }:
                self.hidePopup()
                QtCore.QTimer.singleShot(0, self.sync_visual_state)
        elif obj is self._popup_focus_widget():
            if event.type() in {
                QtCore.QEvent.FocusIn,
                QtCore.QEvent.FocusOut,
                QtCore.QEvent.Hide,
                QtCore.QEvent.EnabledChange,
            }:
                QtCore.QTimer.singleShot(0, self.sync_visual_state)
        return super().eventFilter(obj, event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._bind_window()
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        self.hidePopup()
        super().hideEvent(event)
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def moveEvent(self, event: QtGui.QMoveEvent) -> None:  # type: ignore[override]
        super().moveEvent(event)
        popup = self._popup_widget()
        if popup is not None:
            popup.refresh_geometry()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        popup = self._popup_widget()
        if popup is not None:
            popup.refresh_geometry()

    def focusInEvent(self, event: QtGui.QFocusEvent) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def focusOutEvent(self, event: QtGui.QFocusEvent) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() in {QtCore.QEvent.EnabledChange, QtCore.QEvent.ParentChange}:
            self._bind_window()
            if not self.isEnabled():
                self.hidePopup()
            QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def _on_popup_closed(self) -> None:
        self.setProperty("popupOpen", False)
        self.setProperty("focusWithin", False)
        repolish_widget(self)
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def __del__(self) -> None:
        try:
            if self._tracked_window is not None:
                self._tracked_window.removeEventFilter(self)
        except Exception:
            pass
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None and self._app_filter_installed:
                app.removeEventFilter(self)
        except Exception:
            pass

# ----- Popup combo box -----
class PopupComboBox(_PopupHostComboMixin, QtWidgets.QComboBox):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("focusWithin", False)
        self.setProperty("popupOpen", False)
        self._popup = ComboPopup(self)
        self._popup.index_chosen.connect(self._on_popup_index_chosen)
        self._popup.closed.connect(self._on_popup_closed)
        self._tracked_window: Optional[QtWidgets.QWidget] = None
        self._app_filter_installed = False
        self._bind_popup_focus_widget()
        self._bind_window()
        self._install_app_filter()

    # ----- Popup bridge -----

    def _popup_widget(self) -> Optional[ComboPopup]:
        popup = getattr(self, "_popup", None)
        return popup if isinstance(popup, ComboPopup) and _widget_alive(popup) else None

    def _popup_focus_widget(self) -> Optional[QtWidgets.QWidget]:
        return self.lineEdit()

    def setEditable(self, editable: bool) -> None:  # type: ignore[override]
        super().setEditable(editable)
        self._bind_popup_focus_widget()
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def setLineEdit(self, edit: QtWidgets.QLineEdit) -> None:  # type: ignore[override]
        super().setLineEdit(edit)
        self._bind_popup_focus_widget()
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    # ----- Interaction -----

    def showPopup(self) -> None:  # type: ignore[override]
        popup = self._popup_widget()
        if popup is None or not self.isEnabled() or self.count() <= 0:
            return
        popup.show_for(self)
        self.sync_visual_state()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        popup = self._popup_widget()
        if _widget_visible(popup):
            if key in {QtCore.Qt.Key_Escape, QtCore.Qt.Key_F4}:
                self.hidePopup()
                event.accept()
                return
            if key in {QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space}:
                row = popup.current_row()
                self._on_popup_index_chosen(row)
                event.accept()
                return
            if key == QtCore.Qt.Key_Up:
                popup.move_selection(-1)
                event.accept()
                return
            if key == QtCore.Qt.Key_Down:
                popup.move_selection(1)
                event.accept()
                return
            if key == QtCore.Qt.Key_PageUp:
                popup.page_selection(-1)
                event.accept()
                return
            if key == QtCore.Qt.Key_PageDown:
                popup.page_selection(1)
                event.accept()
                return
            if key == QtCore.Qt.Key_Home:
                popup.jump_selection(0)
                event.accept()
                return
            if key == QtCore.Qt.Key_End:
                popup.jump_selection(self.count() - 1)
                event.accept()
                return
        elif key in {QtCore.Qt.Key_F4, QtCore.Qt.Key_Space} or (
            key == QtCore.Qt.Key_Down and bool(event.modifiers() & QtCore.Qt.AltModifier)
        ):
            self.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton and not self.isEditable():
            if _widget_visible(self._popup_widget()):
                self.hidePopup()
            else:
                self.showPopup()
            self.setFocus(QtCore.Qt.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)
        QtCore.QTimer.singleShot(0, self.sync_visual_state)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        if _widget_visible(self._popup_widget()):
            self.hidePopup()
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
        self.hidePopup()


# ----- Popup multi-select field -----
class PopupMultiSelectField(_PopupHostComboMixin, QtWidgets.QComboBox):
    selection_changed = QtCore.pyqtSignal(list)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "multiSelectField")
        self.setProperty("placeholderVisible", True)
        self.setProperty("selected_items", [])
        setup_combo(self)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)

        self._items: list[str] = []
        self._placeholder = ""
        self._display_text = ""
        self._tracked_window: Optional[QtWidgets.QWidget] = None
        self._app_filter_installed = False
        self._popup = MultiSelectPopup(self)
        self._popup.selection_changed.connect(self._on_popup_selection_changed)
        self._popup.closed.connect(self._on_popup_closed)

        self._bind_window()
        self._install_app_filter()
        self._sync_display_item("")
        self._apply_selected_items([])

    def _popup_widget(self) -> Optional[MultiSelectPopup]:
        popup = getattr(self, "_popup", None)
        return popup if isinstance(popup, MultiSelectPopup) and _widget_alive(popup) else None

    # ----- State helpers -----

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

    def show_popup(self) -> None:
        self.showPopup()

    def hide_popup(self) -> None:
        self.hidePopup()

    def showPopup(self) -> None:  # type: ignore[override]
        popup = self._popup_widget()
        if popup is None or not self.isEnabled() or not self._items:
            return
        popup.set_items(self._items, self.selected_items())
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
            self.hidePopup()
        else:
            self.showPopup()
        self.setFocus(QtCore.Qt.MouseFocusReason)

    def _on_popup_selection_changed(self) -> None:
        popup = self._popup_widget()
        if popup is None:
            return
        self._apply_selected_items(popup.selected_items(), emit_signal=True)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.LeftButton:
            self._toggle_popup()
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # type: ignore[override]
        if _widget_visible(self._popup_widget()):
            self.hidePopup()
            event.accept()
            return
        event.ignore()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        popup_visible = _widget_visible(self._popup_widget())
        if popup_visible and key in {QtCore.Qt.Key_Escape, QtCore.Qt.Key_F4}:
            self.hidePopup()
            event.accept()
            return
        if key in {QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space, QtCore.Qt.Key_F4} or (
            key == QtCore.Qt.Key_Down and bool(event.modifiers() & QtCore.Qt.AltModifier)
        ):
            self._toggle_popup()
            event.accept()
            return
        super().keyPressEvent(event)
