# app/controller/workers/source_expansion_worker.py
from __future__ import annotations

from typing import Any

from PyQt5 import QtCore

from app.controller.workers.task_worker import TaskWorker
from app.model.domain.results import SourceExpansionResult
from app.model.services.source_expansion_service import SourceExpansionService


class SourceExpansionWorker(TaskWorker):
    """Background worker that expands a user add action into normalized sources."""

    expanded = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str, dict)

    def __init__(self, *, mode: str, raw: str = "", paths: list[str] | None = None, origin_kind: str = "") -> None:
        super().__init__()
        self._mode = str(mode or "").strip().lower()
        self._raw = str(raw or "")
        self._paths = list(paths or [])
        self._origin_kind = str(origin_kind or "").strip().lower()

    def _emit_status(self, key: str, params: dict[str, Any] | None = None) -> None:
        self.status_changed.emit(str(key or ""), dict(params or {}))

    def _execute(self) -> None:
        svc = SourceExpansionService(cancel_check=self.cancel_check, status_callback=self._emit_status)
        result: SourceExpansionResult
        if self._mode == "manual_input":
            result = svc.expand_manual_input(self._raw)
        elif self._mode == "local_paths":
            result = svc.expand_local_paths(self._paths, origin_kind=self._origin_kind or "local_paths")
        else:
            raise ValueError(f"Unsupported source expansion mode: {self._mode}")
        self.expanded.emit(result)
