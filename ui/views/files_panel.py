# ui/views/files_panel.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from core.config.app_config import AppConfig as Config
from core.io.text import is_url
from ui.utils.translating import tr
from ui.utils.logging import QtHtmlLogSink
from ui.views.dialogs import ask_cancel, ask_conflict
from ui.workers.metadata_worker import MetadataWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.workers.model_loader_worker import ModelLoadWorker


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    try:
        n = int(n)
    except Exception:
        return "-"
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB"]
    v = float(n)
    u = 0
    while v >= 1024 and u < len(units) - 1:
        v /= 1024.0
        u += 1
    return f"{v:.2f} {units[u]}"


def _fmt_seconds(sec: float | None) -> str:
    if sec is None:
        return "-"
    try:
        sec = float(sec)
    except Exception:
        return "-"
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
        if not e.mimeData().hasUrls():
            super().dropEvent(e)
            return

        paths = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                paths.append(p)
        out = [str(p) for p in dict.fromkeys(paths)]
        if out:
            self.pathsDropped.emit(out)

        e.acceptProposedAction()


class FilesPanel(QtWidgets.QWidget):
    """Files tab: sources list + model + transcription control."""

    COL_CHECK = 0
    COL_NO = 1
    COL_TITLE = 2
    COL_DUR = 3
    COL_SRC = 4
    COL_PATH = 5
    COL_STATUS = 6
    COL_PREVIEW = 7

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)

        # Source input bar
        src_bar = QtWidgets.QHBoxLayout()
        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setPlaceholderText(tr("files.placeholder"))
        self.btn_src_add = QtWidgets.QPushButton(tr("files.add"))
        self.btn_open_output = QtWidgets.QPushButton(tr("files.open_output"))
        src_bar.addWidget(self.src_edit, 1)
        src_bar.addWidget(self.btn_src_add)
        src_bar.addWidget(self.btn_open_output)
        root.addLayout(src_bar)

        # Table
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

        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSortingEnabled(False)

        header = self.tbl.horizontalHeader()
        header.setSectionResizeMode(self.COL_CHECK, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_NO, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_TITLE, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_DUR, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_SRC, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_STATUS, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PREVIEW, QtWidgets.QHeaderView.ResizeToContents)

        details_layout.addWidget(self.tbl, 2)
        root.addWidget(details_group, 2)

        # Controls under table
        ops_bar = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton(tr("files.add_files"))
        self.btn_add_folder = QtWidgets.QPushButton(tr("files.add_folder"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))
        ops_bar.addWidget(self.btn_add_files)
        ops_bar.addWidget(self.btn_add_folder)
        ops_bar.addStretch(1)
        ops_bar.addWidget(self.btn_remove_selected)
        ops_bar.addWidget(self.btn_clear_list)
        root.addLayout(ops_bar)

        # Start/cancel
        ctrl_bar = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_start = QtWidgets.QPushButton(tr("ctrl.start"))
        self.btn_cancel = QtWidgets.QPushButton(tr("ctrl.cancel"))
        ctrl_bar.addWidget(self.progress, 1)
        ctrl_bar.addWidget(self.btn_start)
        ctrl_bar.addWidget(self.btn_cancel)
        root.addLayout(ctrl_bar)

        # Log
        self.output = QtWidgets.QTextBrowser()
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)
        self.output.anchorClicked.connect(self._on_anchor_clicked)
        root.addWidget(self.output, 3)
        self.log = QtHtmlLogSink(self.output)

        # State
        self.pipe = None
        self._keys: set[str] = set()  # path/url unique keys
        self._row_by_key: Dict[str, int] = {}
        self._transcript_by_key: Dict[str, str] = {}

        self._model_thread: Optional[QtCore.QThread] = None
        self._model_worker: Optional[ModelLoadWorker] = None

        self._meta_thread: Optional[QtCore.QThread] = None
        self._meta_worker: Optional[MetadataWorker] = None

        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None

        self._was_cancelled = False
        self._conflict_apply_all_action: str | None = None
        self._conflict_apply_all_new_base: str | None = None

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

        # Load model in background
        Config.initialize()
        self._start_model_load()
        self._update_buttons()

    # ----- Link opener -----

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        try:
            if url.isLocalFile():
                QtGui.QDesktopServices.openUrl(url)
        except Exception:
            pass

    # ----- Entries API -----

    def get_entries(self) -> List[Dict[str, Any]]:
        """Return list entries in the shape expected by TranscriptionWorker."""
        entries: List[Dict[str, Any]] = []
        for r in range(self.tbl.rowCount()):
            key = self._key_at_row(r)
            if not key:
                continue
            if is_url(key):
                entries.append({"type": "url", "value": key})
            else:
                entries.append({"type": "file", "value": key})
        return entries

    # ----- Add helpers -----

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
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)

        # checkbox
        it_check = QtWidgets.QTableWidgetItem("")
        it_check.setFlags(it_check.flags() | QtCore.Qt.ItemIsUserCheckable)
        it_check.setCheckState(QtCore.Qt.Unchecked)
        self.tbl.setItem(row, self.COL_CHECK, it_check)

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

        it_path = QtWidgets.QTableWidgetItem(key)
        self.tbl.setItem(row, self.COL_PATH, it_path)

        it_status = QtWidgets.QTableWidgetItem("-")
        it_status.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_STATUS, it_status)

        btn = QtWidgets.QToolButton()
        btn.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView))
        btn.setEnabled(False)
        btn.clicked.connect(lambda: self._open_transcript_for_row(row))
        self.tbl.setCellWidget(row, self.COL_PREVIEW, btn)

        self._row_by_key[key] = row

    def _update_row_from_meta(self, row: int, meta: Dict[str, Any]) -> None:
        if row < 0 or row >= self.tbl.rowCount():
            return

        title = str(meta.get("name") or meta.get("title") or "-")
        duration = meta.get("duration")
        source = str(meta.get("source") or "-")
        path = str(meta.get("path") or "-")

        self.tbl.item(row, self.COL_TITLE).setText(title)
        self.tbl.item(row, self.COL_DUR).setText(_fmt_seconds(duration))
        self.tbl.item(row, self.COL_SRC).setText(source)
        self.tbl.item(row, self.COL_PATH).setText(path)

    def _start_metadata_for(self, keys: List[str]) -> None:
        if not keys:
            return
        if self._meta_thread is not None:
            # best-effort cancel previous
            try:
                self._meta_thread.requestInterruption()
            except Exception:
                pass

        entries = []
        for k in keys:
            if is_url(k):
                entries.append({"type": "url", "value": k})
            else:
                entries.append({"type": "file", "value": k})

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

    # ----- Actions -----

    def _on_add_clicked(self) -> None:
        raw = (self.src_edit.text() or "").strip()
        if not raw:
            self.log.warn(tr("log.add.empty"))
            return

        key = raw
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

    def _on_add_files_clicked(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            tr("files.add_files"),
            str(Path.home()),
            "Media files (*.wav *.mp3 *.flac *.m4a *.ogg *.aac *.mp4 *.webm *.mkv *.mov *.avi);;All files (*)",
        )
        added: List[str] = []
        for p in paths:
            key = str(Path(p))
            if self._try_add_key(key):
                self._insert_placeholder_row(key, src_label="LOCAL")
                added.append(key)
        if added:
            self._start_metadata_for(added)
        self._refresh_order_numbers()
        self._update_buttons()

    def _on_add_folder_clicked(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, tr("files.add_folder"), str(Path.home()))
        if not folder:
            return
        base = Path(folder)
        allowed = set(Config.audio_extensions()) | set(Config.video_extensions())
        allowed = {a.lower() if a.startswith(".") else f".{a.lower()}" for a in allowed}

        added: List[str] = []
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in allowed:
                continue
            key = str(p)
            if self._try_add_key(key):
                self._insert_placeholder_row(key, src_label="LOCAL")
                added.append(key)

        if added:
            self._start_metadata_for(added[:30])
        self._refresh_order_numbers()
        self._update_buttons()

    @QtCore.pyqtSlot(list)
    def _on_paths_dropped(self, paths: List[str]) -> None:
        added: List[str] = []
        allowed = set(Config.audio_extensions()) | set(Config.video_extensions())
        allowed = {a.lower() if a.startswith(".") else f".{a.lower()}" for a in allowed}
        for raw in paths:
            p = Path(str(raw))
            if p.is_dir():
                for x in p.rglob("*"):
                    if x.is_file() and x.suffix.lower() in allowed:
                        key = str(x)
                        if self._try_add_key(key):
                            self._insert_placeholder_row(key, src_label="LOCAL")
                            added.append(key)
            else:
                if p.exists() and p.is_file() and p.suffix.lower() in allowed:
                    key = str(p)
                    if self._try_add_key(key):
                        self._insert_placeholder_row(key, src_label="LOCAL")
                        added.append(key)

        if added:
            self._start_metadata_for(added[:30])
        self._refresh_order_numbers()
        self._update_buttons()

    def _checked_rows(self) -> List[int]:
        rows: List[int] = []
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, self.COL_CHECK)
            if it and it.checkState() == QtCore.Qt.Checked:
                rows.append(r)
        return rows

    def _selected_rows(self) -> List[int]:
        return sorted({idx.row() for idx in self.tbl.selectionModel().selectedRows()})

    def _on_remove_selected(self) -> None:
        rows = self._checked_rows() or self._selected_rows()
        if not rows:
            return
        rows = sorted(rows, reverse=True)
        for r in rows:
            key = self._key_at_row(r)
            if key:
                self._keys.discard(key)
                self._row_by_key.pop(key, None)
                self._transcript_by_key.pop(key, None)
            self.tbl.removeRow(r)

        self._rebuild_row_map()
        self._refresh_order_numbers()
        self._update_buttons()

    def _on_clear_clicked(self) -> None:
        self.tbl.setRowCount(0)
        self._keys.clear()
        self._row_by_key.clear()
        self._transcript_by_key.clear()
        self.progress.setValue(0)
        self._update_buttons()

    # ----- Output folder -----

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

    # ----- Start / cancel transcription -----

    def _on_start_clicked(self) -> None:
        if not self.pipe:
            self.log.info(tr("log.pipe_not_ready"))
            return

        entries = self.get_entries()
        if not entries:
            self.log.info(tr("log.no_items"))
            return

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
        self._transcribe_worker.progress.connect(self.progress.setValue)
        self._transcribe_worker.item_status.connect(self._on_item_status)
        self._transcribe_worker.item_path_update.connect(self._on_item_path_update)
        self._transcribe_worker.transcript_ready.connect(self._on_transcript_ready)
        self._transcribe_worker.conflict_check.connect(self._on_conflict)

        self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
        self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
        self._transcribe_thread.finished.connect(self._on_transcribe_finished)
        self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)

        self._transcribe_thread.start()
        self._update_buttons()

    def _on_cancel_clicked(self) -> None:
        if not self._transcribe_worker:
            return
        if not ask_cancel(self):
            return
        self._was_cancelled = True
        try:
            if self._transcribe_thread is not None:
                self._transcribe_thread.requestInterruption()
            self._transcribe_worker.cancel()
        except Exception:
            pass
        self.log.warn(tr("log.cancelled"))

    def _on_transcribe_finished(self) -> None:
        self._transcribe_thread = None
        self._transcribe_worker = None

        if self._was_cancelled:
            self.progress.setValue(0)
        else:
            self.progress.setValue(100)
            self.log.ok(tr("log.done"))

        self._update_buttons()

    # ----- Conflict decisions -----

    @QtCore.pyqtSlot(str, str)
    def _on_conflict(self, stem: str, _existing_dir: str) -> None:
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

    # ----- Item updates from TranscriptionWorker -----

    def _key_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_PATH)
        return str(it.text()).strip() if it else ""

    def _rebuild_row_map(self) -> None:
        self._row_by_key.clear()
        for r in range(self.tbl.rowCount()):
            key = self._key_at_row(r)
            if key:
                self._row_by_key[key] = r

    def _refresh_order_numbers(self) -> None:
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, self.COL_NO)
            if it:
                it.setText(str(r + 1))

    @QtCore.pyqtSlot(str, str)
    def _on_item_status(self, key: str, status: str) -> None:
        row = self._row_by_key.get(key)
        if row is None:
            return
        it = self.tbl.item(row, self.COL_STATUS)
        if it:
            it.setText(status)

    @QtCore.pyqtSlot(str, str)
    def _on_item_path_update(self, old_key: str, new_key: str) -> None:
        row = self._row_by_key.pop(old_key, None)
        if row is None:
            return

        # Update internal sets
        self._keys.discard(old_key)
        self._keys.add(new_key)
        self._row_by_key[new_key] = row

        # Update table
        self.tbl.item(row, self.COL_PATH).setText(new_key)
        self.tbl.item(row, self.COL_SRC).setText("LOCAL")

        # kick metadata refresh for the new local file path
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

    def _open_transcript_for_row(self, row: int) -> None:
        try:
            key = self._key_at_row(row)
            p = self._transcript_by_key.get(key)
            if not p:
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(p))))
        except Exception:
            pass

    # ----- Model loading -----

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

        self._model_thread.start()

    def _on_model_ready(self, pipe) -> None:
        self.pipe = pipe
        self.log.ok(tr("log.model.ready", device=Config.DEVICE_FRIENDLY_NAME))
        self._update_buttons()

    def _on_model_error(self, msg: str) -> None:
        self.pipe = None
        self.log.err(tr("log.model.error", msg=msg))
        self._update_buttons()

    # ----- UI state -----

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
        self.btn_add_files.setEnabled(not running)
        self.btn_add_folder.setEnabled(not running)
