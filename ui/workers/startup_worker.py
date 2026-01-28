# ui/workers/startup_worker.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from PyQt5 import QtCore

ProgressCb = Callable[[int], None]
TaskFn = Callable[[ProgressCb, Dict[str, Any]], None]


@dataclass(frozen=True)
class StartupTask:
    label: str
    weight: int
    fn: TaskFn


class StartupWorker(QtCore.QObject):
    status = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int)
    failed = QtCore.pyqtSignal(str)
    ready = QtCore.pyqtSignal(dict)

    def __init__(self, tasks: List[StartupTask]) -> None:
        super().__init__()
        self._tasks = tasks
        self._ctx: Dict[str, Any] = {}

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            total = sum(max(1, int(t.weight)) for t in self._tasks) or 1
            done = 0

            self.progress.emit(0)

            for t in self._tasks:
                w = max(1, int(t.weight))
                self.status.emit(t.label)

                def phase_progress(pct: int) -> None:
                    pct = max(0, min(100, int(pct)))
                    overall = int(((done + (w * pct / 100.0)) / total) * 100.0)
                    self.progress.emit(max(0, min(100, overall)))

                t.fn(phase_progress, self._ctx)

                done += w
                self.progress.emit(int((done / total) * 100.0))

            self.progress.emit(100)
            self.ready.emit(self._ctx)

        except Exception as e:
            self.failed.emit(str(e))