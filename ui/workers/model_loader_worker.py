# ui/workers/model_loader_worker.py
from __future__ import annotations

from PyQt5 import QtCore

from core.services.transcription_service import TranscriptionService


class ModelLoadWorker(QtCore.QObject):
    """Background worker that builds the ASR pipeline off the GUI thread."""

    model_ready = QtCore.pyqtSignal(object)
    model_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            svc = TranscriptionService()
            svc.build(log=lambda _m: None)
            self.model_ready.emit(svc.pipeline)
        except Exception as e:
            self.model_error.emit(str(e))
        finally:
            self.finished.emit()
