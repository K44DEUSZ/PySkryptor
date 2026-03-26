# app/controller/support/source_expansion.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.controller.workers.source_expansion_worker import SourceExpansionWorker
from app.controller.workers.task_thread_runner import TaskThreadRunner


def build_manual_input_worker(raw: str) -> SourceExpansionWorker:
    return SourceExpansionWorker(mode="manual_input", raw=str(raw or ""))


def build_local_paths_worker(paths: list[str], origin_kind: str) -> SourceExpansionWorker:
    return SourceExpansionWorker(
        mode="local_paths",
        paths=list(paths or []),
        origin_kind=str(origin_kind or "local_paths"),
    )


def start_expansion_worker(
    *,
    runner: TaskThreadRunner,
    worker: SourceExpansionWorker,
    set_worker: Callable[[SourceExpansionWorker | None], None],
    emit_expansion_busy: Callable[[bool], None],
    emit_busy: Callable[[bool], None],
    emit_status: Callable[[str, dict[str, Any]], None],
    emit_ready: Callable[[object], None],
    emit_failed: Callable[[str, dict[str, Any]], None],
    is_busy: Callable[[], bool],
) -> SourceExpansionWorker | None:
    set_worker(worker)
    emit_expansion_busy(True)
    emit_busy(True)

    def _connect(wk: SourceExpansionWorker) -> None:
        wk.status_changed.connect(emit_status)
        wk.expanded.connect(emit_ready)
        wk.failed.connect(emit_failed)

    def _done() -> None:
        set_worker(None)
        emit_expansion_busy(False)
        emit_status("", {})
        emit_busy(is_busy())

    return runner.start(worker, connect=_connect, on_finished=_done)
