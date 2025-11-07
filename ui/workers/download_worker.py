# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService
from ui.i18n.translator import tr


class DownloadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    progress_pct = QtCore.pyqtSignal(int)
    meta_ready = QtCore.pyqtSignal(object)
    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        action: str,
        url: str,
        kind: Optional[str] = None,
        quality: Optional[str] = None,
        ext: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._action = action
        self._url = url
        self._kind = kind
        self._quality = quality
        self._ext = ext
        self._svc = DownloadService()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action == "probe":
                self._do_probe()
            elif self._action == "download":
                self._do_download()
            else:
                self.download_error.emit(tr("down.log.error", msg=f"Nieznana akcja: {self._action}"))
        except Exception as e:
            self.download_error.emit(str(e))
        finally:
            self.finished.emit()

    def _do_probe(self) -> None:
        self.progress_log.emit(tr("down.log.analyze"))
        meta = self._svc.probe(self._url, log=lambda m: None)
        self.meta_ready.emit(meta)
        self.progress_log.emit(tr("down.log.meta_ready"))

    def _do_download(self) -> None:
        def _on_progress(pct: int, stage: str) -> None:
            self.progress_pct.emit(max(0, min(100, int(pct))))

        self.progress_log.emit(tr("down.log.downloading"))
        path = self._svc.download(
            url=self._url,
            kind=self._kind or "video",
            quality=self._quality or "auto",
            ext=self._ext or "mp4",
            out_dir=Config.DOWNLOADS_DIR,
            progress_cb=_on_progress,
            log=lambda m: None,
        )
        if not path:
            self.download_error.emit(tr("down.log.error", msg="Brak pliku wyjściowego."))
            return
        self.progress_pct.emit(100)
        self.progress_log.emit(tr("down.log.downloaded", path=str(path)))
        self.download_finished.emit(path)
