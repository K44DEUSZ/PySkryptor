# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.services.download_service import DownloadService


class DownloadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    progress_pct = QtCore.pyqtSignal(int)
    meta_ready = QtCore.pyqtSignal(dict)
    download_finished = QtCore.pyqtSignal(object)  # Path
    download_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        url: str,
        action: str,
        format_expr: Optional[str] = None,
        desired_ext: Optional[str] = None,
        kind: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ):
        super().__init__()
        self.url = url
        self.action = action  # "probe" | "download"
        self.format_expr = format_expr
        self.desired_ext = desired_ext or ""
        self.kind = kind or "video"
        self.output_dir = output_dir
        self._service = DownloadService()
        self._cancelled = False
        self._started_logged = False

    def cancel(self) -> None:
        self._cancelled = True

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            def _log(msg: str) -> None:
                self.progress_log.emit(str(msg))

            def _progress(pct: int, stage: str) -> None:
                if not self._started_logged and pct > 0:
                    self.progress_log.emit("⬇️ Pobieranie rozpoczęte…")
                    self._started_logged = True
                self.progress_pct.emit(pct)
                if pct >= 100:
                    self.progress_log.emit("✅ Pobieranie zakończone.")

            if self.action == "probe":
                meta = self._service.probe(self.url, log=_log)
                if not self._cancelled:
                    self.meta_ready.emit(meta)
            elif self.action == "download":
                if not self.format_expr:
                    raise ValueError("Brak definicji formatu do pobrania.")
                target = self._service.download(
                    self.url,
                    self.format_expr,
                    self.desired_ext,
                    self.kind,
                    self.output_dir,
                    _progress,
                    _log,
                )
                if not self._cancelled:
                    self.download_finished.emit(target)
            else:
                raise ValueError("Nieznana akcja.")
        except Exception as e:
            self.download_error.emit(str(e))
        finally:
            self.finished.emit()
