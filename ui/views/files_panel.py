# ui/views/files_panel.py
from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Optional

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.utils.text import format_bytes, format_hms, is_url
from ui.i18n.translator import tr
from ui.widgets.file_drop_list import FileDropList


class FilesPanel(QtWidgets.QWidget):
    """UI for the 'File transcription' tab with its own signals."""

    # ---------- Outgoing Signals ----------
    request_details = QtCore.pyqtSignal(list)          # keys: List[str]
    start_requested = QtCore.pyqtSignal(list)          # entries: List[dict]
    cancel_requested = QtCore.pyqtSignal()

    # ---------- Columns ----------
    COL_NAME = 0
    COL_SRC = 1
    COL_PATH = 2
    COL_SIZE = 3
    COL_DUR = 4
    COL_STATUS = 5
    COL_PREVIEW = 6

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # ---------- Layout ----------
        layout = QtWidgets.QVBoxLayout(self)

        # Source row
        src_bar = QtWidgets.QHBoxLayout()
        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setPlaceholderText(tr("files.placeholder"))
        self.btn_src_add = QtWidgets.QPushButton(tr("files.add"))
        src_bar.addWidget(self.src_edit, 1)
        src_bar.addWidget(self.btn_src_add)
        layout.addLayout(src_bar)

        # Operations row
        ops_bar = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton(tr("files.add_files"))
        self.btn_add_folder = QtWidgets.QPushButton(tr("files.add_folder"))
        self.btn_open_output = QtWidgets.QPushButton(tr("files.open_output"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))
        ops_bar.addWidget(self.btn_add_files)
        ops_bar.addWidget(self.btn_add_folder)
        ops_bar.addWidget(self.btn_open_output)
        ops_bar.addStretch(1)
        ops_bar.addWidget(self.btn_remove_selected)
        ops_bar.addWidget(self.btn_clear_list)
        layout.addLayout(ops_bar)

        # Hidden DnD store
        self.file_list = FileDropList()
        self.file_list.setVisible(False)
        layout.addWidget(self.file_list)

        # Details table
        group = QtWidgets.QGroupBox(tr("files.details.title"))
        g_lay = QtWidgets.QVBoxLayout(group)

        self.tbl = QtWidgets.QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels([
            tr("files.details.col.name"),
            tr("files.details.col.source"),
            tr("files.details.col.path"),
            tr("files.details.col.size"),
            tr("files.details.col.duration"),
            tr("files.details.col.status"),
            tr("files.details.col.preview"),
        ])
        header = self.tbl.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        g_lay.addWidget(self.tbl, 2)
        layout.addWidget(group, 2)

        # Controls row
        ctrl = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_start = QtWidgets.QPushButton(tr("ctrl.start"))
        self.btn_cancel = QtWidgets.QPushButton(tr("ctrl.cancel"))
        ctrl.addWidget(self.progress, 1)
        ctrl.addWidget(self.btn_start)
        ctrl.addWidget(self.btn_cancel)
        layout.addLayout(ctrl)

        # Output log
        self.output = QtWidgets.QTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 3)

        # ---------- State ----------
        self._row_by_key: Dict[str, int] = {}
        self._transcript_by_key: Dict[str, str] = {}
        for b in (self.btn_start, self.btn_cancel, self.btn_clear_list, self.btn_remove_selected):
            b.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)

        # ---------- Signals ----------
        self.btn_src_add.clicked.connect(self._on_src_add_clicked)
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_open_output.clicked.connect(self._open_output_folder)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_list)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(lambda: self.cancel_requested.emit())
        self.file_list.pathsDropped.connect(self._on_paths_dropped)

        self._refresh_buttons()

    # ---------- Public Helpers ----------

    def append_log(self, text: str) -> None:
        try:
            self.output.append(text)
        except Exception:
            pass

    def set_model_ready(self, ready: bool) -> None:
        self._refresh_buttons(model_ready=ready)

    def set_progress(self, value: int) -> None:
        self.progress.setValue(int(value))

    @QtCore.pyqtSlot(str, str)
    def set_item_status(self, key: str, status: str) -> None:
        row = self._row_by_key.get(key)
        if row is not None:
            self.tbl.item(row, self.COL_STATUS).setText(status)

    @QtCore.pyqtSlot(str, str)
    def update_item_path(self, old_key: str, new_local_path: str) -> None:
        row = self._row_by_key.get(old_key)
        if row is None:
            return
        self.tbl.item(row, self.COL_SRC).setText("LOCAL")
        self.tbl.item(row, self.COL_PATH).setText(new_local_path)
        self._row_by_key.pop(old_key, None)
        self._row_by_key[new_local_path] = row

    @QtCore.pyqtSlot(str, str)
    def enable_preview_for_key(self, key: str, transcript_path: str) -> None:
        self._transcript_by_key[key] = transcript_path
        row = self._row_by_key.get(key)
        if row is None:
            return
        w = self.tbl.cellWidget(row, self.COL_PREVIEW)
        if isinstance(w, QtWidgets.QToolButton):
            w.setEnabled(True)

    def update_details_rows(self, rows: List[dict]) -> None:
        for r in rows:
            key = str(r.get("path", ""))
            row = self._row_by_key.get(key)
            if row is None:
                continue
            self.tbl.item(row, self.COL_NAME).setText(str(r.get("name", "")))
            self.tbl.item(row, self.COL_SRC).setText(str(r.get("source", "")))
            self.tbl.item(row, self.COL_SIZE).setText(format_bytes(r.get("size")))
            self.tbl.item(row, self.COL_DUR).setText(format_hms(r.get("duration")))
        self._refresh_buttons()

    def refresh_buttons(self, *, transcribing: Optional[bool] = None, model_ready: Optional[bool] = None) -> None:
        self._refresh_buttons(transcribing, model_ready)

    def get_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for r in range(self.tbl.rowCount()):
            path = self.tbl.item(r, self.COL_PATH).text()
            src = self.tbl.item(r, self.COL_SRC).text().lower()
            entries.append({"type": ("url" if is_url(path) or src == "url" else "file"), "value": path})
        return entries

    # ---------- Internal UI Handlers ----------

    def _append_row(self, name: str, src: str, path: str) -> None:
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)

        def set_cell(col: int, text: str) -> None:
            item = QtWidgets.QTableWidgetItem(text)
            if col in (self.COL_SRC, self.COL_SIZE, self.COL_DUR, self.COL_STATUS):
                item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.tbl.setItem(row, col, item)

        set_cell(self.COL_NAME, name)
        set_cell(self.COL_SRC, src)
        set_cell(self.COL_PATH, path)
        set_cell(self.COL_SIZE, "-")
        set_cell(self.COL_DUR, "-")
        set_cell(self.COL_STATUS, "-")

        btn = QtWidgets.QToolButton()
        btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        btn.setEnabled(False)
        btn.clicked.connect(lambda: self._open_transcript_for_row(row))
        self.tbl.setCellWidget(row, self.COL_PREVIEW, btn)

        self._row_by_key[path] = row

    def _on_paths_dropped(self, paths: List[str]) -> None:
        keys: List[str] = []
        for p in paths:
            path = Path(p)
            name = path.stem
            src = "LOCAL"
            self._append_row(name, src, p)
            keys.append(p)
        if keys:
            self.request_details.emit(keys)
            self._refresh_buttons()

    def _on_src_add_clicked(self) -> None:
        text = self.src_edit.text().strip()
        if not text:
            self.append_log(tr("log.add.empty"))
            return
        src = "URL" if is_url(text) else "LOCAL"
        if src == "LOCAL":
            p = Path(text)
            if not p.exists():
                self.append_log(tr("log.add.missing"))
                return
        name = (Path(text).stem if src == "LOCAL" else text)
        self._append_row(name, src, text)
        self.src_edit.clear()
        self.request_details.emit([text])
        self._refresh_buttons()
        self.append_log(tr("log.add.ok", text=text))

    def _on_add_files(self) -> None:
        dlg = QtWidgets.QFileDialog(self, tr("files.add_files"))
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        if dlg.exec_():
            keys: List[str] = []
            for p in dlg.selectedFiles():
                path = str(Path(p))
                self._append_row(Path(p).stem, "LOCAL", path)
                keys.append(path)
            if keys:
                self.request_details.emit(keys)
        self._refresh_buttons()

    def _on_add_folder(self) -> None:
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("files.add_folder"))
        if dir_path:
            self._append_row(Path(dir_path).name, "LOCAL", dir_path)
            self.request_details.emit([dir_path])
        self._refresh_buttons()

    def _open_output_folder(self) -> None:
        try:
            Config.TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.TRANSCRIPTIONS_DIR)))
        except Exception as e:
            self.append_log(tr("log.unexpected", msg=str(e)))

    def _open_transcript_for_row(self, row: int) -> None:
        try:
            key = self.tbl.item(row, self.COL_PATH).text()
            path = self._transcript_by_key.get(key)
            if not path:
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            pass

    def _rows_selected(self) -> List[int]:
        return sorted({idx.row() for idx in self.tbl.selectionModel().selectedRows()})

    def _on_remove_selected(self) -> None:
        rows = self._rows_selected()
        rows.reverse()
        for r in rows:
            key = self.tbl.item(r, self.COL_PATH).text()
            self.tbl.removeRow(r)
            self._row_by_key.pop(key, None)
            self._transcript_by_key.pop(key, None)
        self._refresh_buttons()

    def _on_clear_list(self) -> None:
        self.tbl.setRowCount(0)
        self._row_by_key.clear()
        self._transcript_by_key.clear()
        self._refresh_buttons()

    def _on_start_clicked(self) -> None:
        entries = self.get_entries()
        self.start_requested.emit(entries)

    def _refresh_buttons(self, transcribing: Optional[bool] = None, model_ready: Optional[bool] = None) -> None:
        has_items = self.tbl.rowCount() > 0
        running = bool(transcribing) if transcribing is not None else False
        ready = bool(model_ready) if model_ready is not None else True
        has_sel = bool(self._rows_selected())

        self.btn_start.setEnabled(has_items and ready and not running)
        self.btn_cancel.setEnabled(running)
        self.btn_clear_list.setEnabled(has_items and not running)
        self.btn_remove_selected.setEnabled(has_sel and not running)
