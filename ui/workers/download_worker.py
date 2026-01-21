# ui/workers/download_worker.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.io.text import sanitize_filename
from core.services.download_service import DownloadService, DownloadError, DownloadCancelled
from ui.utils.translating import tr


class DownloadWorker(QtCore.QObject):
    """Downloader tab worker.

    This class must match the signal/slot contract used by ui/views/downloader_panel.py:
    - action="probe"  -> emits meta_ready(dict)
    - action="download" -> emits progress + download_finished(Path)
    """

    progress_log = QtCore.pyqtSignal(str)
    progress_pct = QtCore.pyqtSignal(int)
    meta_ready = QtCore.pyqtSignal(dict)
    duplicate_check = QtCore.pyqtSignal(str, str)  # title, existing_path
    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        action: str,
        url: str,
        kind: str = "video",
        quality: str = "auto",
        ext: str = "mp4",
        audio_lang: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._svc = DownloadService()
        self._action = (action or "").strip().lower()
        self._url = (url or "").strip()

        self._kind = (kind or "video").strip().lower()
        self._quality = (quality or "auto").strip().lower()
        self._ext = (ext or "mp4").strip().lower()
        self._audio_lang = audio_lang

        self._cancelled = False

        # Duplicate decision rendezvous
        self._dup_decision_action: Optional[str] = None  # skip | overwrite | rename
        self._dup_decision_name: str = ""

    # ----- Public control -----

    def cancel(self) -> None:
        """Best-effort cancellation used by the UI."""
        self._cancelled = True

    def on_duplicate_decided(self, action: str, new_name: str) -> None:
        """Callback from UI duplicate dialog."""
        self._dup_decision_action = (action or "skip").strip().lower()
        self._dup_decision_name = (new_name or "").strip()

    def _is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        try:
            return bool(QtCore.QThread.currentThread().isInterruptionRequested())
        except Exception:
            return False

    # ----- Worker entrypoint -----

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action == "probe":
                self._do_probe()
            elif self._action == "download":
                self._do_download()
            else:
                self.download_error.emit(
                    tr("error.down.download_failed", detail=f"Invalid action: {self._action}")
                )
        finally:
            self.finished.emit()

    # ----- Probe -----

    def _do_probe(self) -> None:
        if self._is_cancelled():
            return

        try:
            self.progress_log.emit(tr("down.log.analyze"))
            meta = self._svc.probe(self._url, log=lambda _m: None)

            payload: Dict[str, Any] = {
                "title": meta.get("title"),
                "duration": meta.get("duration"),
                "filesize": meta.get("filesize") or meta.get("filesize_approx"),
                "extractor": meta.get("extractor"),
                "formats": meta.get("formats") or [],
                "audio_tracks": meta.get("audio_tracks") or [],
            }
            self.meta_ready.emit(payload)
        except DownloadError as de:
            self.download_error.emit(tr(de.key, **de.params))
        except Exception as e:
            self.download_error.emit(tr("error.down.probe_failed", detail=str(e)))

    # ----- Duplicate detection helpers -----

    @staticmethod
    def _pick_existing(stem: str) -> Optional[Path]:
        root = Config.DOWNLOADS_DIR
        candidates = [p for p in root.glob(f"{stem}.*") if p.is_file()]
        if not candidates:
            return None
        try:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        except Exception:
            return candidates[0]

    @staticmethod
    def _remove_existing(stem: str) -> None:
        root = Config.DOWNLOADS_DIR
        for p in root.glob(f"{stem}.*"):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass

    def _wait_for_duplicate_decision(self) -> Optional[str]:
        waited_ms = 0
        timeout_ms = 15_000
        while (
            not self._is_cancelled()
            and self._dup_decision_action is None
            and waited_ms < timeout_ms
        ):
            QtCore.QThread.msleep(10)
            waited_ms += 10
        return self._dup_decision_action

    # ----- Download -----

    def _do_download(self) -> None:
        if self._is_cancelled():
            return

        self._dup_decision_action = None
        self._dup_decision_name = ""

        # Probe title for nicer duplicate handling.
        title = self._url
        try:
            meta = self._svc.probe(self._url, log=lambda _m: None)
            title = meta.get("title") or title
        except Exception:
            pass

        stem = sanitize_filename(Path(title).stem) or "download"
        existing = self._pick_existing(stem)
        if existing is not None:
            self.duplicate_check.emit(title, str(existing))
            decision = self._wait_for_duplicate_decision()

            if self._is_cancelled():
                return

            if decision is None:
                self.download_error.emit(tr("error.down.duplicate_timeout"))
                return

            if decision == "skip":
                self.progress_log.emit(tr("status.skipped"))
                self.progress_pct.emit(0)
                return

            if decision == "overwrite":
                self._remove_existing(stem)

            if decision == "rename":
                new_name = sanitize_filename(self._dup_decision_name) or "download"
                stem = new_name

        seen_stage_downloading = False
        seen_stage_post = False

        def _on_progress(pct: int, stage: str) -> None:
            nonlocal seen_stage_downloading, seen_stage_post

            try:
                self.progress_pct.emit(int(pct))
            except Exception:
                pass

            try:
                if stage == "downloading" and not seen_stage_downloading:
                    self.progress_log.emit(tr("down.log.downloading"))
                    seen_stage_downloading = True
                elif stage in ("finished", "postprocess") and not seen_stage_post:
                    self.progress_log.emit(tr("down.log.postprocess"))
                    seen_stage_post = True
            except Exception:
                pass

        try:
            path = self._svc.download(
                url=self._url,
                kind=self._kind,
                quality=self._quality,
                ext=self._ext,
                out_dir=Config.DOWNLOADS_DIR,
                progress_cb=_on_progress,
                log=lambda _m: None,
                audio_lang=self._audio_lang,
                file_stem=stem,
                cancel_check=self._is_cancelled,
            )

            if not path:
                self.download_error.emit(tr("error.down.no_output_file"))
                return

            self.progress_pct.emit(100)
            self.download_finished.emit(path)

        except DownloadCancelled:
            self.progress_log.emit(tr("log.cancelled"))
            self.progress_pct.emit(0)
        except DownloadError as de:
            self.download_error.emit(tr(de.key, **de.params))
        except Exception as e:
            self.download_error.emit(tr("error.down.download_failed", detail=str(e)))
