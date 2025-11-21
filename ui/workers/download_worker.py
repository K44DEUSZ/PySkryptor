# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService, DownloadError
from core.utils.text import sanitize_filename
from ui.i18n.translator import tr


class DownloadWorker(QtCore.QObject):
    progress_log = QtCore.pyqtSignal(str)
    progress_pct = QtCore.pyqtSignal(int)

    meta_ready = QtCore.pyqtSignal(object)
    download_already_exists = QtCore.pyqtSignal(Path, str)  # (path, title)  [legacy]
    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    # NEW: rendezvous for duplicate handling in panel
    duplicate_check = QtCore.pyqtSignal(str, str)  # (title, existing_path)

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
        self._cancelled = False

        # duplicate rendezvous state
        self._dup_decision_action: Optional[str] = None  # "skip" | "overwrite" | "rename"
        self._dup_decision_name: str = ""

    # ----- API ---------------------------------------------------------------

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
            self.download_error.emit(tr(de.key, **de.params))
        except Exception as e:
            self.download_error.emit(tr("down.log.error", msg=str(e)))
        finally:
            self.finished.emit()

    @QtCore.pyqtSlot()
    def cancel(self) -> None:
        self._cancelled = True

    @QtCore.pyqtSlot(str, str)
    def on_duplicate_decided(self, action: str, new_name: str) -> None:
        self._dup_decision_action = action
        self._dup_decision_name = new_name

    # ----- Steps -------------------------------------------------------------

    def _do_probe(self) -> None:
        self.progress_log.emit(tr("down.log.analyze"))
        meta = self._svc.probe(self._url, log=lambda m: None)
        self.meta_ready.emit(meta)
        # do NOT log meta_ready here; panel will log it once

    def _do_download(self) -> None:
        def _on_progress(pct: int, stage: str) -> None:
            self.progress_pct.emit(max(0, min(100, int(pct))))

        kind = (self._kind or "video").lower()
        ext = (self._ext or "mp4").lower()
        if kind == "video" and ext in ("mp3", "m4a"):
            ext = "mp4"

        # duplicate check (ask panel once, before hitting network)
        try:
            meta = self._svc.probe(self._url, log=lambda _: None)
            title = str(meta.get("title") or "")
            if title:
                safe = sanitize_filename(title)
                candidate = Config.DOWNLOADS_DIR / f"{safe}.{ext}"

                # if a file with same stem+ext exists → ask UI what to do
                existing: Optional[Path] = None
                if candidate.exists():
                    existing = candidate
                else:
                    # any file sharing the stem?
                    for p in Config.DOWNLOADS_DIR.glob(f"{safe}.*"):
                        if p.is_file():
                            existing = p
                            break

                if existing:
                    self.duplicate_check.emit(title, str(existing))
                    # wait until panel replies
                    while self._dup_decision_action is None:
                        QtCore.QThread.msleep(10)

                    if self._dup_decision_action == "skip":
                        self.download_error.emit(tr("down.log.error", msg=tr("down.dialog.exists.skip")))
                        return
                    if self._dup_decision_action == "rename":
                        # rename target by new stem; DownloadService will still decide final path
                        safe = sanitize_filename(self._dup_decision_name or safe)
                        # nothing else here; outtmpl uses %(title)s so the rename happens after download

        except Exception:
            # ignore duplicate pre-check failures; we'll proceed with download
            pass

        self.progress_log.emit(tr("down.log.downloading"))
        path = self._svc.download(
            url=self._url,
            kind=kind,
            quality=(self._quality or "auto"),
            ext=ext,
            out_dir=Config.DOWNLOADS_DIR,
            progress_cb=_on_progress,
            log=lambda m: None,
        )
        if not path:
            self.download_error.emit(tr("down.log.error", msg="No output file."))
            return
        if not self._cancelled:
            self.progress_pct.emit(100)
            self.download_finished.emit(path)
