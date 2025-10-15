# gui/worker.py
# Worker do wykonywania transkrypcji w wątku roboczym.
# Dostosowany do pełnej transkrypcji z Whisper: bez ręcznego chunkowania;
# długie nagrania wymagają return_timestamps=True (long-form generation).

from pathlib import Path
from typing import Optional, Iterable

from PyQt5 import QtCore

from core.config import Config


class Worker(QtCore.QObject):
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        model_manager=None,
        files: Optional[Iterable[Path]] = None,
        pipe=None,
        parent: Optional[QtCore.QObject] = None,
    ):
        super().__init__(parent)
        self.model_manager = model_manager
        self.files = list(files or [])
        self._cancelled = False

        # Pipeline może być wstrzyknięty bezpośrednio (rekomendowane po model_ready)
        self.pipe = pipe

    def cancel(self) -> None:
        self._cancelled = True
        self.log.emit("⏹️ Przerwano na żądanie.")

    def _log_runtime_mode(self) -> None:
        mode = "GPU" if Config.DEVICE.type == "cuda" else "CPU"
        dtype_name = str(Config.DTYPE).split(".")[-1]
        tf32 = "ON" if Config.TF32_ENABLED else "OFF"
        dev_name = Config.DEVICE_FRIENDLY_NAME
        msg = (
            f"🧠 Tryb: {mode}{f' ({dev_name})' if mode == 'GPU' else ''}, "
            f"dtype={dtype_name}, TF32={tf32}, chunking=auto (Whisper)"
        )
        self.log.emit(msg)

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            self._log_runtime_mode()

            # Preferuj self.pipe (po asynchronicznym ładowaniu); fallback do model_manager.pipe
            asr_pipe = self.pipe
            if asr_pipe is None and self.model_manager is not None:
                asr_pipe = getattr(self.model_manager, "pipe", None)

            if asr_pipe is None:
                self.log.emit("⚠️ Brak gotowego pipeline — przerwanie zadania.")
                self.finished.emit()
                return

            total = len(self.files)
            for idx, path in enumerate(self.files, start=1):
                if self._cancelled:
                    break

                p = Path(path)
                if not p.exists():
                    self.log.emit(f"⚠️ Pomijam: nie znaleziono pliku: {p}")
                    continue

                self.log.emit(f"🎧 Transkrypcja: {p.name}")

                try:
                    # Długie pliki: wymagany return_timestamps=True (long-form)
                    result = asr_pipe(
                        str(p),
                        return_timestamps=True,
                    )
                except Exception as e:
                    self.log.emit(f"❗ Błąd transkrypcji {p.name}: {e}")
                    continue

                text = ""
                if isinstance(result, dict) and "text" in result:
                    text = result["text"]
                else:
                    text = str(result)

                try:
                    from core.config import Config as _C
                    out_dir = (_C.OUTPUT_DIR / p.stem)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / "transcript.txt"
                    with out_path.open("w", encoding="utf-8") as f:
                        f.write(text)
                    self.log.emit(f"✅ Zapisano: {out_path}")
                except Exception as e:
                    self.log.emit(f"❗ Błąd zapisu transkryptu dla {p.name}: {e}")

                self.progress.emit(int(idx * 100 / max(1, total)))

        finally:
            if self._cancelled:
                self.log.emit("🛑 Praca przerwana.")
            self.finished.emit()
