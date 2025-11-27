# ui/utils/concurrency.py
from __future__ import annotations

from PyQt5 import QtCore


class CancellationToken(QtCore.QObject):
    """Tiny cancellation flag that also emits a Qt signal when cancelled."""
    cancelled = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._flag: bool = False

    def cancel(self) -> None:
        if not self._flag:
            self._flag = True
            self.cancelled.emit()

    def reset(self) -> None:
        self._flag = False

    @property
    def is_cancelled(self) -> bool:
        return self._flag

    def wait_for_cancelled(self, timeout_ms: int = -1) -> bool:
        """Block the current event loop until cancelled or timeout_ms elapses."""
        if self._flag:
            return True
        loop = QtCore.QEventLoop()
        self.cancelled.connect(loop.quit)
        if timeout_ms >= 0:
            QtCore.QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec_()
        return self._flag
