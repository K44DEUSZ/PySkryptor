# app/controller/tasks/media_probe_task.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.tasks.base_worker import BaseWorker
from app.model.io.media_probe import MediaProbeService

_LOG = logging.getLogger(__name__)


class MediaProbeWorker(BaseWorker):
    table_ready = QtCore.pyqtSignal(list)
    item_error = QtCore.pyqtSignal(str, str, dict)

    def __init__(
        self,
        entries: list[dict[str, Any]],
        *,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._entries = list(entries or [])

    def cancel(self) -> None:
        super().cancel()

    def _execute(self) -> None:
        svc = MediaProbeService()

        out: list[dict[str, Any]] = []
        for ent in self._entries:
            if self._cancel.is_cancelled:
                break

            src = str(ent.get("type") or "").strip().lower()
            val = str(ent.get("value") or "").strip()
            if not src or not val:
                continue

            try:
                if src == "url":
                    meta = svc.from_url(val)
                else:
                    meta = svc.from_local(Path(val))
                if meta:
                    out.append(meta.as_files_row())
            except Exception as ex:
                _LOG.exception("Media probe failed.", extra={"source": src, "value": val})
                self.item_error.emit(val, "error.media_probe.failed", {"source": src, "value": val, "detail": str(ex)})

        if out and not self._cancel.is_cancelled:
            self.table_ready.emit(out)
