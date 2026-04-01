# app/controller/support/expansion_flow.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.controller.workers.source_expansion_worker import SourceExpansionWorker
from app.controller.workers.worker_runner import WorkerRunner


def _build_source_expansion_worker(mode: str, **worker_kwargs: Any) -> SourceExpansionWorker:
    """Build a source-expansion worker with normalized constructor payload."""
    payload = {key: value for key, value in worker_kwargs.items() if value is not None}
    return SourceExpansionWorker(mode=str(mode or ""), **payload)


def start_source_expansion(
    *,
    runner: WorkerRunner,
    current_worker: SourceExpansionWorker | None,
    mode: str,
    set_worker: Callable[[SourceExpansionWorker | None], None],
    emit_expansion_busy: Callable[[bool], None],
    emit_busy: Callable[[bool], None],
    emit_status: Callable[[str, dict[str, Any]], None],
    emit_ready: Callable[[object], None],
    emit_failed: Callable[[str, dict[str, Any]], None],
    emit_access_intervention: Callable[[dict[str, Any]], None] | None,
    is_busy: Callable[[], bool],
    **worker_kwargs: Any,
) -> SourceExpansionWorker | None:
    """Start a source-expansion worker using the shared coordinator lifecycle."""
    if runner.is_running():
        return current_worker

    worker = _build_source_expansion_worker(mode, **worker_kwargs)
    set_worker(worker)
    emit_expansion_busy(True)
    emit_busy(True)

    def _connect(wk: SourceExpansionWorker) -> None:
        wk.status_changed.connect(emit_status)
        wk.expanded.connect(emit_ready)
        wk.failed.connect(emit_failed)
        if emit_access_intervention is not None:
            wk.access_intervention_required.connect(emit_access_intervention)

    def _done() -> None:
        set_worker(None)
        emit_expansion_busy(False)
        emit_status("", {})
        emit_busy(is_busy())

    return runner.start(worker, connect=_connect, on_finished=_done)
