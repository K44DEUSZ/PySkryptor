# app/controller/support/quick_settings.py
from __future__ import annotations

from typing import Any, Callable

from app.controller.workers.settings_worker import SettingsWorker
from app.controller.workers.task_thread_runner import TaskThreadRunner


def start_settings_save(
    *,
    runner: TaskThreadRunner,
    current_worker: SettingsWorker | None,
    payload: dict[str, Any],
    on_failed: Callable[[SettingsWorker], None],
    set_worker: Callable[[SettingsWorker | None], None],
) -> SettingsWorker | None:
    """Start the shared quick-options save flow for panel coordinators."""

    if runner.is_running():
        return current_worker

    worker = SettingsWorker(action="save", payload=dict(payload or {}))
    set_worker(worker)

    def _connect(wk: SettingsWorker) -> None:
        on_failed(wk)

    def _done() -> None:
        set_worker(None)

    return runner.start(worker, connect=_connect, on_finished=_done)
