# controller/tasks/metadata_task.py
from __future__ import annotations

from typing import Any, Dict, List

from PyQt5 import QtCore

from model.services.media_metadata import MediaMetadataService
from model.io.text import is_url
from view.utils.concurrency import CancellationToken


class MetadataWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    progress_log = QtCore.pyqtSignal(str)
    table_ready = QtCore.pyqtSignal(list)

    def __init__(self, entries: List[Dict[str, Any]]) -> None:
        super().__init__()
        self._entries = entries or []
        self._cancel = CancellationToken()

    def cancel(self) -> None:
        self._cancel.cancel()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            svc = MediaMetadataService()
            rows: List[Dict[str, Any]] = []

            for e in self._entries:
                if self._cancel.is_cancelled():
                    break
                if QtCore.QThread.currentThread().isInterruptionRequested():
                    break

                kind = str(e.get("type") or "").strip().lower()
                val = str(e.get("value") or "").strip()
                if not val:
                    continue

                try:
                    if kind == "url" or is_url(val):
                        mm = svc.from_url(val, log=lambda m: self.progress_log.emit(str(m)))
                    else:
                        mm = svc.from_local(val, log=lambda m: self.progress_log.emit(str(m)))
                    rows.append(mm.as_files_row())
                except Exception as ex:
                    self.progress_log.emit(f"{val}: {ex}")

            if rows:
                self.table_ready.emit(rows)
        finally:
            self.finished.emit()
