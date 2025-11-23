# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService, DownloadError
from core.utils.text import sanitize_filename
from ui.utils.translating import tr


class DownloadWorker(QtCore.QObject):
    """Background worker for probing and downloading media via DownloadService."""

    progress_log = QtCore.pyqtSignal(str)
    progress_pct = QtCore.pyqtSignal(int)

    meta_ready = QtCore.pyqtSignal(object)
    download_already_exists = QtCore.pyqtSignal(Path, str)  # (path, title)  [legacy]
    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    # duplicate handling with GUI
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


    # ----- API -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        """Entry point for the worker thread."""
        try:
            if self._action == "probe":
                self._do_probe()
            elif self._action == "download":
                self._do_download()
            else:
                msg = tr("error.config.unknown_action", action=self._action)
                self.download_error.emit(tr("down.log.error", msg=msg))
        except DownloadError as de:
            self.download_error.emit(tr(de.key, **de.params))
        except Exception as e:
            self.download_error.emit(tr("down.log.error", msg=str(e)))
        finally:
            self.finished.emit()


    @QtCore.pyqtSlot()
    def cancel(self) -> None:
        """Mark current operation as cancelled (best-effort)."""
        self._cancelled = True


    @QtCore.pyqtSlot(str, str)
    def on_duplicate_decided(self, action: str, new_name: str) -> None:
        """Callback from GUI with user decision on duplicate file."""
        self._dup_decision_action = action
        self._dup_decision_name = new_name


    # ----- Steps -----

    def _do_probe(self) -> None:
        """Probe URL metadata and emit meta_ready."""
        self.progress_log.emit(tr("down.log.analyze"))
        meta = self._svc.probe(self._url, log=lambda m: None)
        self.meta_ready.emit(meta)


    def _do_download(self) -> None:
        """Run download with duplicate handling and progress callback."""
        seen_stage_downloading = False
        seen_stage_post = False

        def _on_progress(pct: int, stage: str) -> None:
            nonlocal seen_stage_downloading, seen_stage_post

            # progress bar
            self.progress_pct.emit(max(0, min(100, int(pct))))

            # stage logs (localized via JSON)
            if stage == "downloading" and not seen_stage_downloading:
                self.progress_log.emit(tr("down.log.downloading"))
                seen_stage_downloading = True
            elif stage in ("finished", "postprocess") and not seen_stage_post:
                self.progress_log.emit(tr("down.log.postprocess"))
                seen_stage_post = True

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
                        # wrap inner message from dialog key, outer from down.log.error
                        msg = tr("down.dialog.exists.skip")
                        self.download_error.emit(tr("down.log.error", msg=msg))
                        return
                    if self._dup_decision_action == "rename":
                        # rename target by new stem; DownloadService still decides final path
                        safe = sanitize_filename(self._dup_decision_name or safe)

        except Exception:
            # ignore duplicate pre-check failures; we'll proceed with download
            pass

        # actual download (progress + logging via callback)
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
            detail = tr("error.down.no_output_file") if "error.down.no_output_file" else ""
            self.download_error.emit(tr("error.down.download_failed", detail=detail))
            return

        if not self._cancelled:
            self.progress_pct.emit(100)
            self.download_finished.emit(path)
