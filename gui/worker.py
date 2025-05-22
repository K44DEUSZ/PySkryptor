from PyQt5.QtCore import QObject, pyqtSignal
from pathlib import Path
from shutil import copy2

from core.config import Config
from core.downloader import Downloader
from core.transcription_processor import TranscriptionProcessor
from core.file_manager import FileManager

class Worker(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, pipe, mode: str, urls=None, files=None):
        super().__init__()
        self.pipe = pipe
        self.mode = mode
        self.urls = urls or []
        self.files = files or []
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        self._log = self.log.emit

        def process_file(file_path: Path):
            if self.cancelled:
                return

            output_dir = Config.OUTPUT_DIR / file_path.stem
            if output_dir.exists():
                self._log(f"⏭️ Transkrypcja dla pliku '{file_path.name}' już istnieje — pomijam.")
                return

            try:
                self._log(f"🔍 Przetwarzanie pliku: {file_path}")
                output_dir.mkdir(parents=True, exist_ok=True)

                source = file_path
                if not source.exists():
                    self._log(f"❌ Źródłowy plik nie istnieje: {source}")
                    return

                self._log(f"🧠 Rozpoczynam transkrypcję: {source}")
                result = self.pipe(
                    str(source),
                    chunk_length_s=30,
                    stride_length_s=5,
                    generate_kwargs={"language": Config.LANGUAGE}
                )

                text = TranscriptionProcessor.clean(result["text"].strip())
                (output_dir / f"{file_path.stem}.txt").write_text(text, encoding="utf-8")

                copy2(source, output_dir / source.name)
                self._log(f"✅ Zakończono transkrypcję pliku: {file_path.name}")

            except Exception as e:
                self._log(f"❌ Błąd podczas przetwarzania pliku {file_path.name}: {e}")

            try:
                if source.exists():
                    source.unlink()
                    self._log(f"🗑️ Usunięto plik tymczasowy: {source.name}")
            except Exception as e:
                self._log(f"⚠️ Nie udało się usunąć pliku tymczasowego {source.name}: {e}")

        if self.mode == "url":
            def on_file_ready(path: Path):
                process_file(path)

            Downloader.download(self.urls, on_file_ready=on_file_ready, log=self._log)

        else:
            copied_files = []
            for f in self.files:
                path = Path(f)
                if FileManager.should_skip_local(path, log=self._log):
                    continue
                if not path.exists():
                    self._log(f"❌ Plik nie istnieje: {path}")
                    continue
                if path.suffix.lower() not in Config.AUDIO_EXT + Config.VIDEO_EXT:
                    self._log(f"❌ Nieobsługiwane rozszerzenie pliku: {path.name}")
                    continue
                try:
                    copied = FileManager.copy_audio_only(f, log=self._log)
                    copied_files.append(copied)
                    self._log(f"📥 Skopiowano: {copied.name}")
                except Exception as e:
                    self._log(f"❌ Błąd kopiowania pliku {path.name}: {e}")

            tasks = FileManager.filter_media(copied_files)
            total = len(tasks)
            for index, file_path in enumerate(tasks, start=1):
                if self.cancelled:
                    break
                process_file(file_path)
                self.progress.emit(int(index / total * 100))

        self.finished.emit()
