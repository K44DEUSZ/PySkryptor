from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QPushButton, QLabel,
    QFileDialog, QProgressBar, QRadioButton, QButtonGroup, QHBoxLayout
)
from PyQt5.QtCore import Qt, QThread
from pathlib import Path

from core.config import Config
from core.model_manager import ModelManager
from gui.file_drop_list import FileDropList
from gui.worker import Worker

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PySkryptor")
        self.setGeometry(100, 100, 600, 500)

        self.mode_group = QButtonGroup(self)
        self.radio_file = QRadioButton("Plik lokalny")
        self.radio_url = QRadioButton("Adres URL")
        self.radio_file.setChecked(True)
        self.mode_group.addButton(self.radio_url)
        self.mode_group.addButton(self.radio_file)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Tryb:"))
        mode_layout.addWidget(self.radio_url)
        mode_layout.addWidget(self.radio_file)

        self.url_input = QTextEdit()
        self.url_input.setPlaceholderText("Wklej adresy URL, po jednym w linii…")
        self.file_list = FileDropList()
        self.drag_hint = QLabel("Przeciągnij pliki lub użyj przycisku")
        self.drag_hint.setAlignment(Qt.AlignCenter)
        self.drag_hint.setStyleSheet("color:gray;font-style:italic")
        self.btn_add = QPushButton("Dodaj pliki")

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.output = QTextEdit()
        self.output.setReadOnly(True)

        self.btn_start = QPushButton("Rozpocznij transkrypcję")
        self.btn_cancel = QPushButton("Anuluj")
        self.btn_cancel.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_layout)
        layout.addWidget(QLabel("Źródło danych:"))
        layout.addWidget(self.url_input)
        layout.addWidget(self.btn_add)
        layout.addWidget(self.drag_hint)
        layout.addWidget(self.file_list)
        layout.addWidget(QLabel("Postęp:"))
        layout.addWidget(self.progress)
        layout.addWidget(QLabel("Log:"))
        layout.addWidget(self.output)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_cancel)

        self.radio_url.toggled.connect(self._toggle_mode)
        self.btn_add.clicked.connect(self._add_files)
        self.btn_start.clicked.connect(self._start_transcription)
        self.btn_cancel.clicked.connect(self._cancel_transcription)

        manager = ModelManager()
        manager.load()
        self.pipe = manager.get_pipeline()

        self._toggle_mode()

    def _toggle_mode(self):
        url_mode = self.radio_url.isChecked()
        self.url_input.setVisible(url_mode)
        self.btn_add.setVisible(not url_mode)
        self.drag_hint.setVisible(not url_mode)
        self.file_list.setVisible(not url_mode)

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Wybierz pliki")
        for f in files:
            path = Path(f)
            if path.suffix.lower() in Config.AUDIO_EXT + Config.VIDEO_EXT:
                self.file_list.add_file(str(path))

    def _start_transcription(self):
        self.output.clear()
        self.progress.setValue(0)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.output.append("🟢 Rozpoczynam transkrypcję...")

        if self.radio_url.isChecked():
            urls = [u.strip() for u in self.url_input.toPlainText().splitlines() if u.strip()]
            args = {'pipe': self.pipe, 'mode': 'url', 'urls': urls}
        else:
            files = self.file_list.get_file_paths()
            args = {'pipe': self.pipe, 'mode': 'file', 'files': files}

        self.thread = QThread()
        self.worker = Worker(**args)
        self.worker.moveToThread(self.thread)

        self.worker.log.connect(self.output.append)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self._on_finished)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _cancel_transcription(self):
        if hasattr(self, 'worker'):
            self.worker.cancel()
        self.output.append("🛑 Anulowano przez użytkownika.")
        self.btn_cancel.setEnabled(False)

    def _on_finished(self):
        self.output.append("✅ Transkrypcja zakończona.")
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
