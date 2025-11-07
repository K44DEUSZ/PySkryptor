# ui/views/main_window.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.files.file_manager import FileManager
from core.utils.text import format_bytes, format_hms, sanitize_filename, is_url
from ui.i18n.translator import tr
from ui.widgets.file_drop_list import FileDropList
from ui.workers.model_loader_worker import ModelLoadWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.workers.download_worker import DownloadWorker
from ui.workers.metadata_worker import MetadataWorker
from ui.views.dialogs import ask_cancel, ask_conflict


class MainWindow(QtWidgets.QMainWindow):
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
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 820)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        # Tabs
        tabs_box = QtWidgets.QGroupBox(tr("tabs.group"))
        tabs_layout = QtWidgets.QHBoxLayout(tabs_box)
        self.rb_files = QtWidgets.QRadioButton(tr("tabs.files"))
        self.rb_down = QtWidgets.QRadioButton(tr("tabs.downloader"))
        self.rb_live = QtWidgets.QRadioButton(tr("tabs.live"))
        self.rb_settings = QtWidgets.QRadioButton(tr("tabs.settings"))
        self.rb_files.setChecked(True)
        for rb in (self.rb_files, self.rb_down, self.rb_live, self.rb_settings):
            tabs_layout.addWidget(rb)
        tabs_layout.addStretch(1)
        main_layout.addWidget(tabs_box)

        self.stack = QtWidgets.QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        # === Files page (index 0) ===
        files_page = QtWidgets.QWidget()
        files_layout = QtWidgets.QVBoxLayout(files_page)

        src_bar = QtWidgets.QHBoxLayout()
        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setPlaceholderText(tr("files.placeholder"))
        self.btn_src_add = QtWidgets.QPushButton(tr("files.add"))
        src_bar.addWidget(self.src_edit, 1)
        src_bar.addWidget(self.btn_src_add)
        files_layout.addLayout(src_bar)

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
        files_layout.addLayout(ops_bar)

        # Hidden backing store for DnD. We keep it for drag&drop API but do not show it.
        self.file_list = FileDropList()
        self.file_list.setVisible(False)
        files_layout.addWidget(self.file_list)

        # Details group becomes the primary list
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
        files_layout.addWidget(details_group, 2)

        ctrl_bar = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_start = QtWidgets.QPushButton(tr("ctrl.start"))
        self.btn_cancel = QtWidgets.QPushButton(tr("ctrl.cancel"))
        ctrl_bar.addWidget(self.progress, 1)
        ctrl_bar.addWidget(self.btn_start)
        ctrl_bar.addWidget(self.btn_cancel)
        files_layout.addLayout(ctrl_bar)

        self.output = QtWidgets.QTextEdit()
        self.output.setReadOnly(True)
        files_layout.addWidget(self.output, 3)

        self.stack.addWidget(files_page)

        # === Downloader page (index 1) ===
        down_page = QtWidgets.QWidget()
        down_layout = QtWidgets.QVBoxLayout(down_page)

        url_row = QtWidgets.QHBoxLayout()
        self.ed_url = QtWidgets.QLineEdit()
        self.ed_url.setPlaceholderText(tr("down.url.placeholder"))
        self.btn_probe = QtWidgets.QPushButton(tr("down.probe"))
        self.btn_open_downloads = QtWidgets.QPushButton(tr("down.open_folder"))
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_probe)
        url_row.addWidget(self.btn_open_downloads)
        down_layout.addLayout(url_row)

        meta_group = QtWidgets.QGroupBox(tr("down.meta.title"))
        meta_form = QtWidgets.QFormLayout(meta_group)
        self.lbl_service = QtWidgets.QLabel("-")
        self.lbl_title = QtWidgets.QLabel("-")
        self.lbl_duration = QtWidgets.QLabel("-")
        self.lbl_est_size = QtWidgets.QLabel("-")
        meta_form.addRow(tr("down.meta.service"), self.lbl_service)
        meta_form.addRow(tr("down.meta.name"), self.lbl_title)
        meta_form.addRow(tr("down.meta.duration"), self.lbl_duration)
        meta_form.addRow(tr("down.meta.size"), self.lbl_est_size)
        down_layout.addWidget(meta_group)

        sel_group = QtWidgets.QGroupBox(tr("down.select.title"))
        sel_layout = QtWidgets.QHBoxLayout(sel_group)
        self.cb_kind = QtWidgets.QComboBox()
        self.cb_kind.addItems([tr("down.select.type.video"), tr("down.select.type.audio")])
        self.cb_quality = QtWidgets.QComboBox()
        self.cb_ext = QtWidgets.QComboBox()
        self.cb_quality.addItems(["Auto", "1080p", "720p", "480p"])
        self.cb_ext.addItems(["mp4", "webm", "m4a", "mp3"])
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.type")))
        sel_layout.addWidget(self.cb_kind)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.quality")))
        sel_layout.addWidget(self.cb_quality)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.ext")))
        sel_layout.addWidget(self.cb_ext)
        sel_layout.addStretch(1)
        self.btn_download = QtWidgets.QPushButton(tr("down.download"))
        self.btn_download.setEnabled(False)
        sel_layout.addWidget(self.btn_download)
        down_layout.addWidget(sel_group)

        dl_row = QtWidgets.QHBoxLayout()
        self.pb_download = QtWidgets.QProgressBar()
        self.pb_download.setRange(0, 100)
        self.pb_download.setValue(0)
        dl_row.addWidget(self.pb_download, 1)
        down_layout.addLayout(dl_row)

        self.down_log = QtWidgets.QTextEdit()
        self.down_log.setReadOnly(True)
        down_layout.addWidget(self.down_log, 2)

        self.stack.addWidget(down_page)

        # Placeholders (indexes 2,3)
        for title in (tr("tabs.live") + " — w przygotowaniu.", tr("tabs.settings") + " — w przygotowaniu."):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lay.addWidget(QtWidgets.QLabel(title))
            lay.addStretch(1)
            self.stack.addWidget(page)

        # Signals
        self.log_signal.connect(self._append_log)
        self.rb_files.toggled.connect(self._on_tab_changed)
        self.rb_down.toggled.connect(self._on_tab_changed)
        self.rb_live.toggled.connect(self._on_tab_changed)
        self.rb_settings.toggled.connect(self._on_tab_changed)

        self.btn_src_add.clicked.connect(self._on_src_add_clicked)
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_open_output.clicked.connect(self._open_output_folder)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_list)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        # Downloader signals
        self.btn_probe.clicked.connect(self._on_probe_clicked)
        self.btn_download.clicked.connect(self._on_download_clicked)
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_quality.currentIndexChanged.connect(self._update_downloader_buttons)
        self.cb_ext.currentIndexChanged.connect(self._update_downloader_buttons)
        self.cb_quality.currentIndexChanged.connect(self._update_estimated_size)
        self.cb_ext.currentIndexChanged.connect(self._update_estimated_size)
        self.btn_open_downloads.clicked.connect(self._on_open_downloads_clicked)

        for b in (self.btn_start, self.btn_cancel, self.btn_clear_list, self.btn_remove_selected, self.btn_download):
            b.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)

        # data model for details table
        self._row_by_key: Dict[str, int] = {}            # key(url or path) -> row
        self._transcript_by_key: Dict[str, str] = {}     # key -> transcript path

        Config.initialize()
        self.pipe = None
        self._loader_thread: Optional[QtCore.QThread] = None
        self._loader_worker: Optional[ModelLoadWorker] = None
        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None

        # downloader state
        self._down_thread: Optional[QtCore.QThread] = None
        self._down_worker: Optional[DownloadWorker] = None
        self._down_meta: Optional[dict] = None
        self._down_running: bool = False

        # metadata state
        self._meta_thread: Optional[QtCore.QThread] = None
        self._meta_worker: Optional[MetadataWorker] = None

        self._is_running = False
        self._was_cancelled = False

        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        self.output.clear()
        self._append_log(tr("log.init.bg"))
        self._start_model_loading_thread()
        self._update_buttons()
        self._update_downloader_buttons()

    # ----- Utilities -----

    def _append_log(self, text: str) -> None:
        try:
            self.output.append(text)
        except Exception:
            pass

    # ----- Tabs -----

    def _on_tab_changed(self) -> None:
        if self.rb_files.isChecked():
            self.stack.setCurrentIndex(0)
        elif self.rb_down.isChecked():
            self.stack.setCurrentIndex(1)
        elif self.rb_live.isChecked():
            self.stack.setCurrentIndex(2)
        elif self.rb_settings.isChecked():
            self.stack.setCurrentIndex(3)

    # ----- Details table as the main list -----

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

    def _refresh_details_for_keys(self, keys: List[str]) -> None:
        if not keys:
            return
        entries = [{"type": ("url" if is_url(k) else "file"), "value": k} for k in keys]
        if self._meta_thread is not None:
            self._meta_thread.requestInterruption()
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

    def get_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for r in range(self.tbl_details.rowCount()):
            path = self.tbl_details.item(r, self.COL_PATH).text()
            src = self.tbl_details.item(r, self.COL_SRC).text().lower()
            if src == "url":
                entries.append({"type": "url", "value": path})
            else:
                entries.append({"type": "file", "value": path})
        return entries

    # ----- Conflict handling (GUI thread) -----

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
            self._append_log(tr("log.unexpected", msg=f"Błąd okna konfliktu: {e}"))
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")

    # ----- Downloader -----

    def _append_down_log(self, text: str) -> None:
        try:
            self.down_log.append(text)
        except Exception:
            pass

    def _on_open_downloads_clicked(self) -> None:
        try:
            Config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.DOWNLOADS_DIR)))
        except Exception as e:
            self._append_down_log(tr("down.log.error", msg=str(e)))

    def _on_probe_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url:
            self._append_down_log(tr("down.url.placeholder"))
            return
        if self._down_running:
            self._append_down_log("ℹ️ " + tr("down.log.analyze"))
            return

        self._down_meta = None
        self.lbl_service.setText("-")
        self.lbl_title.setText("-")
        self.lbl_duration.setText("-")
        self.lbl_est_size.setText("-")
        self.pb_download.setValue(0)
        self.down_log.clear()
        self.btn_download.setEnabled(False)

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(action="probe", url=url)
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self._append_down_log)
        self._down_worker.meta_ready.connect(self._on_probe_ready)
        self._down_worker.download_error.connect(self._on_download_error)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._down_thread.start()

    def _on_probe_ready(self, meta: Dict[str, Any]) -> None:
        self._down_meta = meta or {}
        service = meta.get("extractor") or meta.get("service") or "-"
        title = meta.get("title") or "-"
        duration = meta.get("duration")
        filesize = meta.get("filesize") or meta.get("filesize_approx")

        self.lbl_service.setText(str(service))
        self.lbl_title.setText(str(title))
        self.lbl_duration.setText(format_hms(duration))
        self.lbl_est_size.setText(format_bytes(filesize) if filesize else "-")

        self._update_downloader_buttons()
        self._update_estimated_size()

    def _on_download_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url or not self._down_meta:
            self._append_down_log(tr("down.url.placeholder"))
            return
        if self._down_running:
            self._append_down_log("ℹ️ " + tr("down.log.downloading"))
            return

        kind = self.cb_kind.currentText().lower()
        quality = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()
        kind = "video" if tr("down.select.type.video").lower() in self.cb_kind.currentText().lower() else "audio"

        self.pb_download.setValue(0)

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(
            action="download",
            url=url,
            kind=kind,
            quality=quality,
            ext=ext,
        )
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self._append_down_log)
        self._down_worker.progress_pct.connect(self.pb_download.setValue)
        self._down_worker.download_finished.connect(self._on_download_finished)
        self._down_worker.download_error.connect(self._on_download_error)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._down_thread.start()

    def _on_download_finished(self, path: Path) -> None:
        self.pb_download.setValue(100)
        self._append_down_log(tr("down.log.downloaded", path=str(path)))
        self._update_downloader_buttons()

    def _on_download_error(self, msg: str) -> None:
        self._append_down_log(tr("down.log.error", msg=msg))
        self._update_downloader_buttons()

    def _on_down_thread_finished(self) -> None:
        self._down_thread = None
        self._down_worker = None
        self._down_running = False
        self._update_downloader_buttons()
        self._update_estimated_size()

    def _on_kind_changed(self) -> None:
        kind = self.cb_kind.currentText().lower()
        if tr("down.select.type.audio").lower() in kind:
            self.cb_quality.clear()
            self.cb_quality.addItems(["Auto", "320k", "256k", "192k", "128k"])
            self.cb_ext.clear()
            self.cb_ext.addItems(["m4a", "mp3"])
        else:
            self.cb_quality.clear()
            self.cb_quality.addItems(["Auto", "1080p", "720p", "480p"])
            self.cb_ext.clear()
            self.cb_ext.addItems(["mp4", "webm"])
        self._update_downloader_buttons()
        self._update_estimated_size()

    def _update_downloader_buttons(self) -> None:
        has_meta = self._down_meta is not None
        self.btn_download.setEnabled(bool(has_meta and not self._down_running))

    def _update_estimated_size(self) -> None:
        meta = self._down_meta or {}
        fmts = meta.get("formats") or []
        if not fmts:
            self.lbl_est_size.setText("-")
            return

        kind = self.cb_kind.currentText().lower()
        q = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        def is_video(fmt: Dict[str, Any]) -> bool:
            return bool(fmt.get("vcodec") not in (None, "none"))

        def is_audio(fmt: Dict[str, Any]) -> bool:
            return not is_video(fmt)

        candidates = []
        for f in fmts:
            fext = str(f.get("ext") or "").lower()
            height = f.get("height") or 0
            abr = f.get("abr") or f.get("tbr") or 0
            if tr("down.select.type.audio").lower() in kind and not is_audio(f):
                continue
            if tr("down.select.type.video").lower() in kind:
                if not is_video(f):
                    continue
            if ext and fext and ext != "auto" and fext != ext:
                continue
            if q != "auto":
                if tr("down.select.type.audio").lower() in kind:
                    try:
                        want = int(q.replace("k", ""))
                        if not abr or abs(int(abr) - want) > 64:
                            continue
                    except Exception:
                        pass
                else:
                    try:
                        want = int(q.replace("p", ""))
                        if not height or abs(int(height) - want) > 200:
                            continue
                    except Exception:
                        pass
            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                candidates.append(int(size))

        if candidates:
            self.lbl_est_size.setText(format_bytes(max(candidates)))
        else:
            self.lbl_est_size.setText("-")

    # ----- Start/cancel transcription -----

    def _start_model_loading_thread(self) -> None:
        if self._loader_thread is not None:
            return
        self._loader_thread = QtCore.QThread(self)
        self._loader_worker = ModelLoadWorker()
        self._loader_worker.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.progress_log.connect(self._append_log)
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
        self._transcribe_worker.cancel()
        self._append_log(tr("log.unexpected", msg="⏹️ przerwano na żądanie"))

    def _on_transcribe_finished(self) -> None:
        self._transcribe_thread = None
        self._transcribe_worker = None
        if not self._was_cancelled:
            self._append_log(tr("log.done"))
        self._update_buttons()

    # ----- Row updates from worker -----

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

    # ----- Buttons state -----

    def _update_buttons(self) -> None:
        has_items = self.tbl_details.rowCount() > 0
        has_sel = bool(self._rows_selected())
        model_ready = self.pipe is not None
        running = self._transcribe_thread is not None

        self.btn_start.setEnabled(has_items and model_ready and not running)
        self.btn_cancel.setEnabled(running)
        self.btn_clear_list.setEnabled(has_items and not running)
        self.btn_remove_selected.setEnabled(has_sel and not running)
