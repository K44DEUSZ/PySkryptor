# app/controller/tasks/startup_task.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from PyQt5 import QtCore

from app.controller.tasks.base_worker import BaseWorker
from app.model.io.file_manager import FileManager
from app.model.services.ai_models_service import AIModelsService, ModelNotInstalledError
from app.model.services.settings_service import RuntimeConfigService

_LOG = logging.getLogger(__name__)
_ROOT = logging.getLogger()

ProgressCb = Callable[[int], None]
TaskFn = Callable[["_StartupRuntime", ProgressCb, dict[str, Any]], None]


@dataclass(frozen=True)
class _StartupRuntime:
    config_cls: Any
    snap: Any

@dataclass(frozen=True)
class StartupTask:
    label: str
    weight: int
    min_display_ms: int
    fn: TaskFn
    runtime: _StartupRuntime

    def run(self, progress: ProgressCb, ctx: dict[str, Any]) -> None:
        self.fn(self.runtime, progress, ctx)


# ----- Startup steps -----
def _task_init_runtime(runtime: _StartupRuntime, progress: ProgressCb, ctx: dict[str, Any]) -> None:
    config_cls = runtime.config_cls
    snap = runtime.snap

    config_cls.initialize_from_snapshot(snap)
    AIModelsService.apply_engine_runtime(snap.engine)
    ctx["settings_snapshot"] = snap

    try:
        dev_str = str(getattr(config_cls, "DEVICE_ID", "cpu"))
        dtype_str = str(getattr(config_cls, "DTYPE_ID", "float32"))
        friendly = str(getattr(config_cls, "DEVICE_FRIENDLY_NAME", dev_str))
        _ROOT.info("Runtime device resolved. device=%s friendly=%s dtype=%s", dev_str, friendly, dtype_str)
        _LOG.debug(
            "Runtime engine settings applied. preferred_device=%s precision=%s allow_tf32=%s low_cpu_mem_usage=%s device=%s dtype=%s",
            str((snap.engine or {}).get("preferred_device", "auto")),
            str((snap.engine or {}).get("precision", "auto")),
            bool((snap.engine or {}).get("allow_tf32", False)),
            bool((snap.engine or {}).get("low_cpu_mem_usage", False)),
            dev_str,
            dtype_str,
        )
    except Exception:
        pass

    progress(100)


def _task_ensure_dirs(runtime: _StartupRuntime, progress: ProgressCb, _ctx: dict[str, Any]) -> None:
    config_cls = runtime.config_cls
    config_cls.ensure_dirs()
    try:
        FileManager.clear_temp_dir(config_cls.DOWNLOADS_TMP_DIR)
        FileManager.clear_temp_dir(config_cls.TRANSCRIPTIONS_TMP_DIR)
        config_cls.DOWNLOADS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        config_cls.TRANSCRIPTIONS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        _LOG.debug(
            "Startup directories prepared. downloads_tmp=%s transcriptions_tmp=%s",
            config_cls.DOWNLOADS_TMP_DIR,
            config_cls.TRANSCRIPTIONS_TMP_DIR,
        )
    except Exception:
        _LOG.exception("Startup temp directory cleanup failed.")
    progress(100)


def _task_setup_ffmpeg(runtime: _StartupRuntime, progress: ProgressCb, _ctx: dict[str, Any]) -> None:
    RuntimeConfigService.setup_ffmpeg_on_path(runtime.config_cls)
    _LOG.debug("FFmpeg runtime configured. ffmpeg_dir=%s", getattr(runtime.config_cls, "FFMPEG_BIN_DIR", ""))
    progress(100)


def _warmup_model_runtime(
    ctx: dict[str, Any],
    *,
    name: str,
    enabled: bool,
    ensure_ready: Callable[[], Any],
    ready_key: str,
    ready_log_key: str,
    error_key_key: str,
    error_params_key: str,
    result_key: str | None = None,
) -> None:
    if not enabled:
        if result_key:
            ctx[result_key] = None
        ctx[ready_key] = False
        _LOG.debug("Startup %s warmup skipped. reason=disabled", name)
        return

    try:
        result = ensure_ready()
        if result_key:
            ctx[result_key] = result
        ctx[ready_key] = bool(result)
        _LOG.debug("Startup %s model ready. %s=%s", name, ready_log_key, bool(ctx[ready_key]))
    except ModelNotInstalledError as ex:
        if result_key:
            ctx[result_key] = None
        ctx[ready_key] = False
        ctx[error_key_key] = getattr(ex, "key", "error.model.not_installed")
        ctx[error_params_key] = {"path": str(getattr(ex, "path", ""))}
        _LOG.debug(
            "Startup %s model missing. key=%s path=%s",
            name,
            ctx[error_key_key],
            ctx[error_params_key].get("path", ""),
        )


def _task_load_transcription_model(_runtime: _StartupRuntime, progress: ProgressCb, ctx: dict[str, Any]) -> None:
    svc = AIModelsService()
    _warmup_model_runtime(
        ctx,
        name="transcription",
        enabled=bool(svc.transcription.is_enabled()),
        ensure_ready=svc.ensure_transcription_ready,
        ready_key="transcription_ready",
        ready_log_key="asr_ready",
        error_key_key="transcription_error_key",
        error_params_key="transcription_error_params",
        result_key="transcription_pipeline",
    )
    progress(100)


def _task_warmup_translation_model(_runtime: _StartupRuntime, progress: ProgressCb, ctx: dict[str, Any]) -> None:
    svc = AIModelsService()
    _warmup_model_runtime(
        ctx,
        name="translation",
        enabled=bool(svc.translation.is_enabled()),
        ensure_ready=svc.ensure_translation_ready,
        ready_key="translation_ready",
        ready_log_key="translation_ready",
        error_key_key="translation_error_key",
        error_params_key="translation_error_params",
    )
    progress(100)


def build_startup_tasks(config_cls: Any, snap: Any, labels: dict[str, str]) -> list[StartupTask]:
    runtime = _StartupRuntime(config_cls=config_cls, snap=snap)
    return [
        StartupTask(label=labels["init"], weight=2, min_display_ms=300, fn=_task_init_runtime, runtime=runtime),
        StartupTask(label=labels["dirs"], weight=1, min_display_ms=300, fn=_task_ensure_dirs, runtime=runtime),
        StartupTask(label=labels["ffmpeg"], weight=1, min_display_ms=300, fn=_task_setup_ffmpeg, runtime=runtime),
        StartupTask(label=labels["asr"], weight=4, min_display_ms=0, fn=_task_load_transcription_model, runtime=runtime),
        StartupTask(label=labels["tr"], weight=3, min_display_ms=0, fn=_task_warmup_translation_model, runtime=runtime),
    ]


def _build_initial_context() -> dict[str, Any]:
    return {
        "settings_snapshot": None,
        "transcription_pipeline": None,
        "transcription_ready": False,
        "transcription_error_key": None,
        "transcription_error_params": {},
        "translation_ready": False,
        "translation_error_key": None,
        "translation_error_params": {},
    }


class StartupWorker(BaseWorker):
    status = QtCore.pyqtSignal(str)
    ready = QtCore.pyqtSignal(dict)

    def __init__(self, tasks: list[StartupTask]) -> None:
        super().__init__()
        self._tasks = tasks
        self._ctx: dict[str, Any] = _build_initial_context()

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

            t.run(phase_progress, self._ctx)

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
            bool(self._ctx.get("transcription_ready")),
            bool(self._ctx.get("translation_ready")),
        )
        self.ready.emit(self._ctx)

    def _handle_failure(self, ex: BaseException) -> None:
        _LOG.exception("Startup worker failed.")
        self.failed.emit("error.startup.failed", {"detail": str(ex)})
