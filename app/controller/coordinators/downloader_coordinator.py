# app/controller/coordinators/downloader_coordinator.py
from __future__ import annotations

from app.controller.contracts import DownloaderPanelViewProtocol

from PyQt5 import QtCore

from app.controller.workers.download_worker import DownloadWorker
from app.controller.workers.task_thread_runner import TaskThreadRunner

class DownloaderCoordinator(QtCore.QObject):
    """Owns Downloader-panel workers and centralizes duplicate/probe routing."""

    busy_changed = QtCore.pyqtSignal(bool)
    probe_busy_changed = QtCore.pyqtSignal(str, bool)
    download_busy_changed = QtCore.pyqtSignal(bool)

    probe_meta_ready = QtCore.pyqtSignal(str, dict)
    probe_failed = QtCore.pyqtSignal(str, str, dict)

    progress_pct = QtCore.pyqtSignal(int)
    stage_changed = QtCore.pyqtSignal(str)
    duplicate_check = QtCore.pyqtSignal(str, str)
    download_finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str, dict)
    cancelled = QtCore.pyqtSignal()
    finished = QtCore.pyqtSignal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._probe_runners: dict[str, TaskThreadRunner] = {}
        self._probe_workers: dict[str, DownloadWorker] = {}

        self._download_runner = TaskThreadRunner(self)
        self._download_worker: DownloadWorker | None = None
        self._view: DownloaderPanelViewProtocol | None = None

    def bind_view(self, panel: DownloaderPanelViewProtocol) -> None:
        if self._view is panel:
            return
        previous = self._view
        if previous is not None:
            for signal, slot in (
                (self.probe_meta_ready, previous.on_probe_ready),
                (self.probe_failed, previous.on_probe_error),
                (self.progress_pct, previous.on_progress_pct),
                (self.stage_changed, previous.on_stage_changed),
                (self.duplicate_check, previous.on_duplicate_check),
                (self.download_finished, previous.on_download_finished),
                (self.failed, previous.on_download_error),
                (self.cancelled, previous.on_download_cancelled),
                (self.finished, previous.on_download_cycle_finished),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._view = panel
        self.probe_meta_ready.connect(panel.on_probe_ready)
        self.probe_failed.connect(panel.on_probe_error)
        self.progress_pct.connect(panel.on_progress_pct)
        self.stage_changed.connect(panel.on_stage_changed)
        self.duplicate_check.connect(panel.on_duplicate_check)
        self.download_finished.connect(panel.on_download_finished)
        self.failed.connect(panel.on_download_error)
        self.cancelled.connect(panel.on_download_cancelled)
        self.finished.connect(panel.on_download_cycle_finished)

    def is_probe_running(self, job_key: str | None = None) -> bool:
        if job_key is None:
            return bool(self._probe_runners)
        runner = self._probe_runners.get(str(job_key))
        return bool(runner is not None and runner.is_running())

    def is_downloading(self) -> bool:
        return self._download_runner.is_running()

    def is_busy(self) -> bool:
        return self.is_probe_running() or self.is_downloading()

    def start_probe(self, *, job_key: str, url: str) -> DownloadWorker | None:
        key = str(job_key or "probe").strip() or "probe"
        runner = self._probe_runners.get(key)
        if runner is not None and runner.is_running():
            return self._probe_workers.get(key)

        runner = TaskThreadRunner(self)
        worker = DownloadWorker(action="probe", url=url)

        self._probe_runners[key] = runner
        self._probe_workers[key] = worker

        self.probe_busy_changed.emit(key, True)
        self.busy_changed.emit(True)

        def _connect(wk: DownloadWorker, *, _job_key: str = key) -> None:
            def _emit_probe_ready(meta: dict[str, object]) -> None:
                self.probe_meta_ready.emit(_job_key, dict(meta or {}))

            def _emit_probe_failed(err_key: str, params: dict[str, object]) -> None:
                self.probe_failed.emit(_job_key, str(err_key), dict(params or {}))

            wk.meta_ready.connect(_emit_probe_ready)
            wk.download_error.connect(_emit_probe_failed)
            wk.failed.connect(_emit_probe_failed)

        def _done(*, _job_key: str = key) -> None:
            self._probe_runners.pop(_job_key, None)
            self._probe_workers.pop(_job_key, None)
            self.probe_busy_changed.emit(_job_key, False)
            self.busy_changed.emit(self.is_busy())

        return runner.start(worker, connect=_connect, on_finished=_done)

    def cancel_probe(self, job_key: str) -> None:
        runner = self._probe_runners.get(str(job_key or ""))
        if runner is not None:
            runner.cancel()

    def cancel_all_probes(self) -> None:
        for runner in list(self._probe_runners.values()):
            runner.cancel()

    def start_download(
        self,
        *,
        url: str,
        kind: str,
        quality: str,
        ext: str,
        audio_lang: str | None = None,
    ) -> DownloadWorker | None:
        if self._download_runner.is_running():
            return self._download_worker

        worker = DownloadWorker(
            action="download",
            url=url,
            kind=kind,
            quality=quality,
            ext=ext,
            audio_lang=audio_lang,
        )
        self._download_worker = worker

        self.download_busy_changed.emit(True)
        self.busy_changed.emit(True)

        def _connect(wk: DownloadWorker) -> None:
            wk.progress_pct.connect(self.progress_pct)
            wk.stage_changed.connect(self.stage_changed)
            wk.duplicate_check.connect(self.duplicate_check)
            wk.download_finished.connect(self.download_finished)
            wk.download_error.connect(self.failed)
            wk.failed.connect(self.failed)
            wk.cancelled.connect(self.cancelled)

        def _done() -> None:
            self._download_worker = None
            self.download_busy_changed.emit(False)
            self.busy_changed.emit(self.is_busy())
            self.finished.emit()

        return self._download_runner.start(worker, connect=_connect, on_finished=_done)

    def cancel_download(self) -> None:
        self._download_runner.cancel()

    def resolve_duplicate(self, action: str, new_name: str = "") -> None:
        wk = self._download_worker
        if wk is None:
            return
        try:
            wk.on_duplicate_decided(action, new_name)
        except (AttributeError, RuntimeError, TypeError):
            return
