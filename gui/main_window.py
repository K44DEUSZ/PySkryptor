# gui/main_window.py
# UI: Jedna lista źródeł (lokalne + URL). „Dodaj” przyjmuje ścieżkę lub URL.
# Zakładki (radio): Transkrypcja plików (aktywny), Transkrypcja live / Downloader / Ustawienia (placeholdery).
# Start uruchamia Workera z listą entries z FileDropList.
# Dodatki:
# - Filtr zdublowanego logu gotowości.
# - Przyciski Start/Anuluj/Wyczyść/Usuń mają dynamiczne podpowiedzi i są blokowane zależnie od stanu.
# - „Anuluj” pyta o potwierdzenie i natychmiast zabija wątek (terminate) po potwierdzeniu.
# - Dialog konfliktu ma opcję „Zastosuj dla pozostałych”; decyzja może być stosowana automatycznie.
# - Po anulowaniu resetowany jest wskaźnik postępu do 0%.

from pathlib import Path
from typing import Optional, List

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config import Config
from core.model_manager import ModelManager
from gui.worker import Worker
from gui.file_drop_list import FileDropList


class ModelLoadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    model_ready = QtCore.pyqtSignal(object)  # gotowy pipeline
    model_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            manager = ModelManager()

            def _log(line: str) -> None:
                # filtrujemy zdublowany komunikat z ModelManagera
                if "Model i pipeline gotowe" in (line or ""):
                    return
                self.progress_log.emit(line)

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


class MainWindow(QtWidgets.QMainWindow):
    log_signal = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PySkryptor")
        self.resize(1100, 720)

        # ---------- Central ----------
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        # ---------- Zakładki (radio) ----------
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

        # ---------- Stos widoków ----------
        self.stack = QtWidgets.QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        # === Transkrypcja plików ===
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
        self.btn_remove_selected = QtWidgets.QPushButton("Usuń zaznaczone")
        self.btn_clear_list = QtWidgets.QPushButton("Wyczyść listę")
        self.btn_open_output = QtWidgets.QPushButton("Otwórz folder transkrypcji")
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

        # === Placeholdery ===
        for title in ("🛠️ Transkrypcja live — w przygotowaniu.",
                      "🛠️ Downloader — w przygotowaniu.",
                      "🛠️ Ustawienia — w przygotowaniu."):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lay.addWidget(QtWidgets.QLabel(title))
            lay.addStretch(1)
            self.stack.addWidget(page)

        # ---------- Sygnały ----------
        self.log_signal.connect(self._append_log)
        self.rb_files.toggled.connect(self._on_tab_changed)
        self.rb_live.toggled.connect(self._on_tab_changed)
        self.rb_down.toggled.connect(self._on_tab_changed)
        self.rb_settings.toggled.connect(self._on_tab_changed)

        self.btn_src_add.clicked.connect(self._on_src_add_clicked)
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_remove_selected.clicked.connect(self.file_list.remove_selected)
        self.btn_clear_list.clicked.connect(self.file_list.clear)
        self.btn_open_output.clicked.connect(self._on_open_output_folder)
        self.file_list.files_changed.connect(self._on_files_changed)
        self.file_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        # Tooltips na wyłączonych kontrolkach
        for b in (self.btn_start, self.btn_cancel, self.btn_clear_list, self.btn_remove_selected):
            b.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)

        # ---------- Init ----------
        Config.initialize()
        self.pipe = None
        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[Worker] = None
        self._is_running = False              # stan przetwarzania
        self._was_cancelled = False           # czy przerwano „twardo” – do resetu paska
        self._conflict_apply_all_action: Optional[str] = None   # 'skip' | 'new' | 'overwrite'
        self._conflict_apply_all_new_base: Optional[str] = None

        self.output.clear()
        self._append_log("🟢 Inicjalizacja — ładowanie modelu w tle…")
        self._start_model_loading_thread()
        self._update_buttons()  # początkowy stan

    # ---- Model loading thread ----

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

    # ---- Log ----

    @QtCore.pyqtSlot(str)
    def _append_log(self, text: str) -> None:
        try:
            self.output.append(text)
        except Exception:
            print(text)

    # ---- Model ready/error ----

    @QtCore.pyqtSlot(object)
    def _on_model_ready(self, pipeline_obj) -> None:
        self.pipe = pipeline_obj
        self._append_log("✅ Model załadowany — możesz rozpocząć transkrypcję")
        self._update_buttons()

    @QtCore.pyqtSlot(str)
    def _on_model_error(self, msg: str) -> None:
        self._append_log(f"❌ Błąd ładowania modelu: {msg}")
        self._update_buttons()

    # ---- Tabs ----

    def _on_tab_changed(self) -> None:
        if self.rb_files.isChecked():
            self.stack.setCurrentIndex(0)
        elif self.rb_live.isChecked():
            self.stack.setCurrentIndex(1)
        elif self.rb_down.isChecked():
            self.stack.setCurrentIndex(2)
        elif self.rb_settings.isChecked():
            self.stack.setCurrentIndex(3)

    # ---- List operations ----

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

    def _on_files_changed(self) -> None:
        self._update_buttons()

    def _on_selection_changed(self) -> None:
        self._update_buttons()

    # ---- Transcription ----

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
        self._update_buttons()
        self.progress.setValue(0)
        self._append_log("▶️ Start transkrypcji…")

        self._transcribe_thread = QtCore.QThread(self)
        self._transcribe_worker = Worker(
            model_manager=None,
            files=None,
            pipe=self.pipe,
            entries=entries,
        )
        self._transcribe_worker.moveToThread(self._transcribe_thread)

        # konflikty i logi
        self._transcribe_worker.conflict_check.connect(self._on_worker_conflict_check)
        self._transcribe_thread.started.connect(self._transcribe_worker.run)
        self._transcribe_worker.log.connect(self._append_log)
        self._transcribe_worker.progress.connect(self.progress.setValue)
        self._transcribe_worker.finished.connect(self._on_transcribe_finished)

        self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
        self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
        self._transcribe_thread.finished.connect(self._on_transcribe_thread_finished)
        self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)

        self._transcribe_thread.start()

    def _hard_cancel(self) -> None:
        """
        Natychmiastowo ubija trwającą transkrypcję.
        Resetuje także pasek postępu do 0%.
        """
        if self._transcribe_thread is None:
            return
        self._append_log("🛑 Twarde przerwanie — zatrzymywanie wątku…")
        self._was_cancelled = True
        try:
            # Najpierw łagodne anulowanie
            if self._transcribe_worker is not None:
                self._transcribe_worker.cancel()
            # Natychmiastowe ubicie wątku
            self._transcribe_thread.terminate()
            self._transcribe_thread.wait(2000)
        except Exception as e:
            self._append_log(f"❗ Błąd przy twardym przerwaniu: {e}")
        finally:
            self._transcribe_thread = None
            self._transcribe_worker = None
            self._is_running = False
            self.progress.setValue(0)  # <-- reset paska postępu natychmiast po anulowaniu
            self._append_log("⏹️ Zatrzymano.")
            self._update_buttons()

    def _on_cancel_clicked(self) -> None:
        if not self._is_running:
            return
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Przerwać transkrypcję?")
        box.setText("Czy na pewno chcesz natychmiast przerwać bieżącą transkrypcję?\n\n"
                    "To przerwie aktualnie przetwarzany plik i pominie pozostałe.")
        yes_btn = box.addButton("Tak, przerwij teraz", QtWidgets.QMessageBox.DestructiveRole)
        no_btn = box.addButton("Nie", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(no_btn)
        box.exec_()
        if box.clickedButton() is yes_btn:
            self._hard_cancel()

    def _on_transcribe_finished(self) -> None:
        self._append_log("✅ Zakończono transkrypcję.")

    def _on_transcribe_thread_finished(self) -> None:
        # Zakończenie wątku – po anulowaniu już zresetowaliśmy pasek,
        # ale dodatkowo zabezpieczamy się tutaj:
        if self._was_cancelled:
            self.progress.setValue(0)  # <-- zabezpieczenie w razie innej kolejności sygnałów
            self._was_cancelled = False

        self._transcribe_thread = None
        self._transcribe_worker = None
        self._is_running = False
        self._update_buttons()

    # --- Dialog konfliktu ---

    @QtCore.pyqtSlot(str, str)
    def _on_worker_conflict_check(self, stem: str, existing_dir: str) -> None:
        # Jeśli mamy globalną decyzję – zastosuj bez pytania
        if self._conflict_apply_all_action:
            action = self._conflict_apply_all_action
            if action == "new":
                base = stem if self._conflict_apply_all_new_base is None else self._conflict_apply_all_new_base
                i = 1
                candidate = f"{base} ({i})"
                while (Config.OUTPUT_DIR / candidate).exists():
                    i += 1
                    candidate = f"{base} ({i})"
                new_stem = candidate
            else:
                new_stem = ""
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided(action, new_stem)
            return

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Istniejący wynik")
        box.setText(
            f"Istnieje już folder wynikowy dla „{stem}”.\n\n"
            f"{existing_dir}\n\nJak chcesz postąpić?"
        )
        skip_btn = box.addButton("Pomiń", QtWidgets.QMessageBox.RejectRole)
        new_btn = box.addButton("Utwórz wersję (1)", QtWidgets.QMessageBox.ActionRole)
        overwrite_btn = box.addButton("Nadpisz", QtWidgets.QMessageBox.DestructiveRole)
        box.setDefaultButton(new_btn)

        # Checkbox „Zastosuj dla pozostałych”
        apply_all_cb = QtWidgets.QCheckBox("Zastosuj dla pozostałych")
        box.setCheckBox(apply_all_cb)

        box.exec_()

        if box.clickedButton() is skip_btn:
            action, new_stem = "skip", ""
        elif box.clickedButton() is overwrite_btn:
            action, new_stem = "overwrite", ""
        else:
            base = stem
            i = 1
            candidate = f"{base} ({i})"
            while (Config.OUTPUT_DIR / candidate).exists():
                i += 1
                candidate = f"{base} ({i})"
            action, new_stem = "new", candidate

        # Zapamiętaj decyzję globalnie, jeśli zaznaczono
        if apply_all_cb.isChecked():
            self._conflict_apply_all_action = action
            self._conflict_apply_all_new_base = stem if action == "new" else None

        if self._transcribe_worker is not None:
            self._transcribe_worker.on_conflict_decided(action, new_stem)

    # --- Otwórz folder transkrypcji ---

    def _on_open_output_folder(self) -> None:
        try:
            Config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.OUTPUT_DIR)))
        except Exception as e:
            self._append_log(f"❗ Nie udało się otworzyć folderu: {e}")

    # ---- Przyciski (stany + podpowiedzi) ----

    def _update_buttons(self) -> None:
        has_items = len(self.file_list.get_entries()) > 0
        has_selection = len(self.file_list.selectedItems()) > 0

        # Start: tylko gdy model gotowy, są pozycje i nie trwa praca
        start_enabled = (self.pipe is not None) and has_items and not self._is_running
        self.btn_start.setEnabled(start_enabled)

        # Budowanie podpowiedzi „dlaczego nie działa”
        start_tip: List[str] = []
        if self.pipe is None:
            start_tip.append("Model nie jest jeszcze gotowy.")
        if not has_items:
            start_tip.append("Nie masz dodanych jeszcze żadnych plików/URL-i.")
        if self._is_running:
            start_tip.append("Transkrypcja już trwa.")
        self.btn_start.setToolTip(" ".join(start_tip) if not start_enabled else "Rozpocznij transkrypcję wybranych pozycji.")

        # Anuluj: tylko w trakcie pracy
        self.btn_cancel.setEnabled(self._is_running)
        self.btn_cancel.setToolTip("Zatrzymaj natychmiast trwającą transkrypcję." if self._is_running else "Brak aktywnej transkrypcji do anulowania.")

        # Wyczyść listę: tylko gdy są pozycje i nie trwa praca
        clear_enabled = has_items and not self._is_running
        self.btn_clear_list.setEnabled(clear_enabled)
        self.btn_clear_list.setToolTip("Usuń wszystkie pozycje z listy." if clear_enabled else ("Nie można czyścić listy podczas transkrypcji." if self._is_running else "Lista jest już pusta."))

        # Usuń zaznaczone: tylko gdy coś zaznaczone i nie trwa praca
        rem_enabled = has_selection and not self._is_running
        self.btn_remove_selected.setEnabled(rem_enabled)
        self.btn_remove_selected.setToolTip("Usuń zaznaczone pozycje." if rem_enabled else ("Zaznacz elementy na liście, aby je usunąć." if not has_selection else "Nie można usuwać podczas transkrypcji."))

        # „Dodaj”/„Dodaj pliki/folder” zablokowane w trakcie pracy
        self.btn_src_add.setEnabled(not self._is_running)
        self.btn_add_files.setEnabled(not self._is_running)
        self.btn_add_folder.setEnabled(not self._is_running)
        self.src_edit.setEnabled(not self._is_running)
