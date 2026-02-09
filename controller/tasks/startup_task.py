from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from PyQt5 import QtCore

from model.io.file_manager import FileManager

ProgressCb = Callable[[int], None]
TaskFn = Callable[[ProgressCb, Dict[str, Any]], None]


@dataclass(frozen=True)
class StartupTask:
    label: str
    weight: int
    fn: TaskFn


def build_startup_tasks(config_cls: Any, snap: Any, labels: Dict[str, str]) -> List[StartupTask]:
    def init_runtime(progress: ProgressCb, ctx: Dict[str, Any]) -> None:
        config_cls.initialize_from_snapshot(snap)
        ctx["settings_snapshot"] = snap
        progress(100)

    def ensure_dirs(progress: ProgressCb, ctx: Dict[str, Any]) -> None:
        config_cls.ensure_dirs()
        # Keep temp dirs predictable: clear leftovers from previous sessions.
        try:
            FileManager.clear_temp_dir(config_cls.DOWNLOADS_TMP_DIR)
            FileManager.clear_temp_dir(config_cls.TRANSCRIPTIONS_TMP_DIR)
            config_cls.DOWNLOADS_TMP_DIR.mkdir(parents=True, exist_ok=True)
            config_cls.TRANSCRIPTIONS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        progress(100)

    def setup_ffmpeg(progress: ProgressCb, ctx: Dict[str, Any]) -> None:
        config_cls.setup_ffmpeg_on_path()
        progress(100)

    return [
        StartupTask(label=labels.get("init", "Initialize"), weight=2, fn=init_runtime),
        StartupTask(label=labels.get("dirs", "Prepare folders"), weight=1, fn=ensure_dirs),
        StartupTask(label=labels.get("ffmpeg", "Prepare FFmpeg"), weight=1, fn=setup_ffmpeg),
    ]


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
                    pct_i = max(0, min(100, int(pct)))
                    overall = int(((done + (w * pct_i / 100.0)) / total) * 100.0)
                    self.progress.emit(max(0, min(100, overall)))

                t.fn(phase_progress, self._ctx)

                done += w
                self.progress.emit(int((done / total) * 100.0))

            self.progress.emit(100)
            self.ready.emit(self._ctx)

        except Exception as e:
            self.failed.emit(str(e))
