# ui/workers/metadata_worker.py
from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Tuple, Union

from PyQt5 import QtCore

from core.services.media_metadata import MediaMetadataService
from ui.utils.concurrency import CancellationToken
from core.io.text import is_url
from ui.utils.translating import tr

GUIEntry = Union[str, Dict[str, Any]]


class MetadataWorker(QtCore.QObject):
    """
    Gathers lightweight metadata for entries to present in the Files tab table.
    For local files: name, source=LOCAL, path, size(bytes), duration(s).
    For URLs: name=title, source=URL, path=url, size if available, duration(s).
    """
    finished = QtCore.pyqtSignal()
    progress_log = QtCore.pyqtSignal(str)
    table_ready = QtCore.pyqtSignal(list)  # list[dict]

    def __init__(self, entries: List[GUIEntry]) -> None:
        super().__init__()
        self._entries = list(entries)
        self._meta = MediaMetadataService()
        self._token = CancellationToken()

    # ----- Cancellation -----

    @QtCore.pyqtSlot()
    def cancel(self) -> None:
        self._token.cancel()

    def _is_cancelled(self) -> bool:
        if self._token.is_cancelled:
            return True
        th = QtCore.QThread.currentThread()
        return bool(th and th.isInterruptionRequested())

    # ----- Main -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        batch: List[Dict[str, Any]] = []
        try:
            for raw in self._entries:
                if self._is_cancelled():
                    self.progress_log.emit(tr("log.cancelled"))
                    break

                try:
                    value, ty = self._normalize_entry(raw)

                    # URLs (or things that look like URLs) → use DownloadService via MediaMetadataService
                    if ty == "url" or is_url(value):
                        self.progress_log.emit(tr("down.log.analyze"))
                        if self._is_cancelled():
                            self.progress_log.emit(tr("log.cancelled"))
                            break
                        meta = self._meta.from_url(value, log=lambda m: None)
                    else:
                        # Local file metadata
                        meta = self._meta.from_local(Path(value))

                    if not meta:
                        continue

                    row = meta.as_files_row()

                    batch.append(row)
                    if len(batch) >= 10:
                        self.table_ready.emit(batch)
                        batch = []

                except Exception as e:
                    self.progress_log.emit(tr("log.unexpected", msg=f"metadata: {e}"))

            if not self._is_cancelled() and batch:
                self.table_ready.emit(batch)
        finally:
            self.finished.emit()

    @staticmethod
    def _normalize_entry(raw: GUIEntry) -> Tuple[str, str]:
        if isinstance(raw, dict):
            t = str(raw.get("type", "") or "").strip().lower()
            v = raw.get("value", "")
            v = str(v) if not isinstance(v, str) else v
            return v.strip(), t
        s = str(raw).strip()
        if s.startswith("[URL]"):
            return s[5:].strip(), "url"
        return s, ""
