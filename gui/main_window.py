# gui/main_window.py
# Pełny interfejs z asynchronicznym ładowaniem modelu, obsługą plików (lokalnie/drag&drop),
# pobieraniem z URL w tle, i bezpiecznymi logami do GUI.

from pathlib import Path
from typing import Optional, List

from PyQt5 import QtWidgets, QtCore

from core.config import Config
from core.model_manager import ModelManager
from gui.worker import Worker
from gui.file_drop_list import FileDropList
from core.downloader import Downloader


class ModelLoadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    model_ready = QtCore.pyqtSignal(object)  # emituje gotowy pipeline
    model_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            manager = ModelManager()

            def _log(line: str) -> None:
                self.progress_log.emit(line)

            # usuwamy dodatkowy, podwójny log – load() sam loguje start
            manager.load(log=_log)
            pipe = manager.pipe
            if pipe is None:
                self.model_error.emit("Pipeline nie został zainicjalizowany.")
            else:
                self.model_ready.emit(pipe)
        except Exception as e:
            self.model_error.emit(str(e))
        finally:
            self.finished.emit()


class UrlDownloadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal(list)   # lista ścieżek pobranych plików (str)
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(self, url: str, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.url = url

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            def _log(m: str) -> None:
                self.progress_log.emit(m)

            self.progress_log.emit(f"🌐 Pobieranie: {self.url}")
            paths = Downloader.download(urls=[self.url], on_file_ready=None, log=_log)
            if not paths:
                self.error.emit("Brak plików po pobraniu.")
            else:
                self.done.emit([str(p) for p in paths])
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class MainWindow(QtWidgets.QMainWindow):
    log_signal = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PySkryptor")
        self.resize(1000, 680)

        # ---------- Widżety i układ ----------
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        # Pasek źródeł: pliki lokalne / adres URL
        self.source_group = QtWidgets.QGroupBox("Źródło")
        self.rb_local = QtWidgets.QRadioButton("Pliki lokalne")
        self.rb_url = QtWidgets.QRadioButton("Adres URL")
        self.rb_local.setChecked(True)

        source_layout = QtWidgets.QHBoxLayout(self.source_group)
        source_layout.addWidget(self.rb_local)
        source_layout.addWidget(self.rb_url)
        source_layout.addStretch(1)

        # Panel lokalny: przyciski operacji na plikach
        self.btn_add_files = QtWidgets.QPushButton("Dodaj pliki…")
        self.btn_add_folder = QtWidgets.QPushButton("Dodaj folder…")
        self.btn_remove_selected = QtWidgets.QPushButton("Usuń zaznaczone")
        self.btn_clear_list = QtWidgets.QPushButton("Wyczyść listę")

        local_panel = QtWidgets.QHBoxLayout()
        local_panel.addWidget(self.btn_add_files)
        local_panel.addWidget(self.btn_add_folder)
        local_panel.addStretch(1)
        local_panel.addWidget(self.btn_remove_selected)
        local_panel.addWidget(self.btn_clear_list)

        # Panel URL: pole + przycisk pobierania
        self.url_edit = QtWidgets.QLineEdit()
        self.url_edit.setPlaceholderText("Wklej URL (YouTube, plik audio/wideo, itp.)")
        self.btn_download = QtWidgets.QPushButton("Pobierz")

        url_panel = QtWidgets.QHBoxLayout()
        url_panel.addWidget(self.url_edit, 1)
        url_panel.addWidget(self.btn_download)

        self.url_widget = QtWidgets.QWidget()
        self.url_widget.setLayout(url_panel)
        self.url_widget.setVisible(False)

        # Lista plików z obsługą drag&drop
        self.file_list = FileDropList()
        self.file_list.setMinimumHeight(200)

        # Pasek postępu + przyciski sterujące transkrypcją
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.btn_start = QtWidgets.QPushButton("Rozpocznij transkrypcję")
        self.btn_cancel = QtWidgets.QPushButton("Anuluj")
        self.btn_start.setEnabled(False)  # aktywowany po załadowaniu modelu

        ctrl_panel = QtWidgets.QHBoxLayout()
        ctrl_panel.addWidget(self.progress, 1)
        ctrl_panel.addWidget(self.btn_start)
        ctrl_panel.addWidget(self.btn_cancel)

        # Pole logów
        self.output = QtWidgets.QTextEdit(self)
        self.output.setReadOnly(True)

        # Główny layout
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.source_group)
        layout.addLayout(local_panel)
        layout.addWidget(self.url_widget)
        layout.addWidget(self.file_list, 1)
        layout.addLayout(ctrl_panel)
        layout.addWidget(self.output, 2)

        # ---------- Sygnały ----------
        self.log_signal.connect(self._append_log)

        self.rb_local.toggled.connect(self._on_source_toggled)
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self.file_list.clear)

        self.btn_download.clicked.connect(self._on_download_clicked)
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        # FileDropList sygnał zmian (np. po dropie / usunięciu)
        self.file_list.files_changed.connect(self._on_files_changed)

        # ---------- Inicjalizacja środowiska ----------
        Config.initialize()

        # Pipeline i wątki robocze
        self.pipe = None
        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[Worker] = None
        self._download_thread: Optional[QtCore.QThread] = None
        self._download_worker: Optional[UrlDownloadWorker] = None

        # ---------- Start: UI widoczne i ładowanie modelu w tle ----------
        self.output.clear()
        self._append_log("🟢 Inicjalizacja — ładowanie modelu w tle…")
        self._start_model_loading_thread()

    # -------------- Wątki: ładowanie modelu --------------

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

    # -------------- Wątki: pobieranie URL --------------

    def _start_download_thread(self, url: str) -> None:
        if self._download_thread is not None:
            self._append_log("⏳ Trwa już pobieranie. Poczekaj na zakończenie.")
            return

        self._download_thread = QtCore.QThread(self)
        self._download_worker = UrlDownloadWorker(url)
        self._download_worker.moveToThread(self._download_thread)

        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.progress_log.connect(self._append_log)
        self._download_worker.done.connect(self._on_download_done)
        self._download_worker.error.connect(self._on_download_error)

        self._download_worker.finished.connect(self._download_thread.quit)
        self._download_worker.finished.connect(self._download_worker.deleteLater)
        self._download_thread.finished.connect(self._on_download_thread_finished)
        self._download_thread.finished.connect(self._download_thread.deleteLater)

        self._download_thread.start()

    def _on_download_done(self, paths: List[str]) -> None:
        self._append_log("✅ Pobieranie zakończone.")
        self.file_list.add_files([Path(p) for p in paths])

    def _on_download_error(self, msg: str) -> None:
        self._append_log(f"❌ Błąd pobierania: {msg}")

    def _on_download_thread_finished(self) -> None:
        self._download_thread = None
        self._download_worker = None

    # -------------- LOGOWANIE ----------------

    @QtCore.pyqtSlot(str)
    def _append_log(self, text: str) -> None:
        try:
            self.output.append(text)
        except Exception:
            print(text)

    # -------------- Reakcje na model_ready / model_error --------------

    @QtCore.pyqtSlot(object)
    def _on_model_ready(self, pipeline_obj) -> None:
        self.pipe = pipeline_obj
        self._append_log("✅ Model załadowany — możesz rozpocząć transkrypcję")
        self.btn_start.setEnabled(True)

    @QtCore.pyqtSlot(str)
    def _on_model_error(self, msg: str) -> None:
        self._append_log(f"❌ Błąd ładowania modelu: {msg}")
        self.btn_start.setEnabled(False)

    # -------------- Źródło danych ----------------

    def _on_source_toggled(self, checked: bool) -> None:
        is_local = self.rb_local.isChecked()
        self.url_widget.setVisible(not is_local)

    def _on_add_files(self) -> None:
        dlg = QtWidgets.QFileDialog(self, "Wybierz pliki")
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        if dlg.exec_():
            files = [Path(p) for p in dlg.selectedFiles()]
            self.file_list.add_files(files)

    def _on_add_folder(self) -> None:
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(self, "Wybierz folder")
        if dir_path:
            p = Path(dir_path)
            exts = {".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm"}
            files = [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in exts]
            self.file_list.add_files(files)

    def _on_remove_selected(self) -> None:
        self.file_list.remove_selected()

    def _on_files_changed(self) -> None:
        # Można tu dodać dodatkową walidację/stan UI zależny od liczby plików.
        pass

    def _on_download_clicked(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self._append_log("ℹ️ Wklej najpierw adres URL.")
            return
        self._start_download_thread(url)

    # -------------- Transkrypcja ----------------

    def _on_start_clicked(self) -> None:
        if self.pipe is None:
            self._append_log("⚠️ Pipeline nie jest gotowy. Spróbuj ponownie po załadowaniu modelu.")
            return
        files = [Path(p) for p in self.file_list.get_file_paths()]
        if not files:
            self._append_log("ℹ️ Dodaj pliki do listy albo pobierz je z URL.")
            return

        # Zablokuj UI startu
        self.btn_start.setEnabled(False)
        self.progress.setValue(0)
        self._append_log("▶️ Start transkrypcji…")

        # Uruchom Workera w wątku
        self._transcribe_thread = QtCore.QThread(self)
        self._transcribe_worker = Worker(
            model_manager=None,
            files=files,
            pipe=self.pipe,
        )
        self._transcribe_worker.moveToThread(self._transcribe_thread)

        self._transcribe_thread.started.connect(self._transcribe_worker.run)
        self._transcribe_worker.log.connect(self._append_log)
        self._transcribe_worker.progress.connect(self.progress.setValue)
        self._transcribe_worker.finished.connect(self._on_transcribe_finished)

        self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
        self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
        self._transcribe_thread.finished.connect(self._on_transcribe_thread_finished)
        self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)

        self._transcribe_thread.start()

    def _on_cancel_clicked(self) -> None:
        if self._transcribe_worker is not None:
            self._transcribe_worker.cancel()

    def _on_transcribe_finished(self) -> None:
        self._append_log("✅ Zakończono transkrypcję.")

    def _on_transcribe_thread_finished(self) -> None:
        self._transcribe_thread = None
        self._transcribe_worker = None
        self.btn_start.setEnabled(True)

    # -------------- Zamknięcie ----------------

    def closeEvent(self, event) -> None:
        try:
            self._append_log("👋 Zamykanie…")
        finally:
            super().closeEvent(event)
