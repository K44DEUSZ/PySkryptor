# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService
from core.utils.concurrency import CancellationToken
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
        self._token = CancellationToken()

    # ---------- Cancellation ----------

    @QtCore.pyqtSlot()
    def cancel(self) -> None:
        self._token.cancel()

    def _is_cancelled(self) -> bool:
        # token OR thread-level interruption
        if self._token.is_cancelled:
            return True
        th = QtCore.QThread.currentThread()
        return bool(th and th.isInterruptionRequested())

    # ---------- Main ----------

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action not in ("probe", "download"):
                self.download_error.emit(tr("error.config.unknown_action", action=self._action))
                return

            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
                return

            if self._action == "probe":
                self._do_probe()
            else:
                self._do_download()
        except Exception as e:
            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
            else:
                self.download_error.emit(tr("error.down.download_failed", detail=str(e)))
        finally:
            self.finished.emit()

    def _do_probe(self) -> None:
        try:
            self.progress_log.emit(tr("down.log.analyze"))
            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
                return
            meta = self._svc.probe(self._url, log=lambda m: None)
            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
                return
            self.meta_ready.emit(meta)
            self.progress_log.emit(tr("down.log.meta_ready"))
        except Exception as e:
            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
            else:
                self.download_error.emit(tr("error.down.probe_failed", detail=str(e)))

    def _do_download(self) -> None:
        def _on_progress(pct: int, _stage: str) -> None:
            # If cancelled mid-download: stop yt_dlp by raising
            if self._is_cancelled():
                raise RuntimeError("cancelled")
            self.progress_pct.emit(max(0, min(100, int(pct))))

        try:
            self.progress_log.emit(tr("down.log.downloading"))
            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
                return

            path = self._svc.download(
                url=self._url,
                kind=(self._kind or "video"),
                quality=(self._quality or "auto"),
                ext=(self._ext or "mp4"),
                out_dir=Config.DOWNLOADS_DIR,
                progress_cb=_on_progress,
                log=lambda m: None,
            )
            if self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
                return

            if not path:
                self.download_error.emit(tr("error.down.download_failed", detail="no output file"))
                return

            self.progress_pct.emit(100)
            self.progress_log.emit(tr("down.log.downloaded", path=str(path)))
            self.download_finished.emit(path)
        except Exception as e:
            if str(e).lower().strip() == "cancelled" or self._is_cancelled():
                self.progress_log.emit(tr("down.log.cancelled"))
            else:
                self.download_error.emit(tr("error.down.download_failed", detail=str(e)))
