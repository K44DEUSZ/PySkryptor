# ui/workers/metadata_worker.py
from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Tuple, Union, Optional

from PyQt5 import QtCore

from core.io.audio_extractor import AudioExtractor
from core.services.download_service import DownloadService
from core.utils.concurrency import CancellationToken
from core.utils.text import is_url
from ui.i18n.translator import tr

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
        self._down = DownloadService()
        self._token = CancellationToken()

    # ---------- Cancellation ----------

    @QtCore.pyqtSlot()
    def cancel(self) -> None:
        self._token.cancel()

    def _is_cancelled(self) -> bool:
        if self._token.is_cancelled:
            return True
        th = QtCore.QThread.currentThread()
        return bool(th and th.isInterruptionRequested())

    # ---------- Main ----------

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
                    if ty == "url" or is_url(value):
                        self.progress_log.emit(tr("down.log.analyze"))
                        if self._is_cancelled():
                            self.progress_log.emit(tr("log.cancelled"))
                            break
                        try:
                            meta = self._down.probe(value, log=lambda m: None)
                        except Exception as e:
                            self.progress_log.emit(tr("error.down.probe_failed", detail=str(e)))
                            continue
                        title = meta.get("title") or value
                        duration = meta.get("duration")
                        size = meta.get("filesize") or meta.get("filesize_approx")
                        row = {
                            "name": title,
                            "source": "URL",
                            "path": value,
                            "size": int(size) if size else None,
                            "duration": float(duration) if duration else None,
                        }
                    else:
                        p = Path(value)
                        if not p.exists() or not p.is_file():
                            continue
                        try:
                            size = p.stat().st_size
                        except Exception:
                            size = None
                        dur = AudioExtractor.probe_duration(p)
                        row = {
                            "name": p.stem,
                            "source": "LOCAL",
                            "path": str(p),
                            "size": int(size) if size is not None else None,
                            "duration": float(dur) if dur is not None else None,
                        }

                    batch.append(row)
                    if len(batch) >= 10:
                        self.table_ready.emit(batch)
                        batch = []

                except Exception as e:
                    self.progress_log.emit(tr("error.config.generic", detail=f"metadata: {e}"))

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
