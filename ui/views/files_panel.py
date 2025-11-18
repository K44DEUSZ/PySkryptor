# ui/views/panels/files_panel.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.utils.text import format_bytes, format_hms, is_url
from ui.i18n.translator import tr
from ui.widgets.file_drop_list import FileDropList
from ui.workers.model_loader_worker import ModelLoadWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.workers.metadata_worker import MetadataWorker
from ui.views.dialogs import ask_cancel, ask_conflict


class FilesPanel(QtWidgets.QWidget):
    """Full Files tab UI + logic (model load, metadata, transcription)."""
    log_signal = QtCore.pyqtSignal(str)

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
        root = QtWidgets.QVBoxLayout(self)

        # Source input
        src_bar = QtWidgets.QHBoxLayout()
        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setPlaceholderText(tr("files.placeholder"))
        self.btn_src_add = QtWidgets.QPushButton(tr("files.add"))
        src_bar.addWidget(self.src_edit, 1)
        src_bar.addWidget(self.btn_src_add)
        root.addLayout(src_bar)

        # Ops
        ops_bar = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton(tr("files.add_files"))
        self.btn_add_folder = QtWidgets.QPushButton(tr("files.add_folder"))
        self.btn_open_output = QtWidgets.QPushButton(tr("files.open_output"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))
        for w in (self.btn_add_files, self.btn_add_folder, self.btn_open_output):
            ops_bar.addWidget(w)
        ops_bar.addStretch(1)
        for w in (self.btn_remove_selected, self.btn_clear_list):
            ops_bar.addWidget(w)
        root.addLayout(ops_bar)

        # Hidden DnD list (API only)
        self.file_list = FileDropList()
        self.file_list.setVisible(False)
        root.addWidget(self.file_list)

        # Details table
        details_group = QtWidgets.QGroupBox(tr("files.details.title"))
        details_layout = QtWidgets.QVBoxLayout(details_group)
        self.tbl_details = QtWidgets.QTableWidget(0, 7)
        self.tbl_details.setHorizontalHeaderLabels([
            tr("files.details.col.name"),
            tr("files.details.col.source"),
            tr("files.details.col.path"),
            tr("files.details.col.size"),
            tr("files.details.col.duration"),
            tr("files.details.col.status"),
            tr("files.details.col.preview"),
        ])
        header = self.tbl_details.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.tbl_details.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_details.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        details_layout.addWidget(self.tbl_details, 2)
        root.addWidget(details_group, 2)

        # Controls
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

        # Log output
        self.output = QtWidgets.QTextEdit()
        self.output.setReadOnly(True)
        root.addWidget(self.output, 3)

        # ---------- State ----------
        self._row_by_key: Dict[str, int] = {}
        self._transcript_by_key: Dict[str, str] = {}

        Config.initialize()
        self.pipe = None

        # Threads/workers
        self._loader_thread: Optional[QtCore.QThread] = None
        self._loader_worker: Optional[ModelLoadWorker] = None
        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None
        self._meta_thread: Optional[QtCore.QThread] = None
        self._meta_worker: Optional[MetadataWorker] = None

        self._was_cancelled = False
        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        self.output.clear()
        self._append_log(tr("log.init.bg"))
        self._start_model_loading_thread()
        self._update_buttons()

        # ---------- Signals ----------
        self.log_signal.connect(self._append_log)

        self.btn_src_add.clicked.connect(self._on_src_add_clicked)
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_open_output.clicked.connect(self._open_output_folder)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_list)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        for b in (self.btn_start, self.btn_cancel, self.btn_clear_list, self.btn_remove_selected):
            b.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)

    # ---------- Convenience ----------
    def _append_log(self, text: str) -> None:
        try:
            self.output.append(text)
        except Exception:
            pass

    # ---------- Table ops ----------
    def _append_row(self, name: str, src: str, path: str) -> None:
        row = self.tbl_details.rowCount()
        self.tbl_details.insertRow(row)

        def set_cell(col: int, text: str) -> None:
            item = QtWidgets.QTableWidgetItem(text)
            if col in (self.COL_SRC, self.COL_SIZE, self.COL_DUR, self.COL_STATUS):
                item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.tbl_details.setItem(row, col, item)

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
        self.tbl_details.setCellWidget(row, self.COL_PREVIEW, btn)

        self._row_by_key[path] = row

    def _rows_selected(self) -> List[int]:
        return sorted({idx.row() for idx in self.tbl_details.selectionModel().selectedRows()})

    def _on_remove_selected(self) -> None:
        rows = self._rows_selected()
        rows.reverse()
        for r in rows:
            key = self.tbl_details.item(r, self.COL_PATH).text()
            self.tbl_details.removeRow(r)
            self._row_by_key.pop(key, None)
            self._transcript_by_key.pop(key, None)
        self._update_buttons()

    def _on_clear_list(self) -> None:
        self.tbl_details.setRowCount(0)
        self._row_by_key.clear()
        self._transcript_by_key.clear()
        self._update_buttons()

    def _on_src_add_clicked(self) -> None:
        text = self.src_edit.text().strip()
        if not text:
            self._append_log(tr("log.add.empty"))
            return
        src = "URL" if is_url(text) else "LOCAL"
        key = text
        name = text if src == "LOCAL" else text
        if src == "LOCAL":
            p = Path(text)
            if not p.exists():
                self._append_log(tr("log.add.missing"))
                return
            name = p.stem
        self._append_row(name, src, key)
        self.src_edit.clear()
        self._refresh_details_for_keys([key])
        self._update_buttons()
        self._append_log(tr("log.add.ok", text=text))

    def _on_add_files(self) -> None:
        dlg = QtWidgets.QFileDialog(self, tr("files.add_files"))
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        if dlg.exec_():
            for p in dlg.selectedFiles():
                path = str(Path(p))
                self._append_row(Path(p).stem, "LOCAL", path)
                self._refresh_details_for_keys([path])
        self._update_buttons()

    def _on_add_folder(self) -> None:
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("files.add_folder"))
        if dir_path:
            self._append_row(Path(dir_path).name, "LOCAL", dir_path)
            self._refresh_details_for_keys([dir_path])
        self._update_buttons()

    def _open_output_folder(self) -> None:
        try:
            Config.TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.TRANSCRIPTIONS_DIR)))
        except Exception as e:
            self._append_log(tr("log.unexpected", msg=str(e)))

    def _open_transcript_for_row(self, row: int) -> None:
        try:
            key = self.tbl_details.item(row, self.COL_PATH).text()
            path = self._transcript_by_key.get(key)
            if not path:
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception:
            pass

    # ---------- Metadata ----------
    def _refresh_details_for_keys(self, keys: List[str]) -> None:
        if not keys:
            return
        # cancel previous
        if self._meta_thread is not None:
            try:
                if self._meta_worker:
                    self._meta_worker.cancel()
            except Exception:
                pass
            self._meta_thread.requestInterruption()

        entries = [{"type": ("url" if is_url(k) else "file"), "value": k} for k in keys]
        self._meta_thread = QtCore.QThread(self)
        self._meta_worker = MetadataWorker(entries)
        self._meta_worker.moveToThread(self._meta_thread)

        self._meta_thread.started.connect(self._meta_worker.run)
        self._meta_worker.progress_log.connect(self._append_log)
        self._meta_worker.table_ready.connect(self._on_details_ready_partial)
        self._meta_worker.finished.connect(self._meta_thread.quit)
        self._meta_worker.finished.connect(self._meta_worker.deleteLater)
        self._meta_thread.finished.connect(self._on_details_finished)
        self._meta_thread.finished.connect(self._meta_thread.deleteLater)
        self._meta_thread.start()

    def _on_details_ready_partial(self, rows: List[dict]) -> None:
        for r in rows:
            key = str(r.get("path", ""))
            row = self._row_by_key.get(key)
            if row is None:
                continue
            self.tbl_details.item(row, self.COL_NAME).setText(str(r.get("name", "")))
            self.tbl_details.item(row, self.COL_SRC).setText(str(r.get("source", "")))
            self.tbl_details.item(row, self.COL_SIZE).setText(format_bytes(r.get("size")))
            self.tbl_details.item(row, self.COL_DUR).setText(format_hms(r.get("duration")))

    def _on_details_finished(self) -> None:
        self._meta_thread = None
        self._meta_worker = None
        self._update_buttons()

    # ---------- Transcription ----------
    def _start_model_loading_thread(self) -> None:
        if self._loader_thread is not None:
            return
        self._loader_thread = QtCore.QThread(self)
        self._loader_worker = ModelLoadWorker()
        self._loader_worker.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.model_ready.connect(self._on_model_ready)
        self._loader_worker.model_error.connect(self._on_model_error)
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.finished.connect(self._loader_worker.deleteLater)
        self._loader_thread.finished.connect(self._on_loader_finished)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)
        self._loader_thread.start()

    def _on_model_ready(self, pipe_obj: object) -> None:
        self.pipe = pipe_obj
        self._append_log(tr("log.model.ready"))
        self._update_buttons()

    def _on_model_error(self, msg: str) -> None:
        self._append_log(tr("log.model.error", msg=msg))
        self._update_buttons()

    def _on_loader_finished(self) -> None:
        self._loader_thread = None
        self._loader_worker = None
        self._update_buttons()

    def _on_start_clicked(self) -> None:
        if not self.pipe:
            self._append_log(tr("log.pipe_not_ready"))
            return
        entries = self.get_entries()
        if not entries:
            self._append_log(tr("log.no_items"))
            return

        self.progress.setValue(0)
        self._was_cancelled = False
        self._conflict_apply_all_action = None
        self._conflict_apply_all_new_base = None
        self._append_log(tr("log.start"))

        self._transcribe_thread = QtCore.QThread(self)
        self._transcribe_worker = TranscriptionWorker(pipe=self.pipe, entries=entries)
        self._transcribe_worker.moveToThread(self._transcribe_thread)
        self._transcribe_thread.started.connect(self._transcribe_worker.run)

        self._transcribe_worker.log.connect(self._append_log)
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
            self._transcribe_worker.cancel()
        except Exception:
            pass
        self._append_log(tr("log.cancelled"))

    def _on_transcribe_finished(self) -> None:
        self._transcribe_thread = None
        self._transcribe_worker = None
        if not self._was_cancelled:
            self._append_log(tr("log.done"))
        self._update_buttons()

    # ---------- Conflict rendezvous ----------
    @QtCore.pyqtSlot(str, str)
    def _on_conflict(self, stem: str, existing_dir: str) -> None:
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
        except Exception as e:
            self._append_log(tr("log.unexpected", msg=f"Conflict dialog error: {e}"))
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")

    # ---------- Row updates ----------
    @QtCore.pyqtSlot(str, str)
    def _on_item_status(self, key: str, status: str) -> None:
        row = self._row_by_key.get(key)
        if row is None:
            return
        self.tbl_details.item(row, self.COL_STATUS).setText(status)

    @QtCore.pyqtSlot(str, str)
    def _on_item_path_update(self, old_key: str, new_local_path: str) -> None:
        row = self._row_by_key.get(old_key)
        if row is None:
            return
        self.tbl_details.item(row, self.COL_SRC).setText("LOCAL")
        self.tbl_details.item(row, self.COL_PATH).setText(new_local_path)
        self._row_by_key.pop(old_key, None)
        self._row_by_key[new_local_path] = row

    @QtCore.pyqtSlot(str, str)
    def _on_transcript_ready(self, key: str, transcript_path: str) -> None:
        self._transcript_by_key[key] = transcript_path
        row = self._row_by_key.get(key)
        if row is None:
            return
        w = self.tbl_details.cellWidget(row, self.COL_PREVIEW)
        if isinstance(w, QtWidgets.QToolButton):
            w.setEnabled(True)
        self._append_log(tr("log.transcript.saved", path=transcript_path))

    # ---------- Buttons ----------
    def _update_buttons(self) -> None:
        has_items = self.tbl_details.rowCount() > 0
        has_sel = bool(self._rows_selected())
        model_ready = self.pipe is not None
        running = self._transcribe_thread is not None
        self.btn_start.setEnabled(has_items and model_ready and not running)
        self.btn_cancel.setEnabled(running)
        self.btn_clear_list.setEnabled(has_items and not running)
        self.btn_remove_selected.setEnabled(has_sel and not running)

    # ---------- Public helpers ----------
    def get_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for r in range(self.tbl_details.rowCount()):
            path = self.tbl_details.item(r, self.COL_PATH).text()
            src = self.tbl_details.item(r, self.COL_SRC).text().lower()
            entries.append({"type": ("url" if src == "url" else "file"), "value": path})
        return entries

    def on_parent_close(self) -> None:
        # cancel background metadata/model/transcription
        try:
            if self._meta_thread and self._meta_worker:
                self._meta_worker.cancel()
                self._meta_thread.requestInterruption()
        except Exception:
            pass
        try:
            if self._transcribe_worker:
                self._transcribe_worker.cancel()
        except Exception:
            pass
