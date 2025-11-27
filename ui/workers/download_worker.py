# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService, DownloadError
from core.services.media_metadata import MediaMetadataService
from core.io.text import sanitize_filename
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

    # Duplicate handling rendezvous with GUI
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
        self._meta = MediaMetadataService(self._svc)

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
                # Unknown action – just log a localized error instead of raising.
                msg = tr("error.config.unknown_action", action=self._action)
                self.download_error.emit(tr("down.log.error", msg=msg))
        except DownloadError as de:
            # Controlled errors from DownloadService – localize by key.
            self.download_error.emit(tr(de.key, **de.params))
        except Exception as e:
            # Any unexpected error – keep app alive, show localized wrapper.
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

        meta_obj = self._meta.from_url(self._url, log=lambda _m: None)

        payload = {
            "title": meta_obj.title,
            "duration": meta_obj.duration,
            "filesize": meta_obj.size,
            "extractor": meta_obj.service,
            "formats": meta_obj.formats or [],
        }
        # If DownloadService.probe adds audio_langs in the future, we will pass it through.
        if meta_obj.audio_langs:
            payload["audio_langs"] = meta_obj.audio_langs

        self.meta_ready.emit(payload)

    def _do_download(self) -> None:
        """Run download with optional duplicate handling and progress callback."""
        # Reset duplicate decision for this run
        self._dup_decision_action = None
        self._dup_decision_name = ""

        seen_stage_downloading = False
        seen_stage_post = False

        def _on_progress(pct: int, stage: str) -> None:
            nonlocal seen_stage_downloading, seen_stage_post

            if self._cancelled:
                return

            # progress bar
            try:
                self.progress_pct.emit(max(0, min(100, int(pct))))
            except Exception:
                # Never let a UI issue kill the worker
                pass

            # stage logs (localized via JSON)
            try:
                if stage == "downloading" and not seen_stage_downloading:
                    self.progress_log.emit(tr("down.log.downloading"))
                    seen_stage_downloading = True
                elif stage in ("finished", "postprocess") and not seen_stage_post:
                    self.progress_log.emit(tr("down.log.postprocess"))
                    seen_stage_post = True
            except Exception:
                pass

        kind = (self._kind or "video").lower()
        ext = (self._ext or "mp4").lower()
        if kind == "video" and ext in ("mp3", "m4a"):
            # Extra safety: do not try to mux "video" into pure-audio container.
            ext = "mp4"

        # ----- Duplicate pre-check -----
        try:
            meta_obj = self._meta.from_url(self._url, log=lambda _m: None)
            title = str(meta_obj.title or "").strip()
            if title:
                safe = sanitize_filename(title)
                candidate = Config.DOWNLOADS_DIR / f"{safe}.{ext}"

                existing: Optional[Path] = None
                if candidate.exists():
                    existing = candidate
                else:
                    for p in Config.DOWNLOADS_DIR.glob(f"{safe}.*"):
                        if p.is_file():
                            existing = p
                            break

                if existing:
                    # Ask GUI what to do.
                    self.duplicate_check.emit(title, str(existing))

                    # Wait for GUI response, but do not block forever.
                    waited_ms = 0
                    while (
                        not self._cancelled
                        and self._dup_decision_action is None
                        and waited_ms < 30_000
                    ):
                        QtCore.QThread.msleep(10)
                        waited_ms += 10

                    if self._cancelled:
                        # Treat as soft cancel – just return.
                        return

                    if self._dup_decision_action is None:
                        # No decision in time – fail gracefully.
                        msg = tr("down.log.error", msg="duplicate decision timeout")
                        self.download_error.emit(msg)
                        return

                    if self._dup_decision_action == "skip":
                        msg = tr("down.dialog.exists.skip")
                        self.download_error.emit(tr("down.log.error", msg=msg))
                        return

                    if self._dup_decision_action == "rename":
                        safe = sanitize_filename(self._dup_decision_name or safe)
                        # `safe` is only used to suggest the filename; yt_dlp still
                        # controls final path via outtmpl, so no extra work here.
        except Exception:
            # If duplicate pre-check fails, ignore and proceed with standard download.
            pass

        if self._cancelled:
            return

        # ----- Actual download -----
        path = self._svc.download(
            url=self._url,
            kind=kind,
            quality=(self._quality or "auto"),
            ext=ext,
            out_dir=Config.DOWNLOADS_DIR,
            progress_cb=_on_progress,
            log=lambda _m: None,
            audio_lang=None,  # GUI-specific language selection handled at panel level
        )

        if not path:
            detail = tr("error.down.no_output_file")
            self.download_error.emit(tr("error.down.download_failed", detail=detail))
            return

        if not self._cancelled:
            try:
                self.progress_pct.emit(100)
                self.download_finished.emit(path)
            except Exception:
                # Even if emitting fails, do not crash worker.
                pass
