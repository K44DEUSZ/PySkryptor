# controller/tasks/model_loader_task.py
from __future__ import annotations

from PyQt5 import QtCore

from model.services.transcription_service import TranscriptionService
from model.services.translation_service import TranslationService


class TranscriptionLoadWorker(QtCore.QObject):
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


class TranslationLoadWorker(QtCore.QObject):
    model_ready = QtCore.pyqtSignal(bool)
    model_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    @QtCore.pyqtSlot(object)
    def run(self, log_cb) -> None:
        try:
            svc = TranslationService()
            ok = bool(svc.warmup(log=log_cb))
            self.model_ready.emit(ok)
        except Exception as e:
            self.model_error.emit(str(e))
        finally:
            self.finished.emit()
