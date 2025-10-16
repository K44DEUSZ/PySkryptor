# ui/views/main_window.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Tuple

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.files.file_manager import FileManager
from ui.widgets.file_drop_list import FileDropList
from ui.workers.model_loader_worker import ModelLoadWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.workers.download_worker import DownloadWorker
from ui.views.dialogs import ask_cancel, ask_conflict


class MainWindow(QtWidgets.QMainWindow):
    log_signal = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PySkryptor")
        self.resize(1200, 780)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        tabs_box = QtWidgets.QGroupBox("Zakładki")
        tabs_layout = QtWidgets.QHBoxLayout(tabs_box)
        self.rb_files = QtWidgets.QRadioButton("Transkrypcja plików")
        self.rb_down = QtWidgets.QRadioButton("Downloader")
        self.rb_live = QtWidgets.QRadioButton("Transkrypcja live")
        self.rb_settings = QtWidgets.QRadioButton("Ustawienia")
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
        self.src_edit.setPlaceholderText("Wklej ścieżkę pliku lub adres URL…")
        self.btn_src_add = QtWidgets.QPushButton("Dodaj")
        src_bar.addWidget(self.src_edit, 1)
        src_bar.addWidget(self.btn_src_add)
        files_layout.addLayout(src_bar)

        ops_bar = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton("Dodaj pliki…")
        self.btn_add_folder = QtWidgets.QPushButton("Dodaj folder…")
        self.btn_open_output = QtWidgets.QPushButton("Otwórz folder transkrykcji")
        self.btn_remove_selected = QtWidgets.QPushButton("Usuń zaznaczone")
        self.btn_clear_list = QtWidgets.QPushButton("Wyczyść listę")
        ops_bar.addWidget(self.btn_add_files)
        ops_bar.addWidget(self.btn_add_folder)
        ops_bar.addWidget(self.btn_open_output)
        ops_bar.addStretch(1)
        ops_bar.addWidget(self.btn_remove_selected)
        ops_bar.addWidget(self.btn_clear_list)
        files_layout.addLayout(ops_bar)

        self.file_list = FileDropList()
        self.file_list.setMinimumHeight(220)
        files_layout.addWidget(self.file_list, 2)

        ctrl_bar = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_start = QtWidgets.QPushButton("Rozpocznij transkrypcję")
        self.btn_cancel = QtWidgets.QPushButton("Anuluj")
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
        self.ed_url.setPlaceholderText("Wklej URL (np. YouTube/TikTok)…")
        self.btn_probe = QtWidgets.QPushButton("Analizuj")
        self.btn_open_downloads = QtWidgets.QPushButton("Otwórz folder pobrań")
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_probe)
        url_row.addWidget(self.btn_open_downloads)
        down_layout.addLayout(url_row)

        meta_group = QtWidgets.QGroupBox("Metadane")
        meta_form = QtWidgets.QFormLayout(meta_group)
        self.lbl_service = QtWidgets.QLabel("-")
        self.lbl_title = QtWidgets.QLabel("-")
        self.lbl_duration = QtWidgets.QLabel("-")
        self.lbl_est_size = QtWidgets.QLabel("-")
        meta_form.addRow("Serwis:", self.lbl_service)
        meta_form.addRow("Tytuł:", self.lbl_title)
        meta_form.addRow("Długość:", self.lbl_duration)
        meta_form.addRow("Szacowany rozmiar:", self.lbl_est_size)
        down_layout.addWidget(meta_group)

        sel_group = QtWidgets.QGroupBox("Wybór formatu")
        sel_layout = QtWidgets.QHBoxLayout(sel_group)
        self.cb_kind = QtWidgets.QComboBox()
        self.cb_kind.addItems(["Wideo", "Audio"])
        self.cb_quality = QtWidgets.QComboBox()
        self.cb_ext = QtWidgets.QComboBox()
        self.cb_quality.addItems(["Auto", "1080p", "720p", "480p"])
        self.cb_ext.addItems(["mp4", "webm"])
        sel_layout.addWidget(QtWidgets.QLabel("Typ:"))
        sel_layout.addWidget(self.cb_kind)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel("Jakość:"))
        sel_layout.addWidget(self.cb_quality)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel("Rozszerzenie:"))
        sel_layout.addWidget(self.cb_ext)
        sel_layout.addStretch(1)
        self.btn_download = QtWidgets.QPushButton("Pobierz")
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
        for title in ("🛠️ Transkrypcja live — w przygotowaniu.", "🛠️ Ustawienia — w przygotowaniu."):
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
        self.btn_open_output.clicked.connect(self._on_open_output_folder)
        self.btn_remove_selected.clicked.connect(self.file_list.remove_selected)
        self.btn_clear_list.clicked.connect(self.file_list.clear)
        self.file_list.files_changed.connect(self._update_buttons)
        self.file_list.itemSelectionChanged.connect(self._update_buttons)

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

        self._is_running = False
        self._was_cancelled = False

        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        self.output.clear()
        self._append_log("🟢 Inicjalizacja — ładowanie modelu w tle…")
        self._start_model_loading_thread()
        self._update_buttons()
        self._update_downloader_buttons()

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
            if apply_all:
                self._conflict_apply_all_action = action
                self._conflict_apply_all_new_base = new_stem if action == "new" else None

            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided(action, new_stem)
        except Exception as e:
            self._append_log(f"❗ Błąd okna konfliktu: {e} — pomijam ten element.")
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")

    # ----- Downloader -----

    def _on_kind_changed(self) -> None:
        kind = self.cb_kind.currentText()
        self.cb_quality.blockSignals(True)
        self.cb_ext.blockSignals(True)
        self.cb_quality.clear()
        self.cb_ext.clear()
        if kind == "Audio":
            self.cb_quality.addItems(["Auto", "320 kbps", "192 kbps", "128 kbps"])
            self.cb_ext.addItems(["m4a", "mp3", "webm"])
        else:
            self.cb_quality.addItems(["Auto", "1080p", "720p", "480p"])
            self.cb_ext.addItems(["mp4", "webm"])
        self.cb_quality.blockSignals(False)
        self.cb_ext.blockSignals(False)
        self._update_downloader_buttons()
        self._update_estimated_size()

    def _on_open_downloads_clicked(self) -> None:
        try:
            Config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.DOWNLOADS_DIR)))
        except Exception as e:
            self._down_append(f"❗ Nie udało się otworzyć folderu: {e}")

    def _on_probe_clicked(self) -> None:
        if self._down_running:
            return
        url = self.ed_url.text().strip()
        if not url:
            self._down_append("ℹ️ Podaj URL do analizy.")
            return
        self._down_clear_meta()
        self._down_append("🔎 Analiza URL…")
        self._down_start_worker(action="probe")

    def _on_meta_ready(self, meta: dict) -> None:
        self._down_meta = meta
        self.lbl_service.setText(str(meta.get("service", "-")))
        self.lbl_title.setText(str(meta.get("title", "-")))
        dur = int(meta.get("duration") or 0)
        h = dur // 3600
        m = (dur % 3600) // 60
        s = dur % 60
        self.lbl_duration.setText(f"{h:02d}:{m:02d}:{s:02d}")
        self._down_append("✅ Metadane gotowe.")
        self._down_running = False
        self._update_downloader_buttons()
        self._update_estimated_size()

    def _estimate_size(self) -> Optional[int]:
        if not self._down_meta:
            return None
        fmts = self._down_meta.get("formats") or []
        kind = "audio" if self.cb_kind.currentText() == "Audio" else "video"
        ext = self.cb_ext.currentText()
        q = self.cb_quality.currentText()

        best: Tuple[int, dict] = (0, {})
        for f in fmts:
            if f.get("kind") != kind:
                continue
            if ext and f.get("ext") and ext != "Auto" and f.get("ext") != ext:
                continue
            size = f.get("filesize") or 0
            if kind == "video":
                limit = None
                if q == "1080p":
                    limit = 1080
                elif q == "720p":
                    limit = 720
                elif q == "480p":
                    limit = 480
                height = f.get("height") or 0
                if limit and height > limit:
                    continue
                score = (height or 0) * 1_000_000 + size
            else:
                target = None
                if q == "320 kbps":
                    target = 320
                elif q == "192 kbps":
                    target = 192
                elif q == "128 kbps":
                    target = 128
                abr = f.get("abr") or 0
                if target and abr and abr < target - 8:
                    continue
                score = (abr or 0) * 1_000 + size
            if score > best[0]:
                best = (score, f)
        if not best[1]:
            return None
        return best[1].get("filesize")

    def _update_estimated_size(self) -> None:
        size = self._estimate_size()
        if size:
            mb = max(1, int(size / (1024 * 1024)))
            self.lbl_est_size.setText(f"~ {mb} MB")
        else:
            self.lbl_est_size.setText("-")

    def _build_format_expr(self) -> Tuple[Optional[str], str, str]:
        kind = "audio" if self.cb_kind.currentText() == "Audio" else "video"
        ext = self.cb_ext.currentText()

        if kind == "audio":
            if ext == "m4a":
                expr = "bestaudio[ext=m4a]/bestaudio"
            elif ext == "webm":
                expr = "bestaudio[ext=webm]/bestaudio"
            else:  # mp3 via postprocessor
                expr = "bestaudio/best"
            return expr, ext, kind

        q = self.cb_quality.currentText()
        height_limit = None
        if q == "1080p":
            height_limit = 1080
        elif q == "720p":
            height_limit = 720
        elif q == "480p":
            height_limit = 480

        base = "bestvideo+bestaudio/best"
        if height_limit:
            base = f"bestvideo[height<={height_limit}]+bestaudio/best[height<={height_limit}]"

        if ext == "mp4":
            base = f"{base}[ext=mp4]/best[ext=mp4]"
        elif ext == "webm":
            base = f"{base}[ext=webm]/best[ext=webm]"
        return base, ext, kind

    def _on_download_clicked(self) -> None:
        if self._down_running:
            return
        if not self._down_meta:
            self._down_append("ℹ️ Najpierw przeprowadź analizę URL.")
            return
        url = self.ed_url.text().strip()
        expr, ext, kind = self._build_format_expr()
        if not expr:
            self._down_append("ℹ️ Wybierz parametry formatu.")
            return
        self._down_append("⬇️ Pobieranie…")
        self.pb_download.setValue(0)
        self._down_start_worker(action="download", format_expr=expr, desired_ext=ext, kind=kind)

    def _down_start_worker(self, action: str, format_expr: Optional[str] = None, desired_ext: Optional[str] = None, kind: Optional[str] = None) -> None:
        self._down_running = True
        self.btn_probe.setEnabled(False)
        self.btn_download.setEnabled(False)

        url = self.ed_url.text().strip()
        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(
            url=url,
            action=action,
            format_expr=format_expr,
            desired_ext=desired_ext,
            kind=kind,
            output_dir=Config.DOWNLOADS_DIR,
        )
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self._down_append)
        self._down_worker.progress_pct.connect(self.pb_download.setValue)
        self._down_worker.meta_ready.connect(self._on_meta_ready)
        self._down_worker.download_finished.connect(self._on_download_finished)
        self._down_worker.download_error.connect(self._on_download_error)
        self._down_worker.finished.connect(self._down_on_finished)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._down_thread.deleteLater)
        self._down_thread.start()

    def _down_on_finished(self) -> None:
        self._down_running = False
        self.btn_probe.setEnabled(True)
        self._update_downloader_buttons()

    def _on_download_finished(self, path_obj) -> None:
        p = Path(path_obj)
        self._down_append(f"✅ Pobrano i zapisano: {p}")
        self.pb_download.setValue(100)

    def _on_download_error(self, msg: str) -> None:
        self._down_append(f"❌ Błąd pobierania: {msg}")

    def _down_append(self, text: str) -> None:
        self.down_log.append(text)

    def _down_clear_meta(self) -> None:
        self.lbl_service.setText("-")
        self.lbl_title.setText("-")
        self.lbl_duration.setText("-")
        self.lbl_est_size.setText("-")
        self.pb_download.setValue(0)
        self._down_meta = None
        self._update_downloader_buttons()

    def _update_downloader_buttons(self) -> None:
        can_download = (self._down_meta is not None) and (not self._down_running)
        self.btn_download.setEnabled(can_download)

    # ----- Files transcription -----

    def _start_model_loading_thread(self) -> None:
        self._loader_thread = QtCore.QThread(self)
        self._loader_worker = ModelLoadWorker()
        self._loader_worker.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.progress_log.connect(self._append_log)
        self._loader_worker.model_ready.connect(self._on_model_ready)
        self._loader_worker.model_error.connect(self._on_model_error)
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.finished.connect(self._loader_worker.deleteLater)
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)
        self._loader_thread.start()

    @QtCore.pyqtSlot(str)
    def _append_log(self, text: str) -> None:
        try:
            self.output.append(text)
        except Exception:
            print(text)

    @QtCore.pyqtSlot(object)
    def _on_model_ready(self, pipeline_obj) -> None:
        self.pipe = pipeline_obj
        self._append_log("✅ Model załadowany — możesz rozpocząć transkrypcję")
        self._update_buttons()

    @QtCore.pyqtSlot(str)
    def _on_model_error(self, msg: str) -> None:
        self._append_log(f"❌ Błąd ładowania modelu: {msg}")
        self._update_buttons()

    def _on_add_files(self) -> None:
        dlg = QtWidgets.QFileDialog(self, "Wybierz pliki")
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        if dlg.exec_():
            paths = [Path(p) for p in dlg.selectedFiles()]
            self.file_list.add_files(paths)
        self._update_buttons()

    def _on_add_folder(self) -> None:
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Wybierz folder")
        if dir_path:
            self.file_list.add_files([dir_path])
        self._update_buttons()

    def _on_open_output_folder(self) -> None:
        try:
            Config.TRANSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.TRANSCRIPTIONS_DIR)))
        except Exception as e:
            self._append_log(f"❗ Nie udało się otworzyć folderu: {e}")

    def _on_src_add_clicked(self) -> None:
        text = self.src_edit.text().strip()
        try:
            if not text:
                self._append_log("ℹ️ Wpisz ścieżkę pliku lub adres URL.")
                return
            added, msg = self.file_list.add_entry(text)
            if added:
                self._append_log(f"✅ Dodano: {msg}")
                self.src_edit.clear()
            else:
                self._append_log(f"⚠️ Nie dodano: {msg}")
        finally:
            self._update_buttons()

    def _on_start_clicked(self) -> None:
        try:
            if self.pipe is None:
                self._append_log("⚠️ Pipeline nie jest gotowy.")
                return
            entries = self.file_list.get_entries()
            if not entries:
                self._append_log("ℹ️ Dodaj przynajmniej jedno źródło (plik lub URL).")
                return

            self._is_running = True
            self._was_cancelled = False
            self.progress.setValue(0)
            self._update_buttons()
            self._append_log("▶️ Start transkrypcji…")

            self._transcribe_thread = QtCore.QThread(self)
            self._transcribe_worker = TranscriptionWorker(files=None, pipe=self.pipe, entries=entries)
            self._transcribe_worker.moveToThread(self._transcribe_thread)

            self._transcribe_worker.log.connect(self._append_log)
            self._transcribe_worker.progress.connect(self.progress.setValue)
            self._transcribe_worker.finished.connect(self._on_transcribe_finished)
            self._transcribe_worker.conflict_check.connect(self._on_conflict)

            self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
            self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
            self._transcribe_thread.finished.connect(self._on_transcribe_thread_finished)
            self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)

            self._transcribe_thread.started.connect(self._transcribe_worker.run)
            self._transcribe_thread.start()
        except Exception as e:
            self._append_log(f"❗ Błąd uruchamiania transkrypcji: {e}")
            self._is_running = False
            self._update_buttons()

    def _hard_cancel(self) -> None:
        if self._transcribe_thread is None:
            return
        self._append_log("🛑 Twarde przerwanie — zatrzymywanie wątku…")
        self._was_cancelled = True
        try:
            if self._transcribe_worker is not None:
                self._transcribe_worker.cancel()
            self._transcribe_thread.terminate()
            self._transcribe_thread.wait(2000)
        except Exception as e:
            self._append_log(f"❗ Błąd przy twardym przerwaniu: {e}")
        finally:
            self._transcribe_thread = None
            self._transcribe_worker = None
            self._is_running = False
            self.progress.setValue(0)
            self._append_log("⏹️ Zatrzymano.")
            self._update_buttons()

    def _on_cancel_clicked(self) -> None:
        if not self._is_running:
            return
        if ask_cancel(self):
            self._hard_cancel()

    def _on_transcribe_finished(self) -> None:
        self._append_log("✅ Zakończono transkrypcję.")

    def _on_transcribe_thread_finished(self) -> None:
        if self._was_cancelled:
            self.progress.setValue(0)
            self._was_cancelled = False
        self._transcribe_thread = None
        self._transcribe_worker = None
        self._is_running = False
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_items = len(self.file_list.get_entries()) > 0
        has_selection = len(self.file_list.selectedItems()) > 0

        start_enabled = (self.pipe is not None) and has_items and not self._is_running
        self.btn_start.setEnabled(start_enabled)

        start_tip: List[str] = []
        if self.pipe is None:
            start_tip.append("Model nie jest jeszcze gotowy.")
        if not has_items:
            start_tip.append("Nie masz dodanych jeszcze żadnych plików/URL-i.")
        if self._is_running:
            start_tip.append("Transkrypcja już trwa.")
        self.btn_start.setToolTip(" ".join(start_tip) if not start_enabled else "Rozpocznij transkrypcję wybranych pozycji.")
        self.btn_cancel.setEnabled(self._is_running)
        self.btn_cancel.setToolTip("Zatrzymaj natychmiast trwającą transkrypcję." if self._is_running else "Brak aktywnej transkrypcji do anulowania.")

        clear_enabled = has_items and not self._is_running
        self.btn_clear_list.setEnabled(clear_enabled)
        self.btn_clear_list.setToolTip("Usuń wszystkie pozycje z listy." if clear_enabled else ("Nie można czyścić listy podczas transkrykcji." if self._is_running else "Lista jest już pusta."))

        rem_enabled = has_selection and not self._is_running
        self.btn_remove_selected.setEnabled(rem_enabled)
        self.btn_remove_selected.setToolTip("Usuń zaznaczone pozycje." if rem_enabled else ("Zaznacz elementy na liście, aby je usunąć." if not has_selection else "Nie można usuwać podczas transkrypcji."))

        self.btn_src_add.setEnabled(not self._is_running)
        self.btn_add_files.setEnabled(not self._is_running)
        self.btn_add_folder.setEnabled(not self._is_running)
        self.src_edit.setEnabled(not self._is_running)
