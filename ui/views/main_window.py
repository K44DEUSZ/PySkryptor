# ui/views/main_window.py
from __future__ import annotations

from typing import Optional, List, Dict, Any

from PyQt5 import QtWidgets, QtCore

from core.config.app_config import AppConfig as Config
from ui.i18n.translator import tr
from ui.views.files_panel import FilesPanel
from ui.views.downloader_panel import DownloaderPanel
from ui.views.dialogs import ask_cancel, ask_conflict
from ui.workers.model_loader_worker import ModelLoadWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.workers.download_worker import DownloadWorker
from ui.workers.metadata_worker import MetadataWorker


class MainWindow(QtWidgets.QMainWindow):
    """Thin coordinator: builds tabs, wires panels to workers, routes signals."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 820)

        # ---------- Root Layout ----------
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        # ---------- Tabs Header ----------
        header = QtWidgets.QGroupBox(tr("tabs.group"))
        h = QtWidgets.QHBoxLayout(header)
        self.rb_files = QtWidgets.QRadioButton(tr("tabs.files"))
        self.rb_down = QtWidgets.QRadioButton(tr("tabs.downloader"))
        self.rb_live = QtWidgets.QRadioButton(tr("tabs.live"))
        self.rb_settings = QtWidgets.QRadioButton(tr("tabs.settings"))
        self.rb_files.setChecked(True)
        for rb in (self.rb_files, self.rb_down, self.rb_live, self.rb_settings):
            h.addWidget(rb)
        h.addStretch(1)
        v.addWidget(header)

        # ---------- Stack: Panels ----------
        self.stack = QtWidgets.QStackedWidget()
        v.addWidget(self.stack, 1)

        self.files_panel = FilesPanel()
        self.stack.addWidget(self.files_panel)

        self.down_panel = DownloaderPanel()
        self.stack.addWidget(self.down_panel)

        # Placeholders (unchanged)
        for title in (tr("tabs.live") + " — w przygotowaniu.", tr("tabs.settings") + " — w przygotowaniu."):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lay.addWidget(QtWidgets.QLabel(title))
            lay.addStretch(1)
            self.stack.addWidget(page)

        # ---------- Runtime State ----------
        Config.initialize()
        self.pipe = None

        # Workers
        self._loader_thread: Optional[QtCore.QThread] = None
        self._loader_worker: Optional[ModelLoadWorker] = None

        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None

        self._down_thread: Optional[QtCore.QThread] = None
        self._down_worker: Optional[DownloadWorker] = None
        self._down_running: bool = False

        self._meta_thread: Optional[QtCore.QThread] = None
        self._meta_worker: Optional[MetadataWorker] = None

        # Conflict „apply to all”
        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        # ---------- Wire Tabs ----------
        self.rb_files.toggled.connect(self._on_tab_changed)
        self.rb_down.toggled.connect(self._on_tab_changed)
        self.rb_live.toggled.connect(self._on_tab_changed)
        self.rb_settings.toggled.connect(self._on_tab_changed)

        # ---------- Wire FilesPanel ----------
        self.files_panel.request_details.connect(self._on_request_details)
        self.files_panel.start_requested.connect(self._on_start_transcription)
        self.files_panel.cancel_requested.connect(self._on_cancel_transcription)

        # ---------- Wire DownloaderPanel ----------
        self.down_panel.probe_requested.connect(self._on_probe)
        self.down_panel.download_requested.connect(self._on_download)

        # ---------- Start Model Loading ----------
        self._start_model_loading_thread()

    # ---------- Tabs ----------

    def _on_tab_changed(self) -> None:
        if self.rb_files.isChecked():
            self.stack.setCurrentIndex(0)
        elif self.rb_down.isChecked():
            self.stack.setCurrentIndex(1)
        elif self.rb_live.isChecked():
            self.stack.setCurrentIndex(2)
        elif self.rb_settings.isChecked():
            self.stack.setCurrentIndex(3)

    # ---------- Model Loader ----------

    def _start_model_loading_thread(self) -> None:
        if self._loader_thread is not None:
            return
        self._loader_thread = QtCore.QThread(self)
        self._loader_worker = ModelLoadWorker()
        self._loader_worker.moveToThread(self._loader_thread)

        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.progress_log.connect(self.files_panel.append_log)
        self._loader_worker.model_ready.connect(self._on_model_ready)
        self._loader_worker.model_error.connect(self._on_model_error)
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.finished.connect(self._loader_worker.deleteLater)
        self._loader_thread.finished.connect(self._on_loader_finished)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)
        self._loader_thread.start()

        self.files_panel.append_log(tr("log.init.bg"))

    def _on_model_ready(self, pipe_obj: object) -> None:
        self.pipe = pipe_obj
        self.files_panel.append_log(tr("log.model.ready"))
        self.files_panel.set_model_ready(True)

    def _on_model_error(self, msg: str) -> None:
        self.files_panel.append_log(tr("log.model.error", msg=msg))
        self.files_panel.set_model_ready(False)

    def _on_loader_finished(self) -> None:
        self._loader_thread = None
        self._loader_worker = None

    # ---------- Metadata (FilesPanel) ----------

    def _on_request_details(self, keys: List[str]) -> None:
        if not keys:
            return
        if self._meta_thread is not None:
            self._meta_thread.requestInterruption()

        entries = [{"type": ("url" if k.lower().startswith("http") else "file"), "value": k} for k in keys]
        self._meta_thread = QtCore.QThread(self)
        self._meta_worker = MetadataWorker(entries)
        self._meta_worker.moveToThread(self._meta_thread)

        self._meta_thread.started.connect(self._meta_worker.run)
        self._meta_worker.progress_log.connect(self.files_panel.append_log)
        self._meta_worker.table_ready.connect(self.files_panel.update_details_rows)
        self._meta_worker.finished.connect(self._meta_thread.quit)
        self._meta_worker.finished.connect(self._meta_worker.deleteLater)
        self._meta_thread.finished.connect(self._on_meta_finished)
        self._meta_thread.finished.connect(self._meta_thread.deleteLater)
        self._meta_thread.start()

    def _on_meta_finished(self) -> None:
        self._meta_thread = None
        self._meta_worker = None
        self.files_panel.refresh_buttons()

    # ---------- Transcription (FilesPanel) ----------

    def _on_start_transcription(self, entries: List[Dict[str, Any]]) -> None:
        if not self.pipe:
            self.files_panel.append_log(tr("log.pipe_not_ready"))
            return
        if not entries:
            self.files_panel.append_log(tr("log.no_items"))
            return

        self._conflict_apply_all_action = None
        self._conflict_apply_all_new_base = None

        self.files_panel.append_log(tr("log.start"))

        self._transcribe_thread = QtCore.QThread(self)
        self._transcribe_worker = TranscriptionWorker(pipe=self.pipe, entries=entries)
        self._transcribe_worker.moveToThread(self._transcribe_thread)

        self._transcribe_thread.started.connect(self._transcribe_worker.run)

        self._transcribe_worker.log.connect(self.files_panel.append_log)
        self._transcribe_worker.progress.connect(self.files_panel.set_progress)
        self._transcribe_worker.item_status.connect(self.files_panel.set_item_status)
        self._transcribe_worker.item_path_update.connect(self.files_panel.update_item_path)
        self._transcribe_worker.transcript_ready.connect(self.files_panel.enable_preview_for_key)
        self._transcribe_worker.conflict_check.connect(self._on_conflict_dialog)

        self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
        self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
        self._transcribe_thread.finished.connect(self._on_transcription_finished)
        self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)

        self._transcribe_thread.start()
        self.files_panel.refresh_buttons(transcribing=True)

    def _on_cancel_transcription(self) -> None:
        if not self._transcribe_worker:
            return
        if not ask_cancel(self):
            return
        self._transcribe_worker.cancel()
        self.files_panel.append_log(tr("log.unexpected", msg="⏹️ przerwano na żądanie"))

    def _on_transcription_finished(self) -> None:
        self._transcribe_thread = None
        self._transcribe_worker = None
        self.files_panel.append_log(tr("log.done"))
        self.files_panel.refresh_buttons(transcribing=False)

    @QtCore.pyqtSlot(str, str)
    def _on_conflict_dialog(self, stem: str, existing_dir: str) -> None:
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
            self.files_panel.append_log(tr("log.unexpected", msg=f"Błąd okna konfliktu: {e}"))
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")

    # ---------- Downloader ----------

    def _on_probe(self, url: str) -> None:
        if self._down_running:
            self.down_panel.append_log(tr("down.log.analyze"))
            return

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(action="probe", url=url)
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self.down_panel.append_log)
        self._down_worker.meta_ready.connect(self.down_panel.set_meta)
        self._down_worker.download_error.connect(self.down_panel.show_error)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_downloader_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._down_thread.start()
        self.down_panel.set_busy(True)

    def _on_download(self, url: str, kind: str, quality: str, ext: str) -> None:
        if self._down_running:
            self.down_panel.append_log(tr("down.log.downloading"))
            return

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(action="download", url=url, kind=kind, quality=quality, ext=ext)
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self.down_panel.append_log)
        self._down_worker.progress_pct.connect(self.down_panel.set_progress)
        self._down_worker.download_finished.connect(self.down_panel.on_download_finished)
        self._down_worker.download_error.connect(self.down_panel.show_error)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_downloader_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._down_thread.start()
        self.down_panel.set_busy(True)

    def _on_downloader_finished(self) -> None:
        self._down_thread = None
        self._down_worker = None
        self._down_running = False
        self.down_panel.set_busy(False)
