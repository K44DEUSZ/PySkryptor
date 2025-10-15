# pyskryptor/core/utils/concurrency.py
from __future__ import annotations

from PyQt5 import QtCore


class CancellationToken(QtCore.QObject):
    cancelled = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._flag = False

    def cancel(self) -> None:
        if not self._flag:
            self._flag = True
            self.cancelled.emit()

    @property
    def is_cancelled(self) -> bool:
        return self._flag
