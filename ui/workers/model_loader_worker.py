# ui/workers/model_loader_worker.py
from __future__ import annotations

from PyQt5 import QtCore

from core.transcription.model_loader import ModelLoader


class ModelLoadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    model_ready = QtCore.pyqtSignal(object)
    model_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            loader = ModelLoader()
            pipe = loader.load(log=self.progress_log.emit)
            self.model_ready.emit(pipe)
        except Exception as e:
            self.model_error.emit(str(e))
        finally:
            self.finished.emit()
