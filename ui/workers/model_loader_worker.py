# pyskryptor/ui/workers/model_loader_worker.py
from __future__ import annotations

from PyQt5 import QtCore

from core.services.transcription_service import TranscriptionService


class ModelLoadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    model_ready = QtCore.pyqtSignal(object)
    model_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            service = TranscriptionService()

            def _log(line: str) -> None:
                if "Model i pipeline gotowe" in (line or ""):
                    return
                self.progress_log.emit(line)

            service.build(log=_log)
            pipe = service.pipeline
            if pipe is None:
                self.model_error.emit("Pipeline nie został zainicjalizowany.")
            else:
                self.model_ready.emit(pipe)
        except Exception as e:
            self.model_error.emit(str(e))
        finally:
            self.finished.emit()
