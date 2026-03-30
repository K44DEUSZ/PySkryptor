# app/view/components/source_table.py
from __future__ import annotations

import logging
from typing import Any, Callable, cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.model.core.runtime.localization import tr
from app.model.download.policy import DownloadPolicy
from app.view.components.popup_combo import PopupComboBox, PopupMultiSelectField
from app.view.support.widget_effects import repolish_widget
from app.view.support.widget_setup import (
    build_layout_host,
    connect_qt_signal,
    set_passive_cursor,
    setup_toggle_button,
    setup_button,
    setup_combo,
)
from app.view.ui_config import ui

_LOG = logging.getLogger(__name__)


def _source_row_height(cfg) -> int:
    return max(int(cfg.control_min_h) + int(cfg.space_s) * 2, 40)


def _source_base_col_width(cfg) -> int:
    return max(36, int(cfg.control_min_h) + 8)


def _source_number_col_width(cfg, raw_width: int | None = None) -> int:
    width = int(_source_base_col_width(cfg) + 6 if raw_width is None else raw_width)
    return max(_source_base_col_width(cfg), width)


def _source_header_check_width(cfg, *, indicator_size: int) -> int:
    return max(_source_base_col_width(cfg), int(indicator_size) + int(cfg.pad_x_l) + int(cfg.space_s) + 1)


def _source_cell_margins(cfg) -> tuple[int, int, int, int]:
    margin_x = max(2, int(cfg.space_s) - 1)
    margin_y = max(1, int(cfg.space_s) // 2)
    return margin_x, margin_y, margin_x, margin_y


class SourceTable(QtWidgets.QTableWidget):
    """Table widget with drag-and-drop support used by Files and Downloader panels."""

    paths_dropped = QtCore.pyqtSignal(list)
    delete_pressed = QtCore.pyqtSignal()

    preview_requested = QtCore.pyqtSignal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self._header_layout_state: dict[str, Any] | None = None
        self._header_user_widths: dict[int, int] = {}
        self._header_layout_auto_fit = False
        self._header_layout_applying = False
        self._header_refresh_pending = False
        self._header_layout_reapply_pending = False
        self._header_checkbox_sync_pending = False
        self._header_checkbox_enabled = True
        self._header_checkbox_column: int | None = None
        self._width_mode = "fit"
        self._item_value_tooltips_enabled = True

        header = self.horizontalHeader()
        header.setSectionsMovable(False)
        connect_qt_signal(header.sectionResized, self._on_header_section_resized)
        try:
            connect_qt_signal(header.geometriesChanged, self._update_header_checkbox_geometry)
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Header geometry signal hookup skipped. detail=%s", ex)

        self._header_checkbox = QtWidgets.QCheckBox(header.viewport())
        self._header_checkbox.setObjectName("SourceTableHeaderCheckbox")
        self._header_checkbox.setText("")
        self._header_checkbox.setTristate(True)
        setup_toggle_button(self._header_checkbox)
        self._configure_table_checkbox(self._header_checkbox)
        self._header_checkbox.clicked.connect(self._on_header_checkbox_clicked)
        self._header_checkbox.hide()

        self.itemSelectionChanged.connect(self._sync_embedded_selection_state)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setShowGrid(True)
        self.setGridStyle(QtCore.Qt.PenStyle.SolidLine)
        set_passive_cursor(self)
        set_passive_cursor(self.viewport())
        vheader = self.verticalHeader()
        cfg = ui(self)
        row_h = _source_row_height(cfg)
        vheader.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        vheader.setDefaultSectionSize(row_h)
        vheader.setMinimumSectionSize(row_h)

    def setCellWidget(self, row: int, column: int, widget: QtWidgets.QWidget | None) -> None:  # type: ignore[override]
        super().setCellWidget(row, column, widget)
        if widget is not None:
            self._install_row_select_filter(widget)
        self._sync_embedded_selection_state()
        self._schedule_header_checkbox_sync()
        self._refresh_width_mode(self._header_layout_state)

    def insertRow(self, row: int) -> None:  # type: ignore[override]
        super().insertRow(row)
        self._schedule_header_checkbox_sync()
        self._refresh_width_mode(self._header_layout_state)

    def removeRow(self, row: int) -> None:  # type: ignore[override]
        self._dispose_row_widgets(row)
        super().removeRow(row)
        self._schedule_header_checkbox_sync()
        self._refresh_width_mode(self._header_layout_state)

    def set_item_value_tooltips_enabled(self, enabled: bool) -> None:
        self._item_value_tooltips_enabled = bool(enabled)

    def setRowCount(self, rows: int) -> None:  # type: ignore[override]
        target_rows = max(0, int(rows))
        current_rows = int(self.rowCount())
        if target_rows < current_rows:
            for row in range(current_rows - 1, target_rows - 1, -1):
                self._dispose_row_widgets(row)
        super().setRowCount(target_rows)
        self._schedule_header_checkbox_sync()
        self._refresh_width_mode(self._header_layout_state)

    @staticmethod
    def _dispose_widget_tree(widget: QtWidgets.QWidget | None) -> None:
        if widget is None:
            return
        popup_fields = [
            child
            for child in [widget, *widget.findChildren(QtWidgets.QWidget)]
            if isinstance(child, (PopupComboBox, PopupMultiSelectField))
        ]
        for child in popup_fields:
            dispose = getattr(child, '_dispose_popup_host', None)
            if callable(dispose):
                try:
                    dispose()
                except (AttributeError, RuntimeError, TypeError):
                    continue
        try:
            widget.deleteLater()
        except (AttributeError, RuntimeError, TypeError):
            return

    def _dispose_row_widgets(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        for col in range(self.columnCount()):
            widget = self.cellWidget(row, col)
            if widget is None:
                continue
            self.removeCellWidget(row, col)
            self._dispose_widget_tree(widget)

    def _install_row_select_filter(self, w: QtWidgets.QWidget) -> None:
        w.installEventFilter(self)
        for ch in w.findChildren(QtWidgets.QWidget):
            ch.installEventFilter(self)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if isinstance(obj, QtWidgets.QWidget):
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                row = self._row_for_widget(obj)
                if row >= 0:
                    self.selectRow(int(row))
                    self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            elif event.type() == QtCore.QEvent.Type.FocusIn:
                row = self._row_for_widget(obj)
                if row >= 0:
                    self.selectRow(int(row))
            elif event.type() == QtCore.QEvent.Type.KeyPress:
                ke = event  # type: ignore[assignment]
                if isinstance(ke, QtGui.QKeyEvent) and ke.key() == QtCore.Qt.Key.Key_Delete:
                    self.delete_pressed.emit()
                    ke.accept()
                    return True
        return super().eventFilter(obj, event)

    def _row_for_widget(self, w: QtWidgets.QWidget) -> int:
        try:
            pt = w.mapTo(self.viewport(), QtCore.QPoint(max(0, int(w.width() / 2)), max(0, int(w.height() / 2))))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1
        idx = self.indexAt(pt)
        return int(idx.row()) if idx.isValid() else -1

    @staticmethod
    def _apply_selected_row_state(widget: QtWidgets.QWidget | None, row_selected: bool) -> None:
        if widget is None:
            return
        if bool(widget.property("selectedRow")) == bool(row_selected):
            return
        widget.setProperty("selectedRow", bool(row_selected))
        repolish_widget(widget)

    def _sync_embedded_selection_state(self) -> None:
        selected = set(self.selected_rows())
        for row in range(self.rowCount()):
            row_selected = row in selected
            for col in range(self.columnCount()):
                host = self.cellWidget(row, col)
                if host is None:
                    continue

                multi = host if isinstance(host, PopupMultiSelectField) else host.findChild(PopupMultiSelectField)
                if isinstance(multi, PopupMultiSelectField):
                    self._apply_selected_row_state(host, row_selected)
                    self._apply_selected_row_state(multi, row_selected)
                    continue

                for w in [host, *host.findChildren(QtWidgets.QWidget)]:
                    self._apply_selected_row_state(w, row_selected)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.MouseButton.LeftButton:
            idx = self.indexAt(e.pos())
            if not idx.isValid():
                self.clearSelection()
        super().mousePressEvent(e)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        paths: list[str] = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                paths.append(p)

        out = list(dict.fromkeys(paths))
        if out:
            self.paths_dropped.emit(out)

        e.acceptProposedAction()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() == QtCore.Qt.Key.Key_Delete:
            self.delete_pressed.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_embedded_selection_state()
        self._update_header_checkbox_geometry()
        self._refresh_width_mode(self._header_layout_state)
        if self._header_layout_state and self._header_layout_auto_fit:
            self._schedule_header_layout_reapply()

    def viewportEvent(self, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        handled = super().viewportEvent(event)
        if event.type() == QtCore.QEvent.Type.Paint:
            self._mask_last_visible_gridline()
        return handled

    def _checkbox_indicator_size(self) -> int:
        return max(14, int(ui(self).table_check_indicator_size))

    def _configure_table_checkbox(self, checkbox: QtWidgets.QCheckBox) -> None:
        indicator = self._checkbox_indicator_size()
        checkbox.setProperty("role", "tableIndicator")
        checkbox.setFixedSize(indicator, indicator)

    def _header_checkbox_widget_size(self) -> int:
        return int(self._checkbox_indicator_size())

    def _header_check_width(self) -> int:
        cfg = ui(self)
        return _source_header_check_width(cfg, indicator_size=int(self._header_checkbox_widget_size()))

    def _header_number_width(self, state: dict[str, Any] | None = None) -> int:
        cfg = ui(self)
        raw_width = int((state or {}).get("number_width", _source_number_col_width(cfg)))
        return _source_number_col_width(cfg, raw_width)

    def _target_layout_width(self) -> int:
        return max(0, int(self._viewport_width()))

    def _minimum_columns_width(self, state: dict[str, Any] | None = None) -> int:
        layout_state = state or self._header_layout_state or {}
        if not layout_state:
            return sum(max(0, int(self.columnWidth(col))) for col in self._visible_columns())
        return sum(int(self._column_floor_width(layout_state, col)) for col in self._visible_columns())

    def _is_overflow_width_mode(self, state: dict[str, Any] | None = None) -> bool:
        layout_state = state or self._header_layout_state
        if not layout_state:
            return False
        return int(self._minimum_columns_width(layout_state)) > int(self._target_layout_width())

    def _refresh_width_mode(self, state: dict[str, Any] | None = None) -> None:
        overflow = bool(self._is_overflow_width_mode(state))
        mode = "overflow" if overflow else "fit"
        if mode == self._width_mode:
            return
        self._width_mode = mode
        policy = (
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if overflow
            else QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setHorizontalScrollBarPolicy(policy)
        self.viewport().update()

    def _mask_last_visible_gridline(self) -> None:
        if not self.showGrid():
            return

        visible_columns = self._visible_columns()
        if not visible_columns:
            return

        viewport = self.viewport()
        last_column = int(visible_columns[-1])
        x = min(
            int(viewport.width() - 1),
            int(self.columnViewportPosition(last_column) + self.columnWidth(last_column) - 1),
        )
        if x < 0 or x >= int(viewport.width()):
            return

        painter = QtGui.QPainter(viewport)
        try:
            painter.fillRect(
                QtCore.QRect(int(x), 0, 1, int(viewport.height())),
                viewport.palette().brush(viewport.backgroundRole()),
            )

            if self.rowCount() <= 0:
                return

            delegate = self.itemDelegate()
            first_row = max(0, int(self.rowAt(0)))
            last_row = int(self.rowAt(max(0, int(viewport.height()) - 1)))
            if last_row < 0:
                last_row = int(self.rowCount() - 1)

            for row in range(first_row, last_row + 1):
                index = self.model().index(int(row), last_column)
                if not index.isValid():
                    continue

                rect = self.visualRect(index)
                if not rect.isValid() or rect.height() <= 0:
                    continue

                strip = QtCore.QRect(int(x), int(rect.top()), 1, int(rect.height()))
                option = self.viewOptions()
                if isinstance(delegate, QtWidgets.QStyledItemDelegate):
                    delegate.initStyleOption(option, index)
                option.rect = strip
                self.style().drawPrimitive(
                    QtWidgets.QStyle.PrimitiveElement.PE_PanelItemViewItem,
                    option,
                    painter,
                    self,
                )
        finally:
            painter.end()

    def _visible_columns(self) -> list[int]:
        header = self.horizontalHeader()
        return [
            int(col)
            for col in range(self.columnCount())
            if not header.isSectionHidden(int(col))
        ]

    def _header_fill_target_candidates(self, state: dict[str, Any], *, include_manual: bool) -> list[int]:
        resizable_columns = self._resizable_columns(state)
        manual_set = {int(col) for col in resizable_columns if int(col) in self._header_user_widths}
        visible_columns = set(self._visible_columns())
        candidates: list[int] = []

        def add(col: int | None, *, allow_manual: bool) -> None:
            if col is None:
                return
            candidate_col = int(col)
            if (
                candidate_col < 0
                or candidate_col >= self.columnCount()
                or candidate_col in candidates
                or candidate_col not in visible_columns
            ):
                return
            if (not allow_manual) and candidate_col in manual_set:
                return
            candidates.append(candidate_col)

        fill_column = state.get("fill_column")
        add(fill_column, allow_manual=include_manual)
        for column in reversed(resizable_columns):
            add(int(column), allow_manual=include_manual)
        for column in reversed(self._visible_columns()):
            add(int(column), allow_manual=include_manual)

        if not include_manual:
            add(fill_column, allow_manual=True)
            for column in reversed(resizable_columns):
                add(int(column), allow_manual=True)
            for column in reversed(self._visible_columns()):
                add(int(column), allow_manual=True)

        return candidates

    def _column_floor_width(self, state: dict[str, Any], col: int) -> int:
        column = int(col)
        fixed_widths = {int(k): int(v) for k, v in (state.get("fixed_widths") or {}).items()}
        if column == int(state.get("check_col", 0)):
            return int(self._header_check_width())
        if column == int(state.get("number_col", 1)):
            return int(self._header_number_width(state))
        if column in fixed_widths:
            return int(fixed_widths[column])
        if column in self._resizable_columns(state):
            return int(self._column_min_width(state, column))
        return max(1, int(self.columnWidth(column)))

    def _align_applied_column_widths(self, state: dict[str, Any]) -> None:
        visible_columns = self._visible_columns()
        if not visible_columns or self._is_overflow_width_mode(state):
            return

        delta = int(self._target_layout_width() - sum(int(self.columnWidth(col)) for col in visible_columns))
        if delta == 0:
            return

        resizable_columns = set(self._resizable_columns(state))
        for column in self._header_fill_target_candidates(state, include_manual=False):
            if delta == 0:
                break

            current = int(self.columnWidth(column))
            if current <= 0:
                continue

            if delta > 0:
                desired = int(current + delta)
                if column in resizable_columns:
                    desired = int(self._clamp_resizable_width(state, column, desired))
                extra = max(0, int(desired - current))
                if extra <= 0:
                    continue
                self.setColumnWidth(column, int(current + extra))
                delta -= int(extra)
                continue

            floor = int(self._column_floor_width(state, column))
            desired = max(floor, int(current + delta))
            if column in resizable_columns:
                desired = max(floor, int(self._clamp_resizable_width(state, column, desired)))
            shrink = max(0, int(current - desired))
            if shrink <= 0:
                continue
            self.setColumnWidth(column, int(current - shrink))
            delta += int(shrink)

    def _update_header_checkbox_geometry(self) -> None:
        column = self._header_checkbox_column
        if column is None:
            self._header_checkbox.hide()
            return

        header = self.horizontalHeader()
        section = int(column)
        if section < 0 or section >= self.columnCount() or header.isSectionHidden(section):
            self._header_checkbox.hide()
            return

        width = int(header.sectionSize(section))
        if width <= 0:
            self._header_checkbox.hide()
            return

        x = int(header.sectionViewportPosition(section))
        height = int(header.height())
        box = self._header_checkbox.size()
        left = int(x + max(0, int((width - box.width()) / 2)))
        top = int(max(0, int((height - box.height()) / 2)))
        self._header_checkbox.move(left, top)
        self._header_checkbox.setVisible(True)
        self._header_checkbox.raise_()

    def _on_header_section_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        self._update_header_checkbox_geometry()
        if not self._header_layout_state or self._header_layout_applying:
            return
        if int(old_size) == int(new_size):
            return

        column = int(logical_index)
        if column not in self._resizable_columns(self._header_layout_state):
            return

        self._header_user_widths[column] = max(0, int(new_size))
        self._schedule_header_layout_reapply()

    def _schedule_header_layout_reapply(self) -> None:
        if self._header_layout_reapply_pending:
            return

        self._header_layout_reapply_pending = True

        def _apply() -> None:
            self._header_layout_reapply_pending = False
            if self._header_layout_state and self._header_layout_auto_fit:
                self.reapply_header_layout()

        QtCore.QTimer.singleShot(0, _apply)

    def reset_header_user_widths(self) -> None:
        self._header_user_widths.clear()

    def set_header_checkbox_enabled(self, enabled: bool) -> None:
        self._header_checkbox_enabled = bool(enabled)
        self._update_header_checkbox_state()

    def _set_header_checkbox_column(self, column: int | None) -> None:
        self._header_checkbox_column = int(column) if column is not None else None
        self._update_header_checkbox_geometry()
        self._schedule_header_checkbox_sync()

    def _schedule_header_checkbox_sync(self) -> None:
        if self._header_checkbox_sync_pending:
            return
        self._header_checkbox_sync_pending = True

        def _sync() -> None:
            self._header_checkbox_sync_pending = False
            self._update_header_checkbox_state()

        QtCore.QTimer.singleShot(0, _sync)

    def _update_header_checkbox_state(self) -> None:
        column = self._header_checkbox_column
        if column is None:
            self._header_checkbox.hide()
            return

        checkboxes: list[QtWidgets.QCheckBox] = []
        for row in range(self.rowCount()):
            cb = self.checkbox_at(row, int(column))
            if cb is not None:
                checkboxes.append(cb)

        enabled = bool(self._header_checkbox_enabled and checkboxes and any(cb.isEnabled() for cb in checkboxes))
        if not checkboxes:
            state = QtCore.Qt.CheckState.Unchecked
        else:
            checked = sum(1 for cb in checkboxes if cb.isChecked())
            if checked <= 0:
                state = QtCore.Qt.CheckState.Unchecked
            elif checked >= len(checkboxes):
                state = QtCore.Qt.CheckState.Checked
            else:
                state = QtCore.Qt.CheckState.PartiallyChecked

        _blocker = QtCore.QSignalBlocker(self._header_checkbox)
        self._header_checkbox.setEnabled(enabled)
        self._header_checkbox.setCheckState(state)
        del _blocker
        self._update_header_checkbox_geometry()

    def _on_header_checkbox_clicked(self, checked: bool) -> None:
        if self._header_checkbox_column is None or not self._header_checkbox.isEnabled():
            return
        for row in range(self.rowCount()):
            cb = self.checkbox_at(row, int(self._header_checkbox_column))
            if cb is None or not cb.isEnabled():
                continue
            cb.setChecked(bool(checked))
        self._schedule_header_checkbox_sync()

    @staticmethod
    def _resizable_columns(state: dict[str, Any] | None) -> list[int]:
        if not state:
            return []
        return [int(col) for col in (state.get("resizable_columns") or [])]

    @staticmethod
    def _column_min_width(state: dict[str, Any], col: int) -> int:
        min_widths = {int(k): int(v) for k, v in (state.get("min_widths") or {}).items()}
        return max(72, int(min_widths.get(int(col), 0)))

    @staticmethod
    def _column_max_width(state: dict[str, Any], col: int) -> int | None:
        max_widths = {int(k): int(v) for k, v in (state.get("max_widths") or {}).items()}
        width = int(max_widths.get(int(col), 0))
        return width if width > 0 else None

    def _clamp_resizable_width(self, state: dict[str, Any], col: int, width: int) -> int:
        clamped = max(self._column_min_width(state, int(col)), int(width))
        max_width = self._column_max_width(state, int(col))
        if max_width is not None:
            clamped = min(clamped, int(max_width))
        return int(clamped)

    def _distribute_resizable_widths(
        self,
        *,
        state: dict[str, Any],
        resizable_columns: list[int],
        fixed_total: int,
        preferred_widths: dict[int, int] | None = None,
        stretch_weights: dict[int, int] | None = None,
    ) -> dict[int, int]:
        preferred = {int(k): int(v) for k, v in (preferred_widths or {}).items()}
        weights = {int(k): int(v) for k, v in (stretch_weights or {}).items()}
        widths = {int(col): self._column_min_width(state, int(col)) for col in resizable_columns}
        overflow = bool(self._is_overflow_width_mode(state))

        available = max(0, self._target_layout_width() - int(fixed_total))
        capacity = max(0, available - sum(int(width) for width in widths.values()))

        manual_columns = [int(col) for col in resizable_columns if int(col) in self._header_user_widths]
        manual_set = {int(col) for col in manual_columns}
        for col in manual_columns:
            desired = self._clamp_resizable_width(state, col, int(self._header_user_widths.get(col, widths[col])))
            if overflow:
                widths[col] = int(desired)
                continue
            extra = min(capacity, max(0, desired - int(widths[col])))
            widths[col] = int(widths[col] + extra)
            capacity -= int(extra)

        for col in resizable_columns:
            column = int(col)
            if column in manual_set or column not in preferred:
                continue
            desired = self._clamp_resizable_width(state, column, int(preferred[column]))
            if overflow:
                widths[column] = max(int(widths[column]), int(desired))
                continue
            extra = min(capacity, max(0, desired - int(widths[column])))
            widths[column] = int(widths[column] + extra)
            capacity -= int(extra)

        if overflow:
            return widths

        weighted_columns = [
            int(col)
            for col in resizable_columns
            if int(col) not in manual_set and int(weights.get(int(col), 0)) > 0
        ]
        if capacity > 0 and weighted_columns:
            remaining_capacity = int(capacity)
            remaining_weight = sum(max(1, int(weights.get(int(col), 1))) for col in weighted_columns)
            for idx, col in enumerate(weighted_columns):
                weight = max(1, int(weights.get(int(col), 1)))
                if idx == len(weighted_columns) - 1 or remaining_weight <= 0:
                    extra = remaining_capacity
                else:
                    extra = int((remaining_capacity * weight) / float(remaining_weight))
                widths[int(col)] = int(widths[int(col)] + max(0, int(extra)))
                remaining_capacity = max(0, int(remaining_capacity - extra))
                remaining_weight = max(0, int(remaining_weight - weight))
            capacity = int(remaining_capacity)

        if capacity > 0:
            for fill_target in self._header_fill_target_candidates(state, include_manual=False):
                if int(fill_target) not in widths:
                    continue
                desired = self._clamp_resizable_width(
                    state,
                    int(fill_target),
                    int(widths[int(fill_target)] + int(capacity)),
                )
                extra = max(0, int(desired - widths[int(fill_target)]))
                if extra <= 0:
                    continue
                widths[int(fill_target)] = int(widths[int(fill_target)] + int(extra))
                capacity -= int(extra)
                if capacity <= 0:
                    break

        return widths

    def _apply_fixed_header_columns(
        self,
        *,
        header: QtWidgets.QHeaderView,
        check_col: int,
        number_col: int,
        check_width: int,
        number_width: int,
        fixed_widths: dict[int, int],
    ) -> int:
        fixed_total = int(check_width) + int(number_width)
        header.setSectionResizeMode(int(check_col), QtWidgets.QHeaderView.Fixed)
        self.setColumnWidth(int(check_col), int(check_width))
        header.setSectionResizeMode(int(number_col), QtWidgets.QHeaderView.Fixed)
        self.setColumnWidth(int(number_col), int(number_width))

        for col, width in fixed_widths.items():
            header.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Fixed)
            self.setColumnWidth(int(col), int(width))
            fixed_total += int(width)

        return int(fixed_total)

    def _apply_resizable_header_columns(
        self,
        *,
        state: dict[str, Any],
        header: QtWidgets.QHeaderView,
        resizable_columns: list[int],
        fixed_total: int,
        preferred_widths: dict[int, int] | None = None,
        stretch_weights: dict[int, int] | None = None,
    ) -> None:
        widths = self._distribute_resizable_widths(
            state=state,
            resizable_columns=resizable_columns,
            fixed_total=fixed_total,
            preferred_widths=preferred_widths,
            stretch_weights=stretch_weights,
        )
        for col in resizable_columns:
            self.setColumnWidth(int(col), int(widths.get(int(col), self._column_min_width(state, int(col)))))
            header.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Interactive)
        self._align_applied_column_widths(state)

    def schedule_populated_header_refresh(
        self,
        *,
        is_active: Callable[[], bool],
        reapply: Callable[[], None],
    ) -> None:
        if self._header_refresh_pending:
            return

        try:
            active = bool(is_active())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            active = False
        if not active:
            return

        self._header_refresh_pending = True

        def _refresh() -> None:
            self._header_refresh_pending = False
            try:
                active_now = bool(is_active())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                active_now = False
            if active_now and self.rowCount() > 0:
                reapply()

        QtCore.QTimer.singleShot(0, _refresh)

    def apply_weighted_header_layout(
        self,
        *,
        check_col: int,
        number_col: int,
        weights: dict[int, int],
        min_widths: dict[int, int] | None = None,
        fixed_widths: dict[int, int] | None = None,
        number_width: int | None = None,
        fill_column: int | None = None,
    ) -> None:
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        self._set_header_checkbox_column(check_col)
        cfg = ui(self)

        self._header_layout_auto_fit = True
        self._header_layout_state = {
            "mode": "weighted",
            "check_col": int(check_col),
            "number_col": int(number_col),
            "weights": {int(k): int(v) for k, v in weights.items()},
            "min_widths": {int(k): int(v) for k, v in (min_widths or {}).items()},
            "fixed_widths": {int(k): int(v) for k, v in (fixed_widths or {}).items()},
            "number_width": int(number_width if number_width is not None else _source_number_col_width(cfg)),
            "fill_column": int(fill_column) if fill_column is not None else None,
            "resizable_columns": [
                int(col)
                for col in weights
                if int(col) not in (int(check_col), int(number_col))
                and int(col) not in {int(k) for k in (fixed_widths or {})}
            ],
        }
        self.reapply_header_layout()
        self._schedule_header_layout_reapply()

    def apply_content_header_layout(
        self,
        *,
        check_col: int,
        number_col: int,
        stretch_weights: dict[int, int],
        fit_columns: list[int] | None = None,
        preferred_widths: dict[int, int] | None = None,
        min_widths: dict[int, int] | None = None,
        max_widths: dict[int, int] | None = None,
        fixed_widths: dict[int, int] | None = None,
        number_width: int | None = None,
        fit_padding: int = 18,
        fill_column: int | None = None,
    ) -> None:
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        self._set_header_checkbox_column(check_col)
        cfg = ui(self)

        explicit_preferred = {int(k): int(v) for k, v in (preferred_widths or {}).items()}
        resizable_columns: list[int] = []
        for col in [*(fit_columns or []), *explicit_preferred.keys(), *stretch_weights.keys()]:
            column = int(col)
            if column in resizable_columns:
                continue
            if column in (int(check_col), int(number_col)):
                continue
            if column in {int(k) for k in (fixed_widths or {})}:
                continue
            resizable_columns.append(column)

        self._header_layout_auto_fit = True
        self._header_layout_state = {
            "mode": "content",
            "check_col": int(check_col),
            "number_col": int(number_col),
            "stretch_weights": {int(k): int(v) for k, v in stretch_weights.items()},
            "fit_columns": [int(c) for c in (fit_columns or [])],
            "preferred_widths": explicit_preferred,
            "min_widths": {int(k): int(v) for k, v in (min_widths or {}).items()},
            "max_widths": {int(k): int(v) for k, v in (max_widths or {}).items()},
            "fixed_widths": {int(k): int(v) for k, v in (fixed_widths or {}).items()},
            "number_width": int(number_width if number_width is not None else _source_number_col_width(cfg)),
            "fit_padding": int(fit_padding),
            "fill_column": int(fill_column) if fill_column is not None else None,
            "resizable_columns": resizable_columns,
        }
        self.reapply_header_layout()
        self._schedule_header_layout_reapply()

    def reapply_header_layout(self) -> None:
        state = self._header_layout_state or {}
        if not state:
            return

        mode = str(state.get("mode") or "weighted")
        if mode == "content":
            self._reapply_content_header_layout(state)
            return
        self._reapply_weighted_header_layout(state)

    def _viewport_width(self) -> int:
        header = self.horizontalHeader()
        viewport_w = int(header.viewport().width()) if header is not None else 0
        if viewport_w <= 0:
            viewport_w = int(self.viewport().width())
        if viewport_w <= 0:
            frame = int(self.frameWidth()) * 2
            viewport_w = int(self.width()) - frame
        return max(0, int(viewport_w))

    def minimum_required_width(self) -> int:
        state = self._header_layout_state or {}
        if not state:
            total = sum(max(0, int(self.columnWidth(col))) for col in self._visible_columns())
        else:
            total = int(self._minimum_columns_width(state))
        total += int(self.frameWidth()) * 2
        total += max(0, int(self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent, None, self)))
        return int(total)

    def _reapply_weighted_header_layout(self, state: dict[str, Any]) -> None:
        self._refresh_width_mode(state)
        check_col = int(state.get("check_col", 0))
        number_col = int(state.get("number_col", 1))
        weights = dict(state.get("weights") or {})
        fixed_widths = {int(k): int(v) for k, v in (state.get("fixed_widths") or {}).items()}
        number_width = int(self._header_number_width(state))
        check_width = self._header_check_width()

        header = self.horizontalHeader()
        resizable_columns = self._resizable_columns(state)

        self._header_layout_applying = True
        try:
            fixed_total = self._apply_fixed_header_columns(
                header=header,
                check_col=check_col,
                number_col=number_col,
                check_width=check_width,
                number_width=number_width,
                fixed_widths=fixed_widths,
            )
            self._apply_resizable_header_columns(
                state=state,
                header=header,
                resizable_columns=resizable_columns,
                fixed_total=fixed_total,
                preferred_widths=None,
                stretch_weights={
                    int(col): int(weight)
                    for col, weight in weights.items()
                    if int(col) in resizable_columns
                },
            )
        finally:
            self._header_layout_applying = False
        self._schedule_header_checkbox_sync()

    def _reapply_content_header_layout(self, state: dict[str, Any]) -> None:
        self._refresh_width_mode(state)
        check_col = int(state.get("check_col", 0))
        number_col = int(state.get("number_col", 1))
        stretch_weights = dict(state.get("stretch_weights") or {})
        fit_columns = [int(c) for c in (state.get("fit_columns") or [])]
        explicit_preferred = {int(k): int(v) for k, v in (state.get("preferred_widths") or {}).items()}
        fixed_widths = {int(k): int(v) for k, v in (state.get("fixed_widths") or {}).items()}
        fit_padding = int(state.get("fit_padding", 18))
        number_width = int(self._header_number_width(state))
        check_width = self._header_check_width()

        header = self.horizontalHeader()
        resizable_columns = self._resizable_columns(state)
        preferred_widths = {int(k): int(v) for k, v in explicit_preferred.items()}

        self._header_layout_applying = True
        try:
            fixed_total = self._apply_fixed_header_columns(
                header=header,
                check_col=check_col,
                number_col=number_col,
                check_width=check_width,
                number_width=number_width,
                fixed_widths=fixed_widths,
            )

            for col in fit_columns:
                column = int(col)
                if column not in resizable_columns:
                    continue
                header.setSectionResizeMode(int(column), QtWidgets.QHeaderView.ResizeToContents)
                self.resizeColumnToContents(int(column))
                preferred_width = int(self.columnWidth(int(column))) + int(fit_padding)
                preferred_widths[column] = max(
                    int(preferred_widths.get(column, 0)),
                    int(self._clamp_resizable_width(state, column, int(preferred_width))),
                )

            self._apply_resizable_header_columns(
                state=state,
                header=header,
                resizable_columns=resizable_columns,
                fixed_total=fixed_total,
                preferred_widths=preferred_widths,
                stretch_weights={
                    int(col): int(weight)
                    for col, weight in stretch_weights.items()
                    if int(col) in resizable_columns
                },
            )
        finally:
            self._header_layout_applying = False
        self._schedule_header_checkbox_sync()

    def checkbox_at(self, row: int, col: int) -> QtWidgets.QCheckBox | None:
        w = self.cellWidget(row, col)
        if w is None:
            return None
        cb = w.findChild(QtWidgets.QCheckBox)
        return cb

    def text_at(self, row: int, col: int) -> str:
        it = self.item(row, col)
        return (it.text() or "").strip() if it else ""

    def internal_key_at(self, row: int, col: int) -> str:
        it = self.item(row, col)
        if not it:
            return ""
        v = it.data(QtCore.Qt.ItemDataRole.UserRole)
        if v:
            return str(v).strip()
        return (it.text() or "").strip()

    def row_for_internal_key(self, col: int, key: str) -> int:
        target = str(key or "").strip()
        if not target:
            return -1
        for row in range(self.rowCount()):
            if self.internal_key_at(row, col) == target:
                return int(row)
        return -1

    def selected_internal_key(self, col: int) -> str:
        rows = self.selected_rows()
        if not rows:
            return ""
        return self.internal_key_at(int(rows[0]), col)

    def renumber_rows(self, col: int, *, start: int = 1) -> None:
        column = int(col)
        base = int(start)
        for row in range(self.rowCount()):
            item = self.item(row, column)
            if item is not None:
                item.setText(str(base + row))

    def set_cell_internal_key(self, row: int, col: int, key: str) -> None:
        host = self.cellWidget(row, col)
        if host is None:
            return

        target = str(key or "").strip()
        candidates: list[QtCore.QObject] = [host, *host.findChildren(QtCore.QObject)]
        updated = False

        for candidate in candidates:
            try:
                prop = candidate.property("internal_key")
            except (AttributeError, RuntimeError, TypeError):
                prop = None
            if prop is None:
                continue
            try:
                candidate.setProperty("internal_key", target)
                updated = True
            except (AttributeError, RuntimeError, TypeError):
                continue

        if updated:
            return

        try:
            host.setProperty("internal_key", target)
        except (AttributeError, RuntimeError, TypeError):
            return

    def checked_rows(self, col: int) -> list[int]:
        rows: list[int] = []
        for r in range(self.rowCount()):
            cb = self.checkbox_at(r, col)
            if cb is not None and cb.isChecked():
                rows.append(r)
        return rows

    def selected_rows(self) -> list[int]:
        sm = self.selectionModel()
        if sm is None:
            return []
        rows = sorted({int(i.row()) for i in sm.selectedRows() if i.isValid()})
        if rows:
            return rows
        out: set[int] = set()
        for rg in self.selectedRanges():
            for r in range(rg.topRow(), rg.bottomRow() + 1):
                out.add(int(r))
        return sorted(out)

    def rows_for_removal(self, checkbox_col: int) -> list[int]:
        rows = self.checked_rows(int(checkbox_col))
        return rows if rows else self.selected_rows()

    def control_at(self, row: int, col: int, cls: type[QtWidgets.QWidget]) -> QtWidgets.QWidget | None:
        host = self.cellWidget(row, col)
        if host is None:
            return None
        if isinstance(host, cls):
            return host
        return host.findChild(cls)

    def combo_at(self, row: int, col: int) -> QtWidgets.QComboBox | None:
        w = self.control_at(row, col, QtWidgets.QComboBox)
        return w if isinstance(w, QtWidgets.QComboBox) else None

    def column_widget_width_hint(self, col: int, *, fallback: int = 0, pad: int = 0, cap: int | None = None) -> int:
        column = int(col)
        best = max(0, int(fallback))
        for row in range(self.rowCount()):
            host = self.cellWidget(row, column)
            if host is None:
                continue

            try:
                cast(QtWidgets.QWidget, host).ensurePolished()
            except RuntimeError:
                continue

            widget_host = cast(QtWidgets.QWidget, host)
            hints = [
                int(widget_host.minimumSizeHint().width()),
                int(widget_host.sizeHint().width()),
                int(widget_host.minimumWidth()),
            ]
            layout = host.layout()
            margin_width = 0
            if layout is not None:
                margins = layout.contentsMargins()
                margin_width = int(margins.left() + margins.right())

            child_widgets = cast(
                list[QtWidgets.QWidget],
                host.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly),
            )
            for child in child_widgets:
                try:
                    cast(QtWidgets.QWidget, child).ensurePolished()
                except RuntimeError:
                    continue
                widget_child = cast(QtWidgets.QWidget, child)
                hints.append(int(widget_child.minimumSizeHint().width()) + margin_width)
                hints.append(int(widget_child.sizeHint().width()) + margin_width)
                hints.append(int(widget_child.minimumWidth()) + margin_width)

            width = max([value for value in hints if int(value) > 0], default=0)
            if pad:
                width += int(pad)
            if width > best:
                best = int(width)

        if cap is not None and int(cap) > 0:
            best = min(best, int(cap))
        return int(best)

    def multi_select_field_at(self, row: int, col: int) -> PopupMultiSelectField | None:
        w = self.control_at(row, col, PopupMultiSelectField)
        return w if isinstance(w, PopupMultiSelectField) else None

    def audio_track_id_at(self, row: int, col: int) -> str | None:
        w = self.combo_at(row, col)
        if not isinstance(w, QtWidgets.QComboBox):
            return None
        idx = int(w.currentIndex())
        track_ids = list(w.property("audio_track_ids") or [None])
        if 0 <= idx < len(track_ids):
            v = track_ids[idx]
            return str(v).strip() or None if v else None
        return None

    @staticmethod
    def _make_center_cell_host(
        control: QtWidgets.QWidget,
        *,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> QtWidgets.QWidget:
        host, lay = build_layout_host(
            layout="grid",
            margins=margins,
            hspacing=0,
            vspacing=0,
        )
        set_passive_cursor(host)
        lay.addWidget(control, 0, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        return host

    def _make_stretch_cell_host(
        self,
        control: QtWidgets.QWidget,
        *,
        margins: tuple[int, int, int, int] | None = None,
    ) -> QtWidgets.QWidget:
        cfg = ui(self)
        resolved_margins = margins or _source_cell_margins(cfg)
        host, lay = build_layout_host(
            layout="hbox",
            margins=resolved_margins,
            spacing=0,
        )
        set_passive_cursor(host)
        host.setMinimumWidth(0)
        control.setMinimumWidth(0)
        host.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for child in control.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly):
            try:
                cast(QtWidgets.QWidget, child).setMinimumWidth(0)
            except RuntimeError:
                continue
        lay.addWidget(control, 1)
        lay.setAlignment(control, QtCore.Qt.AlignmentFlag.AlignVCenter)
        return host

    def make_checkbox_cell(self, *, on_changed: Callable[[], None] | None = None) -> QtWidgets.QWidget:
        cb = QtWidgets.QCheckBox()
        cb.setTristate(False)
        cb.setText("")
        setup_toggle_button(cb)
        cb.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self._configure_table_checkbox(cb)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.stateChanged.connect(lambda _v: self._schedule_header_checkbox_sync())
        if on_changed is not None:
            cb.stateChanged.connect(lambda _v: on_changed())
        return self._make_center_cell_host(cb, margins=(0, 0, 0, 0))

    def make_simple_combo(
        self,
        *,
        internal_key: str,
        items: list[str],
        on_changed: Callable[[int], None] | None = None,
        enabled: bool = True,
    ) -> QtWidgets.QWidget:
        cb = PopupComboBox()
        setup_combo(cb)
        cb.setProperty("internal_key", str(internal_key))
        for it in items or []:
            cb.addItem(str(it))
        cb.setEnabled(bool(enabled))
        cb.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        if on_changed is not None:
            cb.currentIndexChanged.connect(on_changed)
        return self._make_stretch_cell_host(cb)

    def make_multi_select_field(
        self,
        *,
        internal_key: str,
        items: list[str],
        selected: list[str] | None = None,
        placeholder: str = "",
        on_changed: Callable[[list[str]], None] | None = None,
    ) -> QtWidgets.QWidget:
        field = PopupMultiSelectField()
        field.setProperty("internal_key", str(internal_key))
        field.set_items([str(it) for it in (items or [])])
        field.set_placeholder(str(placeholder or ""))
        field.set_selected_items([str(it) for it in (selected or [])])
        field.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        if on_changed is not None:
            field.selection_changed.connect(on_changed)
        return self._make_stretch_cell_host(field)

    def make_preview_cell(
        self,
        *,
        internal_key: str,
        tooltip: str,
        enabled: bool = False,
    ) -> QtWidgets.QWidget:
        btn = QtWidgets.QToolButton()
        cfg = ui(self)
        btn_w = max(int(cfg.control_min_h) + 18, 54)
        setup_button(btn, min_h=cfg.control_min_h, min_w=btn_w)
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon))
        btn.setToolTip(str(tooltip or ""))
        btn.setEnabled(bool(enabled))
        btn.setProperty("internal_key", str(internal_key))
        btn.clicked.connect(lambda: self._emit_preview(btn))
        return self._make_center_cell_host(
            btn,
            margins=_source_cell_margins(cfg),
        )

    def _emit_preview(self, btn: QtWidgets.QAbstractButton) -> None:
        key = str(btn.property("internal_key") or "").strip()
        if key:
            self.preview_requested.emit(key)

    def make_audio_track_combo(
        self,
        *,
        internal_key: str,
        default_text: str,
        on_changed: Callable[[int], None] | None = None,
        enabled: bool = False,
    ) -> QtWidgets.QWidget:
        cb = PopupComboBox()
        setup_combo(cb)
        cb.addItem(str(default_text or ""))
        cb.setProperty("audio_track_ids", [None])
        cb.setProperty("has_choices", False)
        cb.setProperty("internal_key", str(internal_key))
        cb.setEnabled(bool(enabled))
        cb.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        if on_changed is not None:
            cb.currentIndexChanged.connect(on_changed)
        return self._make_stretch_cell_host(cb)

    def update_audio_tracks(
        self,
        *,
        row: int,
        col: int,
        meta: dict[str, Any],
        default_text: str,
        preferred_audio_track_id: str | None = None,
        internal_key: str | None = None,
    ) -> list[str | None]:
        w = self.combo_at(row, col)
        if not isinstance(w, QtWidgets.QComboBox):
            return [None]

        raw = meta.get("audio_tracks") or []
        options: list[tuple[str, str]] = []
        seen_track_ids: set[str] = set()
        for t in raw or []:
            if not isinstance(t, dict):
                continue
            track_id = str(t.get("track_id") or "").strip()
            label = str(
                t.get("label")
                or t.get("lang_code")
                or t.get("lang")
                or t.get("language")
                or ""
            ).strip()
            if not track_id or track_id in seen_track_ids:
                continue
            seen_track_ids.add(track_id)
            options.append((track_id, label or track_id))

        prev_idx = int(w.currentIndex())
        prev_track_ids = list(w.property("audio_track_ids") or [None])
        try:
            prev_track_id = prev_track_ids[prev_idx] if 0 <= prev_idx < len(prev_track_ids) else None
        except (IndexError, TypeError):
            prev_track_id = None
        w.blockSignals(True)
        w.clear()
        w.addItem(str(default_text or ""))
        track_ids: list[str | None] = [None]

        for track_id, label in options:
            w.addItem(label)
            track_ids.append(track_id)

        desired = str(preferred_audio_track_id or prev_track_id or "").strip()

        chosen = 0
        if desired:
            try:
                idx = track_ids.index(desired) if desired in track_ids else -1
            except ValueError:
                idx = -1
            if idx >= 0:
                chosen = int(idx)
        elif len(track_ids) == 2:
            chosen = 1

        w.setCurrentIndex(chosen)
        w.blockSignals(False)

        w.setProperty("audio_track_ids", track_ids)
        w.setProperty("has_choices", len(track_ids) > 2)
        if internal_key is not None:
            w.setProperty("internal_key", str(internal_key))
        if len(track_ids) == 2 and options:
            w.setToolTip(str(options[0][1] or ""))
        else:
            w.setToolTip("")
        return track_ids

    def apply_probe_diagnostics_notice(
        self,
        *,
        row: int,
        col: int,
        status_col: int,
        meta: dict[str, Any],
    ) -> None:
        cb = self.combo_at(row, col)
        if not isinstance(cb, QtWidgets.QComboBox):
            return

        it_status = self.item(row, status_col)
        if it_status is None:
            return

        default_notice = tr("status.notice.metadata_incomplete")
        current_tooltip = str(it_status.toolTip() or "").strip()
        current_text = str(it_status.text() or "").strip()
        has_custom_tooltip = bool(current_tooltip) and current_tooltip not in (current_text, default_notice)
        if has_custom_tooltip:
            return

        diagnostics = meta.get("probe_diagnostics") or {}
        warnings = set(diagnostics.get("warnings") or []) if isinstance(diagnostics, dict) else set()
        details = dict(diagnostics.get("details") or {}) if isinstance(diagnostics, dict) else {}
        decision = dict(details.get("extractor_access_decision") or {})
        decision_state = str(decision.get("state") or details.get("extractor_access_state") or "").strip()
        decision_action = str(decision.get("action") or details.get("extractor_action") or "").strip()
        has_diagnostics = (
            bool(warnings or (diagnostics.get("errors") or []) or decision_state)
            if isinstance(diagnostics, dict)
            else False
        )
        has_choices = bool(cb.property("has_choices"))

        if "browser_cookies_unavailable" in warnings:
            notice = tr("status.notice.browser_cookies_unavailable")
        elif "authentication_required" in warnings:
            notice = tr("status.notice.authentication_required")
        elif DownloadPolicy.is_unavailable_extractor_access_state(decision_state):
            notice = tr("status.notice.extended_access_unavailable")
        elif DownloadPolicy.is_limited_extractor_access_decision(
            decision_state,
            decision_action,
        ):
            notice = tr("status.notice.extended_access_limited")
        elif "extended_access_required" in warnings or "extractor_access_limited" in warnings:
            notice = tr("status.notice.extended_access_required")
        elif (
            "media_unavailable" in warnings
            or "no_downloadable_formats" in warnings
            or "no_public_formats" in warnings
        ):
            notice = tr("status.notice.media_unavailable")
        elif "audio_tracks_probe_only" in warnings:
            notice = tr("status.notice.audio_tracks_probe_only")
        else:
            notice = default_notice

        should_notice = has_diagnostics and (
            not has_choices
            or "browser_cookies_unavailable" in warnings
            or "authentication_required" in warnings
            or "extended_access_required" in warnings
            or "extractor_access_limited" in warnings
            or bool(decision_state)
            or "media_unavailable" in warnings
            or "no_downloadable_formats" in warnings
            or "no_public_formats" in warnings
            or "audio_tracks_probe_only" in warnings
        )

        it_status.setToolTip(notice if should_notice else "")
        cb.setToolTip(notice if should_notice else "")

    def set_cell_text(self, row: int, col: int, text: str, tooltip: str | None = None) -> None:
        it = self.item(row, col)
        if it is None:
            it = QtWidgets.QTableWidgetItem()
            it.setFlags(it.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, col, it)
        it.setText(text)
        if tooltip is not None:
            it.setToolTip(tooltip)
        else:
            it.setToolTip(text if self._item_value_tooltips_enabled else "")

    def clear_cell_tooltip(self, row: int, col: int) -> None:
        it = self.item(row, col)
        if it is not None:
            it.setToolTip(it.text() if self._item_value_tooltips_enabled else "")
