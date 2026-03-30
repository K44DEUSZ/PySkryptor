# app/controller/workers/startup_worker.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from PyQt5 import QtCore

from app.controller.workers.task_worker import TaskWorker
from app.model.core.domain.errors import AppError
from app.model.core.domain.state import AppRuntimeState
from app.model.core.runtime.ffmpeg import setup_ffmpeg_runtime
from app.model.core.utils.path_utils import clear_temp_dir
from app.model.engines.service import AIModelsService, ModelNotInstalledError

_LOG = logging.getLogger(__name__)

ProgressCb = Callable[[int], None]
TaskFn = Callable[["_StartupRuntime", ProgressCb, AppRuntimeState], AppRuntimeState]


@dataclass(frozen=True)
class _StartupRuntime:
    """Shared immutable startup inputs reused across all startup tasks."""

    config_cls: Any
    snap: Any


@dataclass(frozen=True)
class StartupTask:
    """Weighted startup step executed by StartupWorker."""

    label: str
    weight: int
    min_display_ms: int
    fn: TaskFn
    runtime: _StartupRuntime

    def run(self, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        return self.fn(self.runtime, progress, state)


def _task_init_runtime(runtime: _StartupRuntime, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
    config_cls = runtime.config_cls
    snap = runtime.snap

    config_cls.initialize_from_snapshot(snap)
    AIModelsService.apply_engine_runtime(snap.engine)

    dev_str = str(getattr(config_cls, "DEVICE_ID", "cpu"))
    dtype_str = str(getattr(config_cls, "DTYPE_ID", "float32"))
    friendly = str(getattr(config_cls, "DEVICE_FRIENDLY_NAME", dev_str))
    _LOG.info("Runtime device resolved. device=%s friendly=%s dtype=%s", dev_str, friendly, dtype_str)
    _LOG.debug(
        (
            "Runtime engine settings applied. preferred_device=%s precision=%s fp32_math_mode=%s "
            "low_cpu_mem_usage=%s device=%s dtype=%s tf32_enabled=%s"
        ),
        str((snap.engine or {}).get("preferred_device", "auto")),
        str((snap.engine or {}).get("precision", "auto")),
        str((snap.engine or {}).get("fp32_math_mode", "ieee")),
        bool((snap.engine or {}).get("low_cpu_mem_usage", False)),
        dev_str,
        dtype_str,
        bool(getattr(config_cls, "TF32_ENABLED", False)),
    )

    progress(100)
    return replace(state, settings_snapshot=snap)


def _task_ensure_dirs(runtime: _StartupRuntime, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
    config_cls = runtime.config_cls
    config_cls.ensure_dirs()
    try:
        clear_temp_dir(config_cls.PATHS.DOWNLOADS_TMP_DIR)
        clear_temp_dir(config_cls.PATHS.TRANSCRIPTIONS_TMP_DIR)
        config_cls.PATHS.DOWNLOADS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        config_cls.PATHS.TRANSCRIPTIONS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        _LOG.debug(
            "Startup directories prepared. downloads_tmp=%s transcriptions_tmp=%s",
            config_cls.PATHS.DOWNLOADS_TMP_DIR,
            config_cls.PATHS.TRANSCRIPTIONS_TMP_DIR,
        )
    except OSError:
        _LOG.exception("Startup temp directory cleanup failed.")
    progress(100)
    return state


def _task_setup_ffmpeg(runtime: _StartupRuntime, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
    setup_ffmpeg_runtime(runtime.config_cls)
    _LOG.debug("FFmpeg runtime configured. ffmpeg_dir=%s", getattr(runtime.config_cls.PATHS, "FFMPEG_BIN_DIR", ""))
    progress(100)
    return state


def _warmup_model_runtime(
    state: AppRuntimeState,
    *,
    name: str,
    enabled: bool,
    ensure_ready: Callable[[], Any],
    ready_key: str,
    ready_log_key: str,
    error_key_key: str,
    error_params_key: str,
    result_key: str | None = None,
) -> AppRuntimeState:
    if not enabled:
        changes: dict[str, Any] = {
            ready_key: False,
            error_key_key: None,
            error_params_key: {},
        }
        if result_key is not None:
            changes[result_key] = None
        _LOG.debug("Startup %s warmup skipped. reason=disabled", name)
        return replace(state, **changes)

    try:
        result = ensure_ready()
        changes = {
            ready_key: bool(result),
            error_key_key: None,
            error_params_key: {},
        }
        if result_key is not None:
            changes[result_key] = result
        next_state = replace(state, **changes)
        _LOG.debug("Startup %s model ready. %s=%s", name, ready_log_key, bool(getattr(next_state, ready_key)))
        return next_state
    except ModelNotInstalledError as ex:
        params = dict(getattr(ex, "params", {}) or {})
        path = str(getattr(ex, "path", params.get("path", "")) or "")
        changes = {
            ready_key: False,
            error_key_key: getattr(ex, "key", "error.model.not_installed"),
            error_params_key: {"path": path},
        }
        if result_key is not None:
            changes[result_key] = None
        next_state = replace(state, **changes)
        _LOG.debug(
            "Startup %s model missing. path=%s",
            name,
            path,
        )
        return next_state
    except AppError as ex:
        changes = {
            ready_key: False,
            error_key_key: getattr(ex, "key", "error.generic"),
            error_params_key: dict(getattr(ex, "params", {}) or {}),
        }
        if result_key is not None:
            changes[result_key] = None
        next_state = replace(state, **changes)
        _LOG.debug(
            "Startup %s warmup failed. error_key=%s",
            name,
            getattr(next_state, error_key_key),
        )
        return next_state


def _task_load_transcription_model(
    _runtime: _StartupRuntime,
    progress: ProgressCb,
    state: AppRuntimeState,
) -> AppRuntimeState:
    svc = AIModelsService()
    next_state = _warmup_model_runtime(
        state,
        name="transcription",
        enabled=svc.is_enabled("transcription"),
        ensure_ready=lambda: svc.ensure_ready("transcription"),
        ready_key="transcription_ready",
        ready_log_key="asr_ready",
        error_key_key="transcription_error_key",
        error_params_key="transcription_error_params",
        result_key="transcription_pipeline",
    )
    progress(100)
    return next_state


def _task_warmup_translation_model(
    _runtime: _StartupRuntime,
    progress: ProgressCb,
    state: AppRuntimeState,
) -> AppRuntimeState:
    svc = AIModelsService()
    next_state = _warmup_model_runtime(
        state,
        name="translation",
        enabled=svc.is_enabled("translation"),
        ensure_ready=lambda: svc.ensure_ready("translation"),
        ready_key="translation_ready",
        ready_log_key="translation_ready",
        error_key_key="translation_error_key",
        error_params_key="translation_error_params",
    )
    progress(100)
    return next_state


def build_startup_tasks(config_cls: Any, snap: Any, labels: dict[str, str]) -> list[StartupTask]:
    """Build the ordered startup task plan for the splash workflow."""
    runtime = _StartupRuntime(config_cls=config_cls, snap=snap)
    return [
        StartupTask(label=labels["init"], weight=2, min_display_ms=300, fn=_task_init_runtime, runtime=runtime),
        StartupTask(label=labels["dirs"], weight=1, min_display_ms=300, fn=_task_ensure_dirs, runtime=runtime),
        StartupTask(label=labels["ffmpeg"], weight=1, min_display_ms=300, fn=_task_setup_ffmpeg, runtime=runtime),
        StartupTask(
            label=labels["asr"],
            weight=4,
            min_display_ms=0,
            fn=_task_load_transcription_model,
            runtime=runtime,
        ),
        StartupTask(label=labels["tr"], weight=3, min_display_ms=0, fn=_task_warmup_translation_model, runtime=runtime),
    ]


class StartupWorker(TaskWorker):
    """Background worker that executes startup tasks and emits a ready runtime state."""

    status = QtCore.pyqtSignal(str)
    ready = QtCore.pyqtSignal(object)

    def __init__(self, tasks: list[StartupTask]) -> None:
        super().__init__()
        self._tasks = tasks
        self._state = AppRuntimeState()

    def _execute(self) -> None:
        total = sum(max(1, int(t.weight)) for t in self._tasks) or 1
        done = 0
        startup_started = time.perf_counter()

        self.progress.emit(0)

        for t in self._tasks:
            w = max(1, int(t.weight))
            self.status.emit(t.label)
            phase_started = time.perf_counter()
            _LOG.debug("Startup stage started. label=%s weight=%s", t.label, w)

            def phase_progress(pct: int) -> None:
                pct_i = max(0, min(100, int(pct)))
                overall = int(((done + (w * pct_i / 100.0)) / total) * 100.0)
                self.progress.emit(max(0, min(100, overall)))

            self._state = t.run(phase_progress, self._state)

            duration_ms = int((time.perf_counter() - phase_started) * 1000.0)
            remaining_ms = max(0, int(t.min_display_ms) - duration_ms)
            if remaining_ms > 0:
                _LOG.debug(
                    "Startup stage visibility delay applied. label=%s elapsed_ms=%s remaining_ms=%s",
                    t.label,
                    duration_ms,
                    remaining_ms,
                )
                time.sleep(remaining_ms / 1000.0)
                duration_ms += remaining_ms

            done += w
            self.progress.emit(int((done / total) * 100.0))
            _LOG.debug(
                "Startup stage finished. label=%s weight=%s duration_ms=%s",
                t.label,
                w,
                duration_ms,
            )

        self.progress.emit(100)
        _LOG.debug(
            "Startup worker finished. duration_ms=%s asr_ready=%s translation_ready=%s",
            int((time.perf_counter() - startup_started) * 1000.0),
            bool(self._state.transcription_ready),
            bool(self._state.translation_ready),
        )
        self.ready.emit(self._state)

    def _handle_failure(self, ex: BaseException) -> None:
        _LOG.exception("Startup worker failed.")
        self.failed.emit("error.startup.failed", {"detail": str(ex)})
