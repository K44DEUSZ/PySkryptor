# ui/views/files_panel.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt5 import QtCore, QtGui, QtWidgets

from core.config.app_config import AppConfig as Config
from core.io.text import is_url
from ui.utils.translating import tr
from ui.utils.logging import QtHtmlLogSink
from ui.views.dialogs import ask_cancel, ask_conflict
from ui.workers.metadata_worker import MetadataWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.workers.model_loader_worker import ModelLoadWorker


def _fmt_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "-"
    try:
        sec = float(sec)
    except Exception:
        return "-"
    if sec <= 0:
        return "0.0s"
    if sec < 60:
        return f"{sec:.1f}s"
    m = int(sec // 60)
    s = int(sec % 60)
    if m < 60:
        return f"{m}m {s}s"
    h = int(m // 60)
    m = int(m % 60)
    return f"{h}h {m}m {s}s"


class DropTableWidget(QtWidgets.QTableWidget):
    """QTableWidget with file/folder drag&drop support."""

    pathsDropped = QtCore.pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        paths: List[str] = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                paths.append(p)
        out = [str(p) for p in dict.fromkeys(paths)]
        if out:
            self.pathsDropped.emit(out)
        e.acceptProposedAction()


class FilesPanel(QtWidgets.QWidget):
    """Files tab: sources list + transcription control."""

    COL_CHECK = 0
    COL_NO = 1
    COL_TITLE = 2
    COL_DUR = 3
    COL_SRC = 4
    COL_PATH = 5
    COL_STATUS = 6
    COL_PREVIEW = 7

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None

        self._meta_thread: Optional[QtCore.QThread] = None
        self._meta_worker: Optional[MetadataWorker] = None

        self._model_thread: Optional[QtCore.QThread] = None
        self._model_worker: Optional[ModelLoadWorker] = None

        self._was_cancelled: bool = False
        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        root = QtWidgets.QVBoxLayout(self)

        # Requested: smaller buttons
        base_h = 24

        # --- TOP GRID (input + list operations) -> ABOVE sources table
        top_grid = QtWidgets.QGridLayout()
        top_grid.setHorizontalSpacing(8)
        top_grid.setVerticalSpacing(6)
        for c in range(4):
            top_grid.setColumnStretch(c, 1)

        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setPlaceholderText(tr("files.placeholder"))
        self.src_edit.setMinimumHeight(base_h)

        self.btn_src_add = QtWidgets.QPushButton(tr("files.add"))
        self.btn_open_output = QtWidgets.QPushButton(tr("files.open_output"))
        for b in (self.btn_src_add, self.btn_open_output):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumHeight(base_h)

        # 25/75 ratio: Add vs Open output
        top_btn_box = QtWidgets.QHBoxLayout()
        top_btn_box.setSpacing(6)
        top_btn_box.addWidget(self.btn_src_add, 1)
        top_btn_box.addWidget(self.btn_open_output, 3)

        top_grid.addWidget(self.src_edit, 0, 0, 1, 3)
        top_grid.addLayout(top_btn_box, 0, 3, 1, 1)

        self.btn_add_files = QtWidgets.QPushButton(tr("files.add_files"))
        self.btn_add_folder = QtWidgets.QPushButton(tr("files.add_folder"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))

        for b in (self.btn_add_files, self.btn_add_folder, self.btn_remove_selected, self.btn_clear_list):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumHeight(base_h)

        top_grid.addWidget(self.btn_add_files, 1, 0)
        top_grid.addWidget(self.btn_add_folder, 1, 1)
        top_grid.addWidget(self.btn_remove_selected, 1, 2)
        top_grid.addWidget(self.btn_clear_list, 1, 3)

        root.addLayout(top_grid)

        # --- Sources table group
        details_group = QtWidgets.QGroupBox(tr("files.details.title"))
        details_layout = QtWidgets.QVBoxLayout(details_group)

        self.tbl = DropTableWidget()
        self.tbl.setColumnCount(8)
        self.tbl.setHorizontalHeaderLabels([
            "",
            "#",
            tr("files.details.col.name"),
            tr("files.details.col.duration"),
            tr("files.details.col.source"),
            tr("files.details.col.path"),
            tr("files.details.col.status"),
            tr("files.details.col.preview"),
        ])

        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setCornerButtonEnabled(False)

        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setTextElideMode(QtCore.Qt.ElideMiddle)

        self.tbl.itemSelectionChanged.connect(self._update_buttons)
        self.tbl.cellClicked.connect(self._on_table_cell_clicked)

        self._apply_empty_header_mode()

        details_layout.addWidget(self.tbl, 2)
        root.addWidget(details_group, 2)

        # --- BOTTOM BAR (ONLY progress + start/cancel) -> BELOW sources table
        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumHeight(base_h)

        self.btn_start = QtWidgets.QPushButton(tr("ctrl.start"))
        self.btn_cancel = QtWidgets.QPushButton(tr("ctrl.cancel"))
        for b in (self.btn_start, self.btn_cancel):
            b.setMinimumHeight(base_h)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        right_btn_box = QtWidgets.QHBoxLayout()
        right_btn_box.setSpacing(6)
        right_btn_box.addWidget(self.btn_start)
        right_btn_box.addWidget(self.btn_cancel)

        bottom_bar.addWidget(self.progress, 1)
        bottom_bar.addLayout(right_btn_box, 0)

        root.addLayout(bottom_bar)

        # --- Log
        self.output = QtWidgets.QTextBrowser()
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)
        self.output.anchorClicked.connect(self._on_anchor_clicked)
        root.addWidget(self.output, 3)
        self.log = QtHtmlLogSink(self.output)

        # State
        self.pipe = None
        self._keys: set[str] = set()
        self._row_by_key: Dict[str, int] = {}
        self._transcript_by_key: Dict[str, str] = {}
        self._origin_src_by_key: Dict[str, str] = {}       # internal_key -> "URL"/"LOCAL"
        self._display_path_by_key: Dict[str, str] = {}     # internal_key -> display text shown in table

        # Model auto-load
        self._start_model_load()

        # Signals
        self.btn_src_add.clicked.connect(self._on_add_clicked)
        self.src_edit.returnPressed.connect(self._on_add_clicked)

        self.btn_add_files.clicked.connect(self._on_add_files_clicked)
        self.btn_add_folder.clicked.connect(self._on_add_folder_clicked)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_clicked)
        self.btn_open_output.clicked.connect(self._open_output_folder)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        self.tbl.pathsDropped.connect(self._on_paths_dropped)
        self.tbl.cellDoubleClicked.connect(lambda row, _col: self._open_transcript_for_row(row))

        self._update_buttons()

    # ---- header modes ----

    def _apply_empty_header_mode(self) -> None:
        header = self.tbl.horizontalHeader()

        check_w = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth) + 16
        header.setSectionResizeMode(self.COL_CHECK, QtWidgets.QHeaderView.Fixed)
        self.tbl.setColumnWidth(self.COL_CHECK, max(26, check_w))

        header.setSectionResizeMode(self.COL_NO, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PREVIEW, QtWidgets.QHeaderView.ResizeToContents)

        for c in (self.COL_TITLE, self.COL_DUR, self.COL_SRC, self.COL_PATH, self.COL_STATUS):
            header.setSectionResizeMode(c, QtWidgets.QHeaderView.Stretch)

    def _apply_populated_header_mode(self) -> None:
        header = self.tbl.horizontalHeader()

        check_w = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth) + 16
        header.setSectionResizeMode(self.COL_CHECK, QtWidgets.QHeaderView.Fixed)
        self.tbl.setColumnWidth(self.COL_CHECK, max(26, check_w))

        header.setSectionResizeMode(self.COL_NO, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_TITLE, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_DUR, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_SRC, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_STATUS, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PREVIEW, QtWidgets.QHeaderView.ResizeToContents)

    # ---- checkbox widget helpers ----

    def _checkbox_at_row(self, row: int) -> Optional[QtWidgets.QCheckBox]:
        w = self.tbl.cellWidget(row, self.COL_CHECK)
        if not w:
            return None
        return w.findChild(QtWidgets.QCheckBox)

    def _make_checkbox_cell(self) -> QtWidgets.QWidget:
        host = QtWidgets.QWidget()
        host.setContentsMargins(0, 0, 0, 0)
        lay = QtWidgets.QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(QtCore.Qt.AlignCenter)
        cb = QtWidgets.QCheckBox()
        cb.setTristate(False)
        lay.addWidget(cb)
        cb.stateChanged.connect(lambda _v: self._update_buttons())
        return host

    # ---- keys / mapping helpers ----

    def _internal_key_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_PATH)
        if not it:
            return ""
        v = it.data(QtCore.Qt.UserRole)
        if v:
            return str(v).strip()
        return (it.text() or "").strip()

    def _display_path_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_PATH)
        return (it.text() or "").strip() if it else ""

    def _src_label_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_SRC)
        return (it.text() or "").strip() if it else ""

    def _reset_url_rows_to_original_keys(self) -> None:
        """
        Ensures that URL-origin items can be started again from scratch.
        After download we may temporarily map URL -> tmp local file.
        Here we revert internal keys back to URL before a new run.
        """
        for r in range(self.tbl.rowCount()):
            if self._src_label_at_row(r) != "URL":
                continue

            it_path = self.tbl.item(r, self.COL_PATH)
            if not it_path:
                continue

            display_url = (it_path.text() or "").strip()
            if not display_url:
                continue

            current_internal = self._internal_key_at_row(r)
            if current_internal == display_url:
                # already clean
                it_path.setData(QtCore.Qt.UserRole, display_url)
                self._display_path_by_key[display_url] = display_url
                self._origin_src_by_key[display_url] = "URL"
                self._row_by_key[display_url] = r
                continue

            # Remap all internal structures from tmp/local key -> original URL
            old_key = current_internal
            new_key = display_url

            if old_key in self._keys:
                self._keys.discard(old_key)
            self._keys.add(new_key)

            old_row = self._row_by_key.pop(old_key, None)
            self._row_by_key[new_key] = old_row if old_row is not None else r

            if old_key in self._origin_src_by_key:
                self._origin_src_by_key.pop(old_key, None)
            self._origin_src_by_key[new_key] = "URL"

            if old_key in self._display_path_by_key:
                self._display_path_by_key.pop(old_key, None)
            self._display_path_by_key[new_key] = new_key

            self._transcript_by_key.pop(old_key, None)

            # Update internal key stored in the table item
            it_path.setData(QtCore.Qt.UserRole, new_key)
            it_path.setToolTip(new_key)
            it_path.setText(new_key)

    # ---- progress helper ----

    @QtCore.pyqtSlot(int)
    def _on_global_progress(self, value: int) -> None:
        if self._was_cancelled:
            return
        self.progress.setValue(int(value))

    # ---- table interactions ----

    @QtCore.pyqtSlot(int, int)
    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if row < 0:
            return

        mods = QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.ControlModifier):
            return

        if col not in (self.COL_SRC, self.COL_PATH):
            return

        target = self._display_path_at_row(row)
        if not target:
            return

        try:
            if is_url(target):
                if "://" not in target:
                    target = "https://" + target
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(target))
                return

            p = Path(target)
            if not p.exists():
                return

            if os.name == "nt":
                import subprocess
                if p.is_file():
                    subprocess.Popen(["explorer", "/select,", str(p)])
                else:
                    os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(str(p.parent if p.is_file() else p))
                )
        except Exception:
            pass

    # ---- entries ----

    def get_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for r in range(self.tbl.rowCount()):
            key = self._internal_key_at_row(r)
            if not key:
                continue
            if is_url(key):
                entries.append({"type": "url", "value": key})
            else:
                entries.append({"type": "file", "value": key})
        return entries

    # ---- list management ----

    def _normalize_key(self, raw: str) -> str:
        return (raw or "").strip()

    def _try_add_key(self, key: str) -> bool:
        key = self._normalize_key(key)
        if not key:
            return False
        if key in self._keys:
            self.log.warn(f"{tr('status.skipped')} - already in list")
            return False
        self._keys.add(key)
        return True

    def _insert_placeholder_row(self, key: str, *, src_label: str) -> None:
        if self.tbl.rowCount() == 0:
            self._apply_populated_header_mode()

        row = self.tbl.rowCount()
        self.tbl.insertRow(row)

        self.tbl.setCellWidget(row, self.COL_CHECK, self._make_checkbox_cell())

        it_no = QtWidgets.QTableWidgetItem(str(row + 1))
        it_no.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_NO, it_no)

        it_title = QtWidgets.QTableWidgetItem("...")
        self.tbl.setItem(row, self.COL_TITLE, it_title)

        it_dur = QtWidgets.QTableWidgetItem("-")
        it_dur.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_DUR, it_dur)

        it_src = QtWidgets.QTableWidgetItem(src_label)
        it_src.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_SRC, it_src)
        it_src.setToolTip(src_label)

        it_path = QtWidgets.QTableWidgetItem(key)
        it_path.setToolTip(key)
        it_path.setData(QtCore.Qt.UserRole, key)  # internal key
        self.tbl.setItem(row, self.COL_PATH, it_path)

        it_status = QtWidgets.QTableWidgetItem("-")
        it_status.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_STATUS, it_status)

        btn = QtWidgets.QToolButton()
        btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        btn.setEnabled(False)
        self.tbl.setCellWidget(row, self.COL_PREVIEW, btn)

        self._row_by_key[key] = row
        self._origin_src_by_key[key] = src_label
        self._display_path_by_key[key] = key

    def _update_row_from_meta(self, row: int, meta: Dict[str, Any]) -> None:
        if row < 0 or row >= self.tbl.rowCount():
            return

        title = str(meta.get("name") or meta.get("title") or "-")
        duration = meta.get("duration")

        self.tbl.item(row, self.COL_TITLE).setText(title)
        self.tbl.item(row, self.COL_DUR).setText(_fmt_seconds(duration))

    def _start_metadata_for(self, keys: List[str]) -> None:
        if not keys:
            return
        if self._meta_thread is not None:
            try:
                self._meta_thread.requestInterruption()
            except Exception:
                pass

        entries = []
        for k in keys:
            entries.append({"type": ("url" if is_url(k) else "file"), "value": k})

        self._meta_thread = QtCore.QThread(self)
        self._meta_worker = MetadataWorker(entries)
        self._meta_worker.moveToThread(self._meta_thread)

        self._meta_thread.started.connect(self._meta_worker.run)
        self._meta_worker.progress_log.connect(self.log.plain)
        self._meta_worker.table_ready.connect(self._on_meta_rows_ready)

        self._meta_worker.finished.connect(self._meta_thread.quit)
        self._meta_worker.finished.connect(self._meta_worker.deleteLater)
        self._meta_thread.finished.connect(self._meta_thread.deleteLater)
        self._meta_thread.finished.connect(self._on_meta_finished)

        self._meta_thread.start()

    @QtCore.pyqtSlot(list)
    def _on_meta_rows_ready(self, batch: List[Dict[str, Any]]) -> None:
        for meta in batch:
            key = str(meta.get("path") or "").strip()
            if not key:
                continue
            row = self._row_by_key.get(key)
            if row is None:
                continue
            self._update_row_from_meta(row, meta)

    def _on_meta_finished(self) -> None:
        self._meta_thread = None
        self._meta_worker = None

    # ---- actions ----

    def _on_add_clicked(self) -> None:
        key = self._normalize_key(self.src_edit.text())
        if not key:
            return

        if is_url(key):
            if not self._try_add_key(key):
                return
            self._insert_placeholder_row(key, src_label="URL")
            self._start_metadata_for([key])
        else:
            p = Path(key)
            if not p.exists() or not p.is_file():
                self.log.warn(tr("log.add.missing"))
                return
            key = str(p)
            if not self._try_add_key(key):
                return
            self._insert_placeholder_row(key, src_label="LOCAL")
            self._start_metadata_for([key])

        self.src_edit.clear()
        self._refresh_order_numbers()
        self._update_buttons()

    def _on_paths_dropped(self, paths: List[str]) -> None:
        added: List[str] = []
        for p in paths:
            p = self._normalize_key(p)
            if not p:
                continue
            pp = Path(p)
            if not pp.exists() or not pp.is_file():
                continue
            key = str(pp)
            if not self._try_add_key(key):
                continue
            self._insert_placeholder_row(key, src_label="LOCAL")
            added.append(key)

        if added:
            self._start_metadata_for(added[:30])
        self._refresh_order_numbers()
        self._update_buttons()

    def _checked_rows(self) -> List[int]:
        rows: List[int] = []
        for r in range(self.tbl.rowCount()):
            cb = self._checkbox_at_row(r)
            if cb and cb.isChecked():
                rows.append(r)
        return rows

    def _selected_rows(self) -> List[int]:
        rows: List[int] = []
        sel = self.tbl.selectionModel()
        if not sel:
            return rows
        for idx in sel.selectedRows():
            rows.append(idx.row())
        return rows

    def _remove_rows(self, rows: List[int]) -> None:
        if not rows:
            return
        for r in sorted(set(rows), reverse=True):
            key = self._internal_key_at_row(r)
            if key:
                self._keys.discard(key)
                self._row_by_key.pop(key, None)
                self._transcript_by_key.pop(key, None)
                self._origin_src_by_key.pop(key, None)
                self._display_path_by_key.pop(key, None)
            self.tbl.removeRow(r)

        if self.tbl.rowCount() == 0:
            self._apply_empty_header_mode()

        self._refresh_order_numbers()
        self._update_buttons()

    def _refresh_order_numbers(self) -> None:
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, self.COL_NO)
            if it:
                it.setText(str(r + 1))

    def _open_transcript_for_row(self, row: int) -> None:
        key = self._internal_key_at_row(row)
        if not key:
            return
        path = self._transcript_by_key.get(key)
        if not path:
            return
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(path))))
        except Exception:
            pass

    def _on_add_files_clicked(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            tr("files.add_files"),
            "",
            tr("files.details.filters.audio_video"),
        )
        if not files:
            return
        self._on_paths_dropped(files)

    def _on_add_folder_clicked(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, tr("files.add_folder"))
        if not folder:
            return
        p = Path(folder)
        if not p.exists() or not p.is_dir():
            return

        exts = {e.lower() for e in Config.SUPPORTED_MEDIA_EXTS}
        files: List[str] = []
        for fp in p.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in exts:
                files.append(str(fp))
        self._on_paths_dropped(files)

    def _on_remove_selected(self) -> None:
        rows = self._checked_rows() or self._selected_rows()
        self._remove_rows(rows)

    def _on_clear_clicked(self) -> None:
        self._keys.clear()
        self._row_by_key.clear()
        self._transcript_by_key.clear()
        self._origin_src_by_key.clear()
        self._display_path_by_key.clear()
        self.tbl.setRowCount(0)
        self.progress.setValue(0)
        self._apply_empty_header_mode()
        self._update_buttons()

    def _open_output_folder(self) -> None:
        try:
            out_dir = Config.TRANSCRIPTIONS_DIR
            out_dir.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(out_dir))  # type: ignore[attr-defined]
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out_dir)))
        except Exception as e:
            self.log.err(tr("log.unexpected", msg=str(e)))

    # ---- transcription ----

    def _on_start_clicked(self) -> None:
        if not self.pipe:
            self.log.info(tr("log.pipe_not_ready"))
            return

        # IMPORTANT: if some URL items were mapped to TMP paths previously, revert them now
        self._reset_url_rows_to_original_keys()

        entries = self.get_entries()
        if not entries:
            self.log.info(tr("log.no_items"))
            return

        self.log.clear()
        self.progress.setValue(0)

        self._was_cancelled = False
        self._conflict_apply_all_action = None
        self._conflict_apply_all_new_base = None
        self.log.info(tr("log.start"))

        self._transcribe_thread = QtCore.QThread(self)
        self._transcribe_worker = TranscriptionWorker(pipe=self.pipe, entries=entries)
        self._transcribe_worker.moveToThread(self._transcribe_thread)
        self._transcribe_thread.started.connect(self._transcribe_worker.run)

        self._transcribe_worker.log.connect(self.log.plain)
        self._transcribe_worker.progress.connect(self._on_global_progress)
        self._transcribe_worker.item_status.connect(self._on_item_status)
        self._transcribe_worker.item_progress.connect(self._on_item_progress)
        self._transcribe_worker.item_path_update.connect(self._on_item_path_update)
        self._transcribe_worker.transcript_ready.connect(self._on_transcript_ready)
        self._transcribe_worker.conflict_check.connect(self._on_conflict_check)

        self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
        self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
        self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)
        self._transcribe_thread.finished.connect(self._on_transcribe_finished)

        self._update_buttons()
        self._transcribe_thread.start()

    def _on_cancel_clicked(self) -> None:
        if not self._transcribe_worker:
            return
        if not ask_cancel(self):
            return

        self._was_cancelled = True
        self.progress.setValue(0)

        try:
            if self._transcribe_thread is not None:
                self._transcribe_thread.requestInterruption()
            self._transcribe_worker.cancel()
        except Exception:
            pass

        # Reset visible statuses immediately
        for r in range(self.tbl.rowCount()):
            preview = self.tbl.cellWidget(r, self.COL_PREVIEW)
            finished = isinstance(preview, QtWidgets.QToolButton) and preview.isEnabled()
            if not finished:
                it = self.tbl.item(r, self.COL_STATUS)
                if it:
                    it.setText("-")

        # IMPORTANT: revert URL->TMP remaps so the next Start downloads again
        self._reset_url_rows_to_original_keys()

        self.log.warn(tr("log.cancelled"))

    def _on_transcribe_finished(self) -> None:
        if self._was_cancelled:
            self.progress.setValue(0)
        self._transcribe_thread = None
        self._transcribe_worker = None
        self._update_buttons()

    @QtCore.pyqtSlot(str, str)
    def _on_item_status(self, key: str, status: str) -> None:
        if self._was_cancelled:
            return
        row = self._row_by_key.get(key)
        if row is None:
            return
        it = self.tbl.item(row, self.COL_STATUS)
        if it:
            it.setText(status)

    @QtCore.pyqtSlot(str, int)
    def _on_item_progress(self, key: str, pct: int) -> None:
        if self._was_cancelled:
            return
        row = self._row_by_key.get(key)
        if row is None:
            return
        it = self.tbl.item(row, self.COL_STATUS)
        if not it:
            return
        it.setText(tr("status.proc"))

    @QtCore.pyqtSlot(str, str)
    def _on_item_path_update(self, old_key: str, new_key: str) -> None:
        """
        URL download may replace internal key/path to a local file.
        UI should keep displaying original URL.
        """
        row = self._row_by_key.pop(old_key, None)
        if row is None:
            return

        # Keep original URL for display
        display = self._display_path_by_key.get(old_key, old_key)

        src_label = self._origin_src_by_key.get(old_key)

        self._keys.discard(old_key)
        self._keys.add(new_key)
        self._row_by_key[new_key] = row

        if src_label:
            self._origin_src_by_key[new_key] = src_label
        self._origin_src_by_key.pop(old_key, None)

        self._display_path_by_key[new_key] = display
        self._display_path_by_key.pop(old_key, None)

        it_path = self.tbl.item(row, self.COL_PATH)
        if it_path:
            it_path.setData(QtCore.Qt.UserRole, new_key)
            it_path.setText(display)
            it_path.setToolTip(display)

        self._start_metadata_for([new_key])

    @QtCore.pyqtSlot(str, str)
    def _on_transcript_ready(self, key: str, transcript_path: str) -> None:
        self._transcript_by_key[key] = transcript_path
        row = self._row_by_key.get(key)
        if row is None:
            return
        w = self.tbl.cellWidget(row, self.COL_PREVIEW)
        if isinstance(w, QtWidgets.QToolButton):
            w.setEnabled(True)
        self.log.line_with_link(
            tr("log.transcript.saved_prefix"),
            Path(transcript_path),
            title=Path(transcript_path).name,
        )

    @QtCore.pyqtSlot(str, str)
    def _on_conflict_check(self, stem: str, existing_dir: str) -> None:
        if self._was_cancelled:
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")
            return

        try:
            if self._conflict_apply_all_action:
                action = self._conflict_apply_all_action
                new_stem = self._conflict_apply_all_new_base or stem if action == "new" else ""
                if self._transcribe_worker is not None:
                    self._transcribe_worker.on_conflict_decided(action, new_stem)
                return

            action, new_stem, apply_all = ask_conflict(self, stem)
            if apply_all and action != "new":
                self._conflict_apply_all_action = action
                self._conflict_apply_all_new_base = None

            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided(action, new_stem)
        except Exception:
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")

    # ---- links ----

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        try:
            u = url.toString()
            if not u:
                return
            if u.startswith("file://"):
                p = u.replace("file://", "", 1)
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(p))))
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(u))
        except Exception:
            pass

    # ---- model ----

    def _start_model_load(self) -> None:
        if self._model_thread is not None:
            return

        self._model_thread = QtCore.QThread(self)
        self._model_worker = ModelLoadWorker()
        self._model_worker.moveToThread(self._model_thread)

        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.model_ready.connect(self._on_model_ready)
        self._model_worker.model_error.connect(self._on_model_error)

        self._model_worker.finished.connect(self._model_thread.quit)
        self._model_worker.finished.connect(self._model_worker.deleteLater)
        self._model_thread.finished.connect(self._model_thread.deleteLater)
        self._model_thread.finished.connect(self._on_model_thread_finished)

        self._model_thread.start()

    def _on_model_ready(self, pipe) -> None:
        self.pipe = pipe
        self.log.ok(tr("log.model.ready", device=Config.DEVICE_FRIENDLY_NAME))
        self._update_buttons()

    def _on_model_error(self, msg: str) -> None:
        self.pipe = None
        self.log.err(tr("log.model.error", msg=msg))
        self._update_buttons()

    def _on_model_thread_finished(self) -> None:
        self._model_thread = None
        self._model_worker = None
        self._update_buttons()

    # ---- ui state ----

    def _update_buttons(self) -> None:
        has_items = self.tbl.rowCount() > 0
        has_sel = bool(self._checked_rows() or self._selected_rows())
        model_ready = self.pipe is not None
        running = self._transcribe_thread is not None

        self.btn_start.setEnabled(has_items and model_ready and not running)
        self.btn_cancel.setEnabled(running)
        self.btn_clear_list.setEnabled(has_items and not running)
        self.btn_remove_selected.setEnabled(has_sel and not running)
        self.btn_src_add.setEnabled(not running)
        self.btn_open_output.setEnabled(not running)
        self.btn_add_files.setEnabled(not running)
        self.btn_add_folder.setEnabled(not running)