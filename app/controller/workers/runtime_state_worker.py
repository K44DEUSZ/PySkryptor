# app/controller/workers/runtime_state_worker.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Callable

from PyQt5 import QtCore

from app.controller.workers.task_worker import TaskWorker
from app.model.core.config.config import AppConfig
from app.model.core.domain.entities import SettingsSnapshot
from app.model.core.domain.state import AppRuntimeState
from app.model.core.runtime.ffmpeg import setup_ffmpeg_runtime
from app.model.core.utils.path_utils import clear_temp_dir
from app.model.engines.manager import EngineManager
from app.model.engines.runtime_config import apply_engine_runtime

_LOG = logging.getLogger(__name__)

ProgressCb = Callable[[int], None]
StageFn = Callable[[ProgressCb, AppRuntimeState], AppRuntimeState]


@dataclass(frozen=True)
class RuntimeStage:
    """Weighted runtime stage executed by RuntimeStateWorker."""

    label: str
    weight: int
    min_display_ms: int
    fn: StageFn

    def run(self, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        return self.fn(progress, state)


class RuntimeStateWorker(TaskWorker):
    """Background worker that prepares runtime state and warms engine hosts."""

    status = QtCore.pyqtSignal(str)
    ready = QtCore.pyqtSignal(object)

    def __init__(self, *, engine_manager: EngineManager, snapshot: SettingsSnapshot, labels: dict[str, str]) -> None:
        super().__init__()
        self._engine_manager = engine_manager
        self._snapshot = snapshot
        self._labels = dict(labels or {})
        self._state = AppRuntimeState()

    def _execute(self) -> None:
        total = sum(max(1, int(stage.weight)) for stage in self._build_stages()) or 1
        done = 0
        started_at = time.perf_counter()
        self.progress.emit(0)

        for stage in self._build_stages():
            weight = max(1, int(stage.weight))
            self.status.emit(stage.label)
            phase_started = time.perf_counter()

            def stage_progress(pct: int) -> None:
                pct_i = max(0, min(100, int(pct)))
                overall = int(((done + (weight * pct_i / 100.0)) / total) * 100.0)
                self.progress.emit(max(0, min(100, overall)))

            self._state = stage.run(stage_progress, self._state)

            elapsed_ms = int((time.perf_counter() - phase_started) * 1000.0)
            remaining_ms = max(0, int(stage.min_display_ms) - elapsed_ms)
            if remaining_ms > 0:
                time.sleep(remaining_ms / 1000.0)

            done += weight
            self.progress.emit(int((done / total) * 100.0))

        _LOG.debug(
            "Runtime state worker finished. duration_ms=%s transcription_ready=%s translation_ready=%s",
            int((time.perf_counter() - started_at) * 1000.0),
            bool(self._state.transcription.ready),
            bool(self._state.translation.ready),
        )
        self.ready.emit(self._state)

    def _build_stages(self) -> list[RuntimeStage]:
        return [
            RuntimeStage(self._labels["init"], 2, 300, self._stage_init_runtime),
            RuntimeStage(self._labels["dirs"], 1, 300, self._stage_ensure_dirs),
            RuntimeStage(self._labels["ffmpeg"], 1, 300, self._stage_setup_ffmpeg),
            RuntimeStage(self._labels["asr"], 4, 0, self._stage_warmup_transcription),
            RuntimeStage(self._labels["translation"], 3, 0, self._stage_warmup_translation),
        ]

    def _stage_init_runtime(self, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        AppConfig.initialize_from_snapshot(self._snapshot)
        apply_engine_runtime(self._snapshot.engine)
        progress(100)
        return replace(state, settings_snapshot=self._snapshot)

    @staticmethod
    def _stage_ensure_dirs(progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        AppConfig.ensure_dirs()
        try:
            clear_temp_dir(AppConfig.PATHS.DOWNLOADS_TMP_DIR)
            clear_temp_dir(AppConfig.PATHS.TRANSCRIPTIONS_TMP_DIR)
            AppConfig.PATHS.DOWNLOADS_TMP_DIR.mkdir(parents=True, exist_ok=True)
            AppConfig.PATHS.TRANSCRIPTIONS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            _LOG.error("Runtime temp directory cleanup failed.", exc_info=True)
        progress(100)
        return state

    @staticmethod
    def _stage_setup_ffmpeg(progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        setup_ffmpeg_runtime(AppConfig)
        progress(100)
        return state

    def _stage_warmup_transcription(self, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        progress(5)
        transcription_state = self._engine_manager.warmup_role("transcription")
        progress(100)
        return replace(state, transcription=transcription_state)

    def _stage_warmup_translation(self, progress: ProgressCb, state: AppRuntimeState) -> AppRuntimeState:
        progress(5)
        translation_state = self._engine_manager.warmup_role("translation")
        progress(100)
        return replace(state, translation=translation_state)
