# ui/workers/metadata_worker.py
from __future__ import annotations

from pathlib import Path
from typing import List, Dict, Any, Tuple, Union, Optional

from PyQt5 import QtCore

from core.io.audio_extractor import AudioExtractor
from core.services.download_service import DownloadService
from core.utils.text import is_url

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

    @QtCore.pyqtSlot()
    def run(self) -> None:
        rows: List[Dict[str, Any]] = []
        try:
            for raw in self._entries:
                try:
                    value, ty = self._normalize_entry(raw)
                    if ty == "url" or is_url(value):
                        self.progress_log.emit(f"🔎 Analiza URL…")
                        try:
                            meta = self._down.probe(value, log=lambda m: None)
                        except Exception as e:
                            self.progress_log.emit(f"❗ Błąd analizy: {e}")
                            continue
                        title = meta.get("title") or value
                        duration = meta.get("duration")
                        size = meta.get("filesize") or meta.get("filesize_approx")
                        rows.append(
                            {
                                "name": title,
                                "source": "URL",
                                "path": value,
                                "size": int(size) if size else None,
                                "duration": float(duration) if duration else None,
                            }
                        )
                    else:
                        p = Path(value)
                        if not p.exists() or not p.is_file():
                            continue
                        size = None
                        try:
                            size = p.stat().st_size
                        except Exception:
                            pass
                        dur = AudioExtractor.probe_duration(p)
                        rows.append(
                            {
                                "name": p.stem,
                                "source": "LOCAL",
                                "path": str(p),
                                "size": int(size) if size is not None else None,
                                "duration": float(dur) if dur is not None else None,
                            }
                        )
                except Exception as e:
                    self.progress_log.emit(f"❗ Błąd metadanych: {e}")
            self.table_ready.emit(rows)
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
