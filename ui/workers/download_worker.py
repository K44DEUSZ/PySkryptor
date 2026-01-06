# ui/workers/download_worker.py
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from core.config.app_config import AppConfig as Config
from core.services.download_service import DownloadService, DownloadError, DownloadCancelled
from core.services.media_metadata import MediaMetadataService
from ui.utils.translating import tr


class DownloadWorker(QtCore.QObject):
    """Background worker for probing and downloading URLs."""

    meta_ready = QtCore.pyqtSignal(dict)
    progress_log = QtCore.pyqtSignal(str)

    progress_pct = QtCore.pyqtSignal(int)
    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()

    duplicate_check = QtCore.pyqtSignal(str, str)  # (title, existing_path)

    def __init__(
        self,
        *,
        action: str,
        url: str,
        kind: Optional[str] = None,
        quality: Optional[str] = None,
        ext: Optional[str] = None,
        audio_lang: Optional[str] = None,
    ) -> None:
        super().__init__()

        self._action = action
        self._url = url
        self._kind = kind
        self._quality = quality
        self._ext = ext
        self._audio_lang = audio_lang

        self._svc = DownloadService()
        self._meta = MediaMetadataService()

        self._cancel_event = threading.Event()
        self._cancel_logged = False

        self._dup_decision_action: Optional[str] = None  # "skip" | "overwrite" | "rename"
        self._dup_decision_name: str = ""
        self._file_stem: Optional[str] = None
        self._overwrite_existing: bool = False

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if self._action == "probe":
                self._do_probe()
            elif self._action == "download":
                self._do_download()
            else:
                msg = tr("error.config.unknown_action", action=self._action)
                self.download_error.emit(tr("down.log.error", msg=msg))

        except DownloadCancelled:
            if not self._cancel_logged:
                self.progress_log.emit(tr("log.cancelled"))

        except DownloadError as de:
            self.download_error.emit(tr(de.key, **de.params))

        except Exception as e:
            self.download_error.emit(tr("down.log.error", msg=str(e)))

        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancel_event.set()
        if not self._cancel_logged:
            self._cancel_logged = True
            try:
                self.progress_log.emit(tr("log.cancelled"))
            except Exception:
                pass

    def on_duplicate_decided(self, action: str, new_name: str) -> None:
        self._dup_decision_action = action
        self._dup_decision_name = new_name

    def _do_probe(self) -> None:
        self.progress_log.emit(tr("down.log.analyze"))

        meta_obj = self._meta.from_url(self._url, log=lambda _m: None)

        payload = {
            "title": meta_obj.title,
            "duration": meta_obj.duration,
            "filesize": meta_obj.size,
            "extractor": meta_obj.service,
            "formats": meta_obj.formats or [],
        }
        if meta_obj.audio_langs:
            payload["audio_langs"] = meta_obj.audio_langs

        self.meta_ready.emit(payload)

    @staticmethod
    def _ensure_dir(p: Path) -> None:
        p.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_unlink(p: Path) -> None:
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    def _do_download(self) -> None:
        self._dup_decision_action = None
        self._dup_decision_name = ""
        self._file_stem = None
        self._overwrite_existing = False

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

        kind = (self._kind or "video").lower()
        ext = (self._ext or "mp4").lower()
        if kind == "video" and ext in ("mp3", "m4a"):
            ext = "mp4"

        final_dir = Config.DOWNLOADS_DIR
        tmp_dir = final_dir / "_tmp"
        self._ensure_dir(final_dir)
        self._ensure_dir(tmp_dir)

        # ----- Duplicate pre-check (in final_dir) -----
        try:
            title, predicted = self._svc.predict_output_path(
                url=self._url,
                kind=kind,
                quality=(self._quality or "auto"),
                ext=ext,
                out_dir=final_dir,
                audio_lang=self._audio_lang,
                file_stem=None,
                log=lambda _m: None,
            )

            existing: Optional[Path] = None
            if predicted and predicted.exists():
                existing = predicted
            elif predicted:
                candidates = list(final_dir.glob(f"{predicted.stem}.*"))
                candidates = [p for p in candidates if p.is_file()]
                if candidates:
                    existing = max(candidates, key=lambda p: p.stat().st_mtime)

            if existing:
                self.duplicate_check.emit(title or existing.stem or self._url, str(existing))

                waited_ms = 0
                timeout_ms = 15_000
                while (
                    not self._cancel_event.is_set()
                    and self._dup_decision_action is None
                    and waited_ms < timeout_ms
                ):
                    QtCore.QThread.msleep(10)
                    waited_ms += 10

                if self._cancel_event.is_set():
                    return

                if self._dup_decision_action is None:
                    raise DownloadError("error.down.duplicate_timeout")

                if self._dup_decision_action == "skip":
                    self.progress_log.emit(tr("status.skipped"))
                    return

                if self._dup_decision_action == "overwrite":
                    self._overwrite_existing = True

                if self._dup_decision_action == "rename":
                    new_name = (self._dup_decision_name or "").strip()
                    if new_name:
                        self._file_stem = new_name

        except DownloadError:
            raise
        except Exception:
            # Ignore pre-check errors and proceed with download.
            pass

        if self._cancel_event.is_set():
            return

        # Download into tmp_dir so partials never appear in final downloads view.
        tmp_path = self._svc.download(
            url=self._url,
            kind=kind,
            quality=(self._quality or "auto"),
            ext=ext,
            out_dir=tmp_dir,
            progress_cb=_on_progress,
            log=lambda _m: None,
            audio_lang=self._audio_lang,
            file_stem=self._file_stem,
            cancel_check=self._cancel_event.is_set,
        )

        if self._cancel_event.is_set():
            return

        if not tmp_path:
            raise DownloadError("error.down.no_output_file")

        # Move to final_dir
        final_path = final_dir / tmp_path.name

        if final_path.exists():
            if self._overwrite_existing:
                self._safe_unlink(final_path)
            else:
                # Should not happen if dialog logic worked, but keep it safe.
                raise DownloadError("error.down.duplicate_exists", path=str(final_path))

        try:
            tmp_path.replace(final_path)
        except Exception:
            # cross-device fallback
            try:
                data = tmp_path.read_bytes()
                final_path.write_bytes(data)
                self._safe_unlink(tmp_path)
            except Exception as e:
                raise DownloadError("error.down.move_failed", detail=str(e))

        # Best-effort cleanup: remove stray files for this stem in tmp_dir.
        try:
            for p in tmp_dir.glob(f"{tmp_path.stem}*"):
                if p.is_file():
                    self._safe_unlink(p)
            # If empty, remove tmp_dir
            if not any(tmp_dir.iterdir()):
                tmp_dir.rmdir()
        except Exception:
            pass

        try:
            self.progress_pct.emit(100)
            self.download_finished.emit(final_path)
        except Exception:
            pass
