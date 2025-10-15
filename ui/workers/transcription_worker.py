# pyskryptor/ui/workers/transcription_worker.py
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional, Iterable, List, Tuple

from PyQt5 import QtCore

from core.config import Config
from core.services.download_service import DownloadService
from core.files.file_manager import FileManager
from core.transcription.text_postprocessor import TextPostprocessor


class TranscriptionWorker(QtCore.QObject):
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()
    conflict_check = QtCore.pyqtSignal(str, str)

    def __init__(
        self,
        files: Optional[Iterable[Path]] = None,
        pipe=None,
        entries: Optional[List[dict]] = None,
        parent: Optional[QtCore.QObject] = None,
    ):
        super().__init__(parent)
        self.pipe = pipe
        self._cancelled = False

        if entries is not None:
            self.entries = list(entries)
        else:
            self.entries = [{"type": "file", "value": str(Path(p))} for p in (files or [])]

        self._decision_loop: Optional[QtCore.QEventLoop] = None
        self._decision_result: Optional[Tuple[str, str]] = None

        self._downloader = DownloadService()

    def cancel(self) -> None:
        self._cancelled = True
        self.log.emit("⏹️ Żądanie anulowania – zatrzymywanie…")

    @QtCore.pyqtSlot(str, str)
    def on_conflict_decided(self, action: str, new_stem: str) -> None:
        self._decision_result = (action, new_stem)
        if self._decision_loop is not None:
            self._decision_loop.quit()

    def _should_abort(self) -> bool:
        return self._cancelled or (QtCore.QThread.currentThread().isInterruptionRequested())

    def _log_runtime_mode(self) -> None:
        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        dev_name = Config.DEVICE_FRIENDLY_NAME
        self.log.emit(
            f"🧠 Tryb: {mode}{f' ({dev_name})' if mode == 'GPU' else ''}, "
            f"dtype={dtype_name}, TF32={tf32}, źródła={len(self.entries)}"
        )

    def _ask_conflict(self, stem: str) -> Tuple[str, str]:
        existing_dir = str(FileManager.output_dir_for(stem))
        self._decision_result = None
        self._decision_loop = QtCore.QEventLoop()
        self.conflict_check.emit(stem, existing_dir)
        self._decision_loop.exec_()
        self._decision_loop = None
        if self._decision_result is None:
            return "skip", ""
        return self._decision_result

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._log_runtime_mode()

            asr = self.pipe
            if asr is None:
                self.log.emit("⚠️ Brak gotowego pipeline — przerwanie zadania.")
                self.finished.emit()
                return

            total = len(self.entries)
            for idx, entry in enumerate(self.entries, start=1):
                if self._should_abort():
                    break

                etype = (entry.get("type") or "").lower()
                value = entry.get("value") or ""

                if etype == "file":
                    p = Path(value)
                    if not (p.exists() and p.is_file()):
                        self.log.emit(f"⚠️ Pomijam: nie znaleziono pliku: {p}")
                        continue
                    base_stem = p.stem
                elif etype == "url":
                    self.log.emit(f"🌐 Analiza URL (bez pobierania): {value}")
                    try:
                        base_stem = self._downloader.peek_output_stem(value, log=self.log.emit) or "plik"
                    except Exception:
                        base_stem = "plik"
                else:
                    self.log.emit(f"⚠️ Nieznany typ wpisu: {etype}")
                    continue

                if self._should_abort():
                    break

                chosen_stem = base_stem
                out_dir = FileManager.output_dir_for(chosen_stem)
                if out_dir.exists():
                    self.log.emit(f"⚠️ Wykryto istniejący folder wyjściowy: {out_dir}")
                    action, new_stem = self._ask_conflict(base_stem)
                    if action == "skip":
                        self.log.emit("⏭️ Pominieto na żądanie użytkownika.")
                        self.progress.emit(int(idx * 100 / max(1, total)))
                        continue
                    elif action == "overwrite":
                        try:
                            FileManager.remove(chosen_stem)
                            self.log.emit("♻️ Nadpisywanie — usunięto poprzednią wersję.")
                        except Exception as e:
                            self.log.emit(f"❗ Nie udało się usunąć poprzedniej wersji: {e}")
                        chosen_stem = base_stem
                    elif action == "new":
                        chosen_stem = new_stem or FileManager.next_free_stem(base_stem)
                        self.log.emit(f"🆕 Tworzenie nowej wersji: {chosen_stem}")

                if self._should_abort():
                    break

                local_paths: List[Path] = []
                if etype == "file":
                    local_paths = [Path(value)]
                elif etype == "url":
                    self.log.emit(f"🌐 Pobieranie: {value}")
                    try:
                        def _dlog(m: str) -> None:
                            self.log.emit(m)
                        dl_paths = self._downloader.download(urls=[value], on_file_ready=None, log=_dlog)
                        local_paths = [Path(p) for p in dl_paths]
                        if not local_paths:
                            self.log.emit(f"❌ Błąd pobierania: Brak plików po pobraniu.")
                            self.progress.emit(int(idx * 100 / max(1, total)))
                            continue
                    except Exception as e:
                        self.log.emit(f"❌ Błąd pobierania: {e}")
                        self.progress.emit(int(idx * 100 / max(1, total)))
                        continue

                for p in local_paths:
                    if self._should_abort():
                        break
                    self.log.emit(f"🎧 Transkrypcja: {p.name}")
                    try:
                        result = asr(str(p), return_timestamps=True)
                    except Exception as e:
                        self.log.emit(f"❗ Błąd transkrypcji {p.name}: {e}")
                        continue

                    if self._should_abort():
                        break

                    text = result.get("text", "") if isinstance(result, dict) else str(result)
                    text = TextPostprocessor.clean(text)

                    try:
                        out_dir = FileManager.ensure_output(chosen_stem)
                        out_path = out_dir / "transcript.txt"
                        with out_path.open("w", encoding="utf-8") as f:
                            f.write(text)
                        self.log.emit(f"✅ Zapisano: {out_path}")
                    except Exception as e:
                        self.log.emit(f"❗ Błąd zapisu transkryptu dla {p.name}: {e}")

                self.progress.emit(int(idx * 100 / max(1, total)))

        finally:
            self.finished.emit()
