# app/controller/coordinators/downloader_coordinator.py
from __future__ import annotations

from PyQt5 import QtCore

from app.controller.panel_protocols import DownloaderPanelViewProtocol
from app.controller.support.expansion_flow import start_manual_input_expansion
from app.controller.support.panel_support import rebind_downloader_panel_view
from app.controller.workers.download_worker import DownloadWorker
from app.controller.workers.source_expansion_worker import SourceExpansionWorker
from app.controller.workers.worker_runner import WorkerRunner


class DownloaderCoordinator(QtCore.QObject):
    """Owns Downloader-panel workers and centralizes duplicate/probe routing."""

    busy_changed = QtCore.pyqtSignal(bool)
    probe_busy_changed = QtCore.pyqtSignal(str, bool)
    download_busy_changed = QtCore.pyqtSignal(bool)

    probe_meta_ready = QtCore.pyqtSignal(str, dict)
    probe_failed = QtCore.pyqtSignal(str, str, dict)
    access_intervention_required = QtCore.pyqtSignal(str, dict)
    expansion_busy_changed = QtCore.pyqtSignal(bool)
    expansion_status_changed = QtCore.pyqtSignal(str, dict)
    expansion_ready = QtCore.pyqtSignal(object)
    expansion_failed = QtCore.pyqtSignal(str, dict)

    progress_pct = QtCore.pyqtSignal(int)
    stage_changed = QtCore.pyqtSignal(str)
    duplicate_check = QtCore.pyqtSignal(str, str)
    download_finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str, dict)
    cancelled = QtCore.pyqtSignal()
    finished = QtCore.pyqtSignal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._probe_runners: dict[str, WorkerRunner] = {}
        self._probe_workers: dict[str, DownloadWorker] = {}

        self._download_runner = WorkerRunner(self)
        self._download_worker: DownloadWorker | None = None
        self._expansion_runner = WorkerRunner(self)
        self._expansion_worker: SourceExpansionWorker | None = None
        self._view: DownloaderPanelViewProtocol | None = None

    def bind_view(self, panel: DownloaderPanelViewProtocol) -> None:
        if self._view is panel:
            return
        rebind_downloader_panel_view(
            previous_view=self._view,
            new_view=panel,
            probe_meta_ready=self.probe_meta_ready,
            probe_failed=self.probe_failed,
            access_intervention_required=self.access_intervention_required,
            expansion_busy_changed=self.expansion_busy_changed,
            expansion_status_changed=self.expansion_status_changed,
            expansion_ready=self.expansion_ready,
            expansion_failed=self.expansion_failed,
            progress_pct=self.progress_pct,
            stage_changed=self.stage_changed,
            duplicate_check=self.duplicate_check,
            download_finished=self.download_finished,
            failed=self.failed,
            cancelled=self.cancelled,
            finished=self.finished,
        )
        self._view = panel

    def is_probe_running(self, job_key: str | None = None) -> bool:
        if job_key is None:
            return bool(self._probe_runners)
        runner = self._probe_runners.get(str(job_key))
        return bool(runner is not None and runner.is_running())

    def is_downloading(self) -> bool:
        return self._download_runner.is_running()

    def is_expanding(self) -> bool:
        return self._expansion_runner.is_running()

    def is_busy(self) -> bool:
        return self.is_probe_running() or self.is_downloading() or self.is_expanding()

    def _emit_access_intervention_payload(self, payload: dict[str, object]) -> None:
        job_key = str((payload or {}).get("job_key") or (payload or {}).get("source_key") or "")
        self.access_intervention_required.emit(job_key, dict(payload or {}))

    def start_probe(
        self,
        *,
        job_key: str,
        url: str,
        browser_cookies_mode_override: str | None = None,
    ) -> DownloadWorker | None:
        key = str(job_key or "probe").strip() or "probe"
        runner = self._probe_runners.get(key)
        if runner is not None and runner.is_running():
            return self._probe_workers.get(key)

        runner = WorkerRunner(self)
        worker = DownloadWorker(
            action="probe",
            url=url,
            job_key=key,
            browser_cookies_mode_override=browser_cookies_mode_override,
        )

        self._probe_runners[key] = runner
        self._probe_workers[key] = worker

        self.probe_busy_changed.emit(key, True)
        self.busy_changed.emit(True)

        def _connect(wk: DownloadWorker, *, _job_key: str = key) -> None:
            def _emit_probe_ready(meta: dict[str, object]) -> None:
                self.probe_meta_ready.emit(_job_key, dict(meta or {}))

            def _emit_probe_failed(err_key: str, params: dict[str, object]) -> None:
                self.probe_failed.emit(_job_key, str(err_key), dict(params or {}))

            def _emit_access_intervention(params: dict[str, object]) -> None:
                self.access_intervention_required.emit(_job_key, dict(params or {}))

            wk.meta_ready.connect(_emit_probe_ready)
            wk.download_error.connect(_emit_probe_failed)
            wk.access_intervention_required.connect(_emit_access_intervention)

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

    def expand_manual_input(self, raw: str) -> SourceExpansionWorker | None:
        return start_manual_input_expansion(
            runner=self._expansion_runner,
            current_worker=self._expansion_worker,
            raw=raw,
            set_worker=self._set_expansion_worker,
            emit_expansion_busy=self.expansion_busy_changed.emit,
            emit_busy=self.busy_changed.emit,
            emit_status=self.expansion_status_changed.emit,
            emit_ready=self.expansion_ready.emit,
            emit_failed=self.expansion_failed.emit,
            emit_access_intervention=self._emit_access_intervention_payload,
            is_busy=self.is_busy,
        )

    def _set_expansion_worker(self, worker: SourceExpansionWorker | None) -> None:
        self._expansion_worker = worker

    def cancel_expansion(self) -> None:
        self._expansion_runner.cancel()

    def start_download(
        self,
        *,
        job_key: str,
        url: str,
        kind: str,
        quality: str,
        ext: str,
        audio_track_id: str | None = None,
        browser_cookies_mode_override: str | None = None,
    ) -> DownloadWorker | None:
        if self._download_runner.is_running():
            return self._download_worker

        worker = DownloadWorker(
            action="download",
            url=url,
            job_key=job_key,
            kind=kind,
            quality=quality,
            ext=ext,
            audio_track_id=audio_track_id,
            browser_cookies_mode_override=browser_cookies_mode_override,
        )
        self._download_worker = worker

        self.download_busy_changed.emit(True)
        self.busy_changed.emit(True)

        def _connect(wk: DownloadWorker) -> None:
            def _emit_access_intervention(params: dict[str, object]) -> None:
                self.access_intervention_required.emit(str(wk.job_key or job_key), dict(params or {}))

            wk.progress_pct.connect(self.progress_pct)
            wk.stage_changed.connect(self.stage_changed)
            wk.duplicate_check.connect(self.duplicate_check)
            wk.download_finished.connect(self.download_finished)
            wk.download_error.connect(self.failed)
            wk.cancelled.connect(self.cancelled)
            wk.access_intervention_required.connect(_emit_access_intervention)

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

    def resolve_access_intervention(self, job_key: str, action: str, value: str = "") -> None:
        key = str(job_key or "").strip()
        worker = self._probe_workers.get(key)
        if worker is None:
            active_worker = self._download_worker
            if active_worker is not None and active_worker.job_key == key:
                worker = active_worker
        if worker is None and self._expansion_runner.is_running():
            worker = self._expansion_worker
        if worker is None:
            return
        try:
            worker.on_access_intervention_decided(action, value)
        except (AttributeError, RuntimeError, TypeError):
            return
