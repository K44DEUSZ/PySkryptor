# app/view/components/source_table.py
from __future__ import annotations

from typing import Any, Callable, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from app.controller.support.localization import tr
from app.view.ui_config import build_layout_host, repolish_widget, setup_button, setup_combo, ui
from app.view.components.popup_combo import PopupComboBox, PopupMultiSelectField
from app.model.helpers.string_utils import normalize_lang_code


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

        header = self.horizontalHeader()
        header.setSectionsMovable(False)
        header.sectionResized.connect(self._on_header_section_resized)
        try:
            header.geometriesChanged.connect(self._update_header_checkbox_geometry)
        except Exception:
            pass

        self._header_checkbox = QtWidgets.QCheckBox(header.viewport())
        self._header_checkbox.setObjectName("SourceTableHeaderCheckbox")
        self._header_checkbox.setText("")
        self._header_checkbox.setTristate(True)
        self._header_checkbox.setFocusPolicy(QtCore.Qt.NoFocus)
        self._header_checkbox.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self._configure_table_checkbox(self._header_checkbox)
        self._header_checkbox.clicked.connect(self._on_header_checkbox_clicked)
        self._header_checkbox.hide()

        self.itemSelectionChanged.connect(self._sync_embedded_selection_state)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setShowGrid(True)
        self.setGridStyle(QtCore.Qt.SolidLine)
        vheader = self.verticalHeader()
        cfg = ui(self)
        row_h = max(int(cfg.control_min_h) + int(cfg.source_table_row_extra_h), int(cfg.source_table_row_min_h))
        vheader.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        vheader.setDefaultSectionSize(row_h)
        vheader.setMinimumSectionSize(row_h)

    # ----- Row lifecycle / embedded widgets -----

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
        super().removeRow(row)
        self._schedule_header_checkbox_sync()
        self._refresh_width_mode(self._header_layout_state)

    def setRowCount(self, rows: int) -> None:  # type: ignore[override]
        super().setRowCount(rows)
        self._schedule_header_checkbox_sync()
        self._refresh_width_mode(self._header_layout_state)

    def _install_row_select_filter(self, w: QtWidgets.QWidget) -> None:
        w.installEventFilter(self)
        for ch in w.findChildren(QtWidgets.QWidget):
            ch.installEventFilter(self)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if isinstance(obj, QtWidgets.QWidget):
            if event.type() == QtCore.QEvent.MouseButtonPress:
                row = self._row_for_widget(obj)
                if row >= 0:
                    self.selectRow(int(row))
                    self.setFocus(QtCore.Qt.MouseFocusReason)
            elif event.type() == QtCore.QEvent.FocusIn:
                row = self._row_for_widget(obj)
                if row >= 0:
                    self.selectRow(int(row))
            elif event.type() == QtCore.QEvent.KeyPress:
                ke = event  # type: ignore[assignment]
                if isinstance(ke, QtGui.QKeyEvent) and ke.key() == QtCore.Qt.Key_Delete:
                    self.delete_pressed.emit()
                    ke.accept()
                    return True
        return super().eventFilter(obj, event)

    def _row_for_widget(self, w: QtWidgets.QWidget) -> int:
        try:
            pt = w.mapTo(self.viewport(), QtCore.QPoint(max(0, int(w.width() / 2)), max(0, int(w.height() / 2))))
        except Exception:
            return -1
        idx = self.indexAt(pt)
        return int(idx.row()) if idx.isValid() else -1

    def _apply_selected_row_state(self, widget: QtWidgets.QWidget | None, row_selected: bool) -> None:
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

    # ----- Selection / drag-and-drop / keyboard -----

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
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
        paths: List[str] = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                paths.append(p)

        out = list(dict.fromkeys(paths))
        if out:
            self.paths_dropped.emit(out)

        e.acceptProposedAction()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() == QtCore.Qt.Key_Delete:
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
        if event.type() == QtCore.QEvent.Paint:
            self._mask_last_visible_gridline()
        return handled

    # ----- Header checkbox / width mode helpers -----

    def _checkbox_indicator_size(self) -> int:
        width = max(14, int(self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth, None, self)))
        height = max(14, int(self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorHeight, None, self)))
        return max(int(width), int(height))

    def _configure_table_checkbox(self, checkbox: QtWidgets.QCheckBox) -> None:
        indicator = self._checkbox_indicator_size()
        checkbox.setStyleSheet(
            "QCheckBox { spacing: 0px; padding: 0px; margin: 0px; }"
            f"QCheckBox::indicator {{ width: {indicator}px; height: {indicator}px; margin: 0px; padding: 0px; }}"
        )
        checkbox.setFixedSize(indicator, indicator)

    def _header_checkbox_widget_size(self) -> int:
        return int(self._checkbox_indicator_size())

    def _header_check_width(self) -> int:
        cfg = ui(self)
        return max(int(cfg.source_table_header_check_min_w), int(self._header_checkbox_widget_size() + int(cfg.source_table_header_check_pad_x)))

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
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded if overflow else QtCore.Qt.ScrollBarAlwaysOff)
        self.viewport().update()

    def _mask_last_visible_gridline(self) -> None:
        if not self.showGrid():
            return

        visible_columns = self._visible_columns()
        if not visible_columns:
            return

        viewport = self.viewport()
        last_column = int(visible_columns[-1])
        x = min(int(viewport.width() - 1), int(self.columnViewportPosition(last_column) + self.columnWidth(last_column) - 1))
        if x < 0 or x >= int(viewport.width()):
            return

        painter = QtGui.QPainter(viewport)
        try:
            painter.fillRect(QtCore.QRect(int(x), 0, 1, int(viewport.height())), viewport.palette().brush(viewport.backgroundRole()))

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
                self.style().drawPrimitive(QtWidgets.QStyle.PE_PanelItemViewItem, option, painter, self)
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
            column = int(col)
            if column < 0 or column >= self.columnCount() or column in candidates or column not in visible_columns:
                return
            if (not allow_manual) and column in manual_set:
                return
            candidates.append(column)

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
            return max(40, int(state.get("number_width", 46)))
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
            state = int(QtCore.Qt.Unchecked)
        else:
            checked = sum(1 for cb in checkboxes if cb.isChecked())
            if checked <= 0:
                state = int(QtCore.Qt.Unchecked)
            elif checked >= len(checkboxes):
                state = int(QtCore.Qt.Checked)
            else:
                state = int(QtCore.Qt.PartiallyChecked)

        blocker = QtCore.QSignalBlocker(self._header_checkbox)
        self._header_checkbox.setEnabled(enabled)
        self._header_checkbox.setCheckState(state)
        del blocker
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

    # ----- Header width distribution -----

    def _resizable_columns(self, state: dict[str, Any] | None) -> list[int]:
        if not state:
            return []
        return [int(col) for col in (state.get("resizable_columns") or [])]

    def _column_min_width(self, state: dict[str, Any], col: int) -> int:
        min_widths = {int(k): int(v) for k, v in (state.get("min_widths") or {}).items()}
        return max(72, int(min_widths.get(int(col), 0)))

    def _column_max_width(self, state: dict[str, Any], col: int) -> int | None:
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
        fill_column: int | None = None,
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
                desired = self._clamp_resizable_width(state, int(fill_target), int(widths[int(fill_target)] + int(capacity)))
                extra = max(0, int(desired - widths[int(fill_target)]))
                if extra <= 0:
                    continue
                widths[int(fill_target)] = int(widths[int(fill_target)] + int(extra))
                capacity -= int(extra)
                if capacity <= 0:
                    break

        return widths

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
        except Exception:
            active = False
        if not active:
            return

        self._header_refresh_pending = True

        def _refresh() -> None:
            self._header_refresh_pending = False
            try:
                active_now = bool(is_active())
            except Exception:
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
        number_width: int = 46,
        fill_column: int | None = None,
    ) -> None:
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        self._set_header_checkbox_column(check_col)

        self._header_layout_auto_fit = True
        self._header_layout_state = {
            "mode": "weighted",
            "check_col": int(check_col),
            "number_col": int(number_col),
            "weights": {int(k): int(v) for k, v in weights.items()},
            "min_widths": {int(k): int(v) for k, v in (min_widths or {}).items()},
            "fixed_widths": {int(k): int(v) for k, v in (fixed_widths or {}).items()},
            "number_width": int(number_width),
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
        number_width: int = 46,
        fit_padding: int = 18,
        fill_column: int | None = None,
    ) -> None:
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        self._set_header_checkbox_column(check_col)

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
            "number_width": int(number_width),
            "fit_padding": int(fit_padding),
            "fill_column": int(fill_column) if fill_column is not None else None,
            "resizable_columns": resizable_columns,
        }
        self.reapply_header_layout()
        self._schedule_header_layout_reapply()

    def reapply_weighted_header_layout(self) -> None:
        self.reapply_header_layout()

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
        total += max(0, int(self.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent, None, self)))
        return int(total)

    def _reapply_weighted_header_layout(self, state: dict[str, Any]) -> None:
        self._refresh_width_mode(state)
        check_col = int(state.get("check_col", 0))
        number_col = int(state.get("number_col", 1))
        weights = dict(state.get("weights") or {})
        fixed_widths = {int(k): int(v) for k, v in (state.get("fixed_widths") or {}).items()}
        number_width = max(40, int(state.get("number_width", 46)))
        check_width = self._header_check_width()

        header = self.horizontalHeader()
        resizable_columns = self._resizable_columns(state)
        fixed = int(check_width) + int(number_width)
        for width in fixed_widths.values():
            fixed += int(width)

        self._header_layout_applying = True
        try:
            header.setSectionResizeMode(int(check_col), QtWidgets.QHeaderView.Fixed)
            self.setColumnWidth(int(check_col), int(check_width))
            header.setSectionResizeMode(int(number_col), QtWidgets.QHeaderView.Fixed)
            self.setColumnWidth(int(number_col), int(number_width))

            for col, width in fixed_widths.items():
                header.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Fixed)
                self.setColumnWidth(int(col), int(width))

            widths = self._distribute_resizable_widths(
                state=state,
                resizable_columns=resizable_columns,
                fixed_total=fixed,
                preferred_widths=None,
                stretch_weights={int(col): int(weight) for col, weight in weights.items() if int(col) in resizable_columns},
                fill_column=state.get("fill_column"),
            )
            for col in resizable_columns:
                self.setColumnWidth(int(col), int(widths.get(int(col), self._column_min_width(state, int(col)))))
                header.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Interactive)
            self._align_applied_column_widths(state)
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
        number_width = max(40, int(state.get("number_width", 46)))
        check_width = self._header_check_width()

        header = self.horizontalHeader()
        resizable_columns = self._resizable_columns(state)
        fixed = int(check_width) + int(number_width)
        preferred_widths = {int(k): int(v) for k, v in explicit_preferred.items()}

        self._header_layout_applying = True
        try:
            header.setSectionResizeMode(int(check_col), QtWidgets.QHeaderView.Fixed)
            self.setColumnWidth(int(check_col), int(check_width))
            header.setSectionResizeMode(int(number_col), QtWidgets.QHeaderView.Fixed)
            self.setColumnWidth(int(number_col), int(number_width))

            for col, width in fixed_widths.items():
                header.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Fixed)
                self.setColumnWidth(int(col), int(width))
                fixed += int(width)

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

            widths = self._distribute_resizable_widths(
                state=state,
                resizable_columns=resizable_columns,
                fixed_total=fixed,
                preferred_widths=preferred_widths,
                stretch_weights={int(col): int(weight) for col, weight in stretch_weights.items() if int(col) in resizable_columns},
                fill_column=state.get("fill_column"),
            )
            for col in resizable_columns:
                self.setColumnWidth(int(col), int(widths.get(int(col), self._column_min_width(state, int(col)))))
                header.setSectionResizeMode(int(col), QtWidgets.QHeaderView.Interactive)
            self._align_applied_column_widths(state)
        finally:
            self._header_layout_applying = False
        self._schedule_header_checkbox_sync()

    # ----- Public cell accessors -----

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
        v = it.data(QtCore.Qt.UserRole)
        if v:
            return str(v).strip()
        return (it.text() or "").strip()

    def checked_rows(self, col: int) -> List[int]:
        rows: List[int] = []
        for r in range(self.rowCount()):
            cb = self.checkbox_at(r, col)
            if cb is not None and cb.isChecked():
                rows.append(r)
        return rows

    def selected_rows(self) -> List[int]:
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

    def rows_for_removal(self, checkbox_col: int) -> List[int]:
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
                host.ensurePolished()
            except Exception:
                pass

            hints = [int(host.minimumSizeHint().width()), int(host.sizeHint().width()), int(host.minimumWidth())]
            layout = host.layout()
            margin_width = 0
            if layout is not None:
                margins = layout.contentsMargins()
                margin_width = int(margins.left() + margins.right())

            for child in host.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly):
                try:
                    child.ensurePolished()
                except Exception:
                    pass
                hints.append(int(child.minimumSizeHint().width()) + margin_width)
                hints.append(int(child.sizeHint().width()) + margin_width)
                hints.append(int(child.minimumWidth()) + margin_width)

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

    def audio_lang_code_at(self, row: int, col: int) -> Optional[str]:
        w = self.combo_at(row, col)
        if not isinstance(w, QtWidgets.QComboBox):
            return None
        idx = int(w.currentIndex())
        lang_codes = list(w.property("lang_codes") or [None])
        if 0 <= idx < len(lang_codes):
            v = lang_codes[idx]
            return normalize_lang_code(v, drop_region=False) if v else None
        return None

    # ----- Cell widget factories -----

    def _make_center_cell_host(
        self,
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
        lay.addWidget(control, 0, 0, QtCore.Qt.AlignCenter)
        return host

    def _make_stretch_cell_host(
        self,
        control: QtWidgets.QWidget,
        *,
        margins: tuple[int, int, int, int] | None = None,
    ) -> QtWidgets.QWidget:
        cfg = ui(self)
        resolved_margins = margins or (
            int(cfg.source_table_cell_margin_x),
            int(cfg.source_table_cell_margin_y),
            int(cfg.source_table_cell_margin_x),
            int(cfg.source_table_cell_margin_y),
        )
        host, lay = build_layout_host(
            layout="hbox",
            margins=resolved_margins,
            spacing=0,
        )
        host.setMinimumWidth(0)
        control.setMinimumWidth(0)
        host.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        for child in control.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly):
            try:
                child.setMinimumWidth(0)
            except Exception:
                pass
        lay.addWidget(control, 1)
        lay.setAlignment(control, QtCore.Qt.AlignVCenter)
        return host

    def make_checkbox_cell(self, *, on_changed: Optional[Callable[[], None]] = None) -> QtWidgets.QWidget:
        cb = QtWidgets.QCheckBox()
        cb.setTristate(False)
        cb.setText("")
        cb.setFocusPolicy(QtCore.Qt.NoFocus)
        cb.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
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
        on_changed: Optional[Callable[[int], None]] = None,
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
        on_changed: Optional[Callable[[list[str]], None]] = None,
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
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DirOpenIcon))
        btn.setToolTip(str(tooltip or ""))
        btn.setEnabled(bool(enabled))
        btn.setProperty("internal_key", str(internal_key))
        btn.clicked.connect(lambda: self._emit_preview(btn))
        return self._make_center_cell_host(
            btn,
            margins=(
                int(cfg.source_table_cell_margin_x),
                int(cfg.source_table_cell_margin_y),
                int(cfg.source_table_cell_margin_x),
                int(cfg.source_table_cell_margin_y),
            ),
        )

    def _emit_preview(self, btn: QtWidgets.QAbstractButton) -> None:
        key = str(btn.property("internal_key") or "").strip()
        if key:
            self.preview_requested.emit(key)

    # ----- Audio track / probe helpers -----

    def make_audio_track_combo(
        self,
        *,
        internal_key: str,
        default_text: str,
        on_changed: Optional[Callable[[int], None]] = None,
        enabled: bool = False,
    ) -> QtWidgets.QWidget:
        cb = PopupComboBox()
        setup_combo(cb)
        cb.addItem(str(default_text or ""))
        cb.setProperty("lang_codes", [None])
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
    ) -> list[str | None]:
        w = self.combo_at(row, col)
        if not isinstance(w, QtWidgets.QComboBox):
            return [None]

        raw = meta.get("audio_tracks") or meta.get("audio_langs") or []
        codes: list[str] = []
        for t in raw or []:
            if not isinstance(t, dict):
                continue
            code = t.get("lang_code") or t.get("lang") or t.get("language")
            norm = normalize_lang_code(code, drop_region=False) or ""
            if norm and norm not in codes:
                codes.append(norm)

        prev_idx = int(w.currentIndex())
        prev_codes = list(w.property("lang_codes") or [None])
        prev_sel = None
        try:
            prev_sel = prev_codes[prev_idx] if 0 <= prev_idx < len(prev_codes) else None
        except Exception:
            prev_sel = None
        prev_base = normalize_lang_code(prev_sel, drop_region=True) if prev_sel else ""
        w.blockSignals(True)
        w.clear()
        w.addItem(str(default_text or ""))
        lang_codes: list[str | None] = [None]

        for c in codes:
            w.addItem(c)
            lang_codes.append(c)

        desired = prev_sel
        if not desired and prev_base:
            desired = prev_base

        chosen = 0
        if desired:
            try:
                idx = lang_codes.index(desired) if desired in lang_codes else -1
            except Exception:
                idx = -1
            if idx < 0 and prev_base:
                for j, c in enumerate(lang_codes):
                    if c and normalize_lang_code(c, drop_region=True) == prev_base:
                        idx = j
                        break
            if idx >= 0:
                chosen = int(idx)

        w.setCurrentIndex(chosen)
        w.blockSignals(False)

        w.setProperty("lang_codes", lang_codes)
        w.setProperty("has_choices", len(lang_codes) > 2)
        w.setToolTip("")
        return lang_codes

    def apply_probe_diag_notice(self, *, row: int, col: int, status_col: int, meta: dict[str, Any]) -> None:
        cb = self.combo_at(row, col)
        if not isinstance(cb, QtWidgets.QComboBox):
            return

        it_status = self.item(row, status_col)
        if it_status is None:
            return

        notice = tr("status.notice.metadata_incomplete")
        current_tooltip = str(it_status.toolTip() or "").strip()
        current_text = str(it_status.text() or "").strip()
        has_custom_tooltip = bool(current_tooltip) and current_tooltip not in (current_text, notice)
        if has_custom_tooltip:
            return

        diag = meta.get("probe_diag") or {}
        has_diag = isinstance(diag, dict) and bool((diag.get("warnings") or []) or (diag.get("errors") or []))
        has_choices = bool(cb.property("has_choices"))
        should_notice = has_diag and not has_choices

        it_status.setToolTip(notice if should_notice else "")
        cb.setToolTip(notice if should_notice else "")

    # ----- Text / tooltip helpers -----

    def set_cell_text(self, row: int, col: int, text: str, tooltip: str | None = None) -> None:
        it = self.item(row, col)
        if it is None:
            it = QtWidgets.QTableWidgetItem()
            it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
            self.setItem(row, col, it)
        it.setText(text)
        it.setToolTip(tooltip if tooltip is not None else text)

    def clear_cell_tooltip(self, row: int, col: int) -> None:
        it = self.item(row, col)
        if it is not None:
            it.setToolTip(it.text())
