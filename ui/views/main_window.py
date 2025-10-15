# pyskryptor/ui/views/main_window.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, List

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config import Config
from core.files.file_manager import FileManager
from ui.widgets.file_drop_list import FileDropList
from ui.workers.model_loader_worker import ModelLoadWorker
from ui.workers.transcription_worker import TranscriptionWorker
from ui.views.dialogs import ask_cancel, ask_conflict


class MainWindow(QtWidgets.QMainWindow):
    log_signal = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PySkryptor")
        self.resize(1100, 720)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        tabs_box = QtWidgets.QGroupBox("Zakładki")
        tabs_layout = QtWidgets.QHBoxLayout(tabs_box)
        self.rb_files = QtWidgets.QRadioButton("Transkrypcja plików")
        self.rb_live = QtWidgets.QRadioButton("Transkrypcja live")
        self.rb_down = QtWidgets.QRadioButton("Downloader")
        self.rb_settings = QtWidgets.QRadioButton("Ustawienia")
        self.rb_files.setChecked(True)
        for rb in (self.rb_files, self.rb_live, self.rb_down, self.rb_settings):
            tabs_layout.addWidget(rb)
        tabs_layout.addStretch(1)
        main_layout.addWidget(tabs_box)

        self.stack = QtWidgets.QStackedWidget()
        main_layout.addWidget(self.stack, 1)

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
        self.btn_open_output = QtWidgets.QPushButton("Otwórz folder transkrypcji")
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

        for title in ("🛠️ Transkrypcja live — w przygotowaniu.",
                      "🛠️ Downloader — w przygotowaniu.",
                      "🛠️ Ustawienia — w przygotowaniu."):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lay.addWidget(QtWidgets.QLabel(title))
            lay.addStretch(1)
            self.stack.addWidget(page)

        self.log_signal.connect(self._append_log)
        self.rb_files.toggled.connect(self._on_tab_changed)
        self.rb_live.toggled.connect(self._on_tab_changed)
        self.rb_down.toggled.connect(self._on_tab_changed)
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

        for b in (self.btn_start, self.btn_cancel, self.btn_clear_list, self.btn_remove_selected):
            b.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)

        Config.initialize()
        self.pipe = None
        self._loader_thread: Optional[QtCore.QThread] = None
        self._loader_worker: Optional[ModelLoadWorker] = None
        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None
        self._is_running = False
        self._was_cancelled = False

        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        self.output.clear()
        self._append_log("🟢 Inicjalizacja — ładowanie modelu w tle…")
        self._start_model_loading_thread()
        self._update_buttons()

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

    def _on_tab_changed(self) -> None:
        if self.rb_files.isChecked():
            self.stack.setCurrentIndex(0)
        elif self.rb_live.isChecked():
            self.stack.setCurrentIndex(1)
        elif self.rb_down.isChecked():
            self.stack.setCurrentIndex(2)
        elif self.rb_settings.isChecked():
            self.stack.setCurrentIndex(3)

    def _on_src_add_clicked(self) -> None:
        text = self.src_edit.text().strip()
        if not text:
            self._append_log("ℹ️ Wpisz ścieżkę pliku lub adres URL.")
            return
        added, msg = self.file_list.add_entry(text)
        if added:
            self._append_log(f"✅ Dodano: {msg}")
            self.src_edit.clear()
        else:
            self._append_log(f"⚠️ Nie dodano: {msg}")
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
            Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.OUTPUT_DIR)))
        except Exception as e:
            self._append_log(f"❗ Nie udało się otworzyć folderu: {e}")

    def _on_start_clicked(self) -> None:
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
        self._transcribe_worker = TranscriptionWorker(pipe=self.pipe, entries=entries)
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

    @QtCore.pyqtSlot(str, str)
    def _on_conflict(self, stem: str, existing_dir: str) -> None:
        if self._conflict_apply_all_action:
            action = self._conflict_apply_all_action
            if action == "new":
                base = stem if self._conflict_apply_all_new_base is None else self._conflict_apply_all_new_base
                i = 1
                candidate = f"{base} ({i})"
                while FileManager.output_dir_for(candidate).exists():
                    i += 1
                    candidate = f"{base} ({i})"
                new_stem = candidate
            else:
                new_stem = ""
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided(action, new_stem)
            return

        action, new_stem, apply_all = ask_conflict(self, stem)
        if apply_all:
            self._conflict_apply_all_action = action
            self._conflict_apply_all_new_base = stem if action == "new" else None

        if self._transcribe_worker is not None:
            self._transcribe_worker.on_conflict_decided(action, new_stem)

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
        self.btn_clear_list.setToolTip("Usuń wszystkie pozycje z listy." if clear_enabled else ("Nie można czyścić listy podczas transkrypcji." if self._is_running else "Lista jest już pusta."))

        rem_enabled = has_selection and not self._is_running
        self.btn_remove_selected.setEnabled(rem_enabled)
        self.btn_remove_selected.setToolTip("Usuń zaznaczone pozycje." if rem_enabled else ("Zaznacz elementy na liście, aby je usunąć." if not has_selection else "Nie można usuwać podczas transkrypcji."))

        self.btn_src_add.setEnabled(not self._is_running)
        self.btn_add_files.setEnabled(not self._is_running)
        self.btn_add_folder.setEnabled(not self._is_running)
        self.src_edit.setEnabled(not self._is_running)
