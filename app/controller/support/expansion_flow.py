# app/controller/support/expansion_flow.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.controller.workers.source_expansion_worker import SourceExpansionWorker
from app.controller.workers.worker_runner import WorkerRunner


def build_manual_input_worker(raw: str) -> SourceExpansionWorker:
    """Build the worker used to expand raw manual source input."""
    return SourceExpansionWorker(mode="manual_input", raw=str(raw or ""))


def build_local_paths_worker(paths: list[str], origin_kind: str) -> SourceExpansionWorker:
    """Build the worker used to normalize a list of local paths."""
    return SourceExpansionWorker(
        mode="local_paths",
        paths=list(paths or []),
        origin_kind=str(origin_kind or "local_paths"),
    )


def start_expansion_worker(
    *,
    runner: WorkerRunner,
    worker: SourceExpansionWorker,
    set_worker: Callable[[SourceExpansionWorker | None], None],
    emit_expansion_busy: Callable[[bool], None],
    emit_busy: Callable[[bool], None],
    emit_status: Callable[[str, dict[str, Any]], None],
    emit_ready: Callable[[object], None],
    emit_failed: Callable[[str, dict[str, Any]], None],
    emit_access_intervention: Callable[[dict[str, Any]], None] | None,
    is_busy: Callable[[], bool],
) -> SourceExpansionWorker | None:
    """Start a source-expansion worker using the shared coordinator lifecycle."""
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


def start_manual_input_expansion(
    *,
    runner: WorkerRunner,
    current_worker: SourceExpansionWorker | None,
    raw: str,
    set_worker: Callable[[SourceExpansionWorker | None], None],
    emit_expansion_busy: Callable[[bool], None],
    emit_busy: Callable[[bool], None],
    emit_status: Callable[[str, dict[str, Any]], None],
    emit_ready: Callable[[object], None],
    emit_failed: Callable[[str, dict[str, Any]], None],
    emit_access_intervention: Callable[[dict[str, Any]], None] | None,
    is_busy: Callable[[], bool],
) -> SourceExpansionWorker | None:
    """Start manual-input expansion if no expansion worker is already running."""
    if runner.is_running():
        return current_worker

    worker = build_manual_input_worker(raw)
    return start_expansion_worker(
        runner=runner,
        worker=worker,
        set_worker=set_worker,
        emit_expansion_busy=emit_expansion_busy,
        emit_busy=emit_busy,
        emit_status=emit_status,
        emit_ready=emit_ready,
        emit_failed=emit_failed,
        emit_access_intervention=emit_access_intervention,
        is_busy=is_busy,
    )


def start_local_paths_expansion(
    *,
    runner: WorkerRunner,
    current_worker: SourceExpansionWorker | None,
    paths: list[str],
    origin_kind: str,
    set_worker: Callable[[SourceExpansionWorker | None], None],
    emit_expansion_busy: Callable[[bool], None],
    emit_busy: Callable[[bool], None],
    emit_status: Callable[[str, dict[str, Any]], None],
    emit_ready: Callable[[object], None],
    emit_failed: Callable[[str, dict[str, Any]], None],
    emit_access_intervention: Callable[[dict[str, Any]], None] | None,
    is_busy: Callable[[], bool],
) -> SourceExpansionWorker | None:
    """Start local-path expansion if no expansion worker is already running."""
    if runner.is_running():
        return current_worker

    worker = build_local_paths_worker(paths, origin_kind)
    return start_expansion_worker(
        runner=runner,
        worker=worker,
        set_worker=set_worker,
        emit_expansion_busy=emit_expansion_busy,
        emit_busy=emit_busy,
        emit_status=emit_status,
        emit_ready=emit_ready,
        emit_failed=emit_failed,
        emit_access_intervention=emit_access_intervention,
        is_busy=is_busy,
    )

