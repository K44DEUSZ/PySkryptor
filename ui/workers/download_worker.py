# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService, DownloadError
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
        self._action = (action or "").strip().lower()
        self._url = url.strip()
        self._kind = (kind or "").strip().lower() or "video"
        self._quality = (quality or "").strip().lower() or "auto"
        self._ext = (ext or "").strip().lower() or "mp4"
        self._svc = DownloadService()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action == "probe":
                self._do_probe()
            elif self._action == "download":
                self._do_download()
            else:
                self.download_error.emit(tr("down.log.error", msg=f"Unknown action: {self._action}"))
        except DownloadError as de:
            # Map service errors to translated messages
            self.download_error.emit(tr(de.key, **de.params))
        except Exception as e:
            self.download_error.emit(tr("down.log.error", msg=str(e)))
        finally:
            self.finished.emit()

    # ---------- Probe ----------

    def _do_probe(self) -> None:
        self.progress_log.emit(tr("down.log.analyze"))
        meta = self._svc.probe(self._url, log=lambda _m: None)
        self.meta_ready.emit(meta)
        self.progress_log.emit(tr("down.log.meta_ready"))

    # ---------- Download ----------

    def _do_download(self) -> None:
        def _on_progress(pct: int, _stage: str) -> None:
            self.progress_pct.emit(max(0, min(100, int(pct))))

        kind = self._kind
        ext = self._ext
        if kind == "video" and ext in ("mp3", "m4a"):
            ext = "mp4"

        self.progress_log.emit(tr("down.log.downloading"))
        path = self._svc.download(
            url=self._url,
            kind=kind,
            quality=self._quality,
            ext=ext,
            out_dir=Config.DOWNLOADS_DIR,
            progress_cb=_on_progress,
            log=lambda _m: None,
        )
        if not path:
            self.download_error.emit(tr("down.log.error", msg="No output file."))
            return

        self.progress_pct.emit(100)
        self.download_finished.emit(path)
