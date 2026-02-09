# controller/tasks/download_task.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from model.config.app_config import AppConfig as Config
from model.io.text import sanitize_filename
from model.services.download_service import DownloadService
from view.utils.concurrency import CancellationToken


class DownloadWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    progress_log = QtCore.pyqtSignal(str)

    meta_ready = QtCore.pyqtSignal(dict)

    progress_pct = QtCore.pyqtSignal(int)
    duplicate_check = QtCore.pyqtSignal(str, str)

    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str)

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
        self._action = (action or "").strip().lower()
        self._url = (url or "").strip()
        self._kind = (kind or "video").strip().lower()
        self._quality = (quality or "auto").strip().lower()
        self._ext = (ext or "mp4").strip().lower()
        self._audio_lang = audio_lang

        self._cancel = CancellationToken()

        self._dup_mutex = QtCore.QMutex()
        self._dup_wait = QtCore.QWaitCondition()
        self._dup_action: Optional[str] = None
        self._dup_new_name: str = ""

    def cancel(self) -> None:
        self._cancel.cancel()
        try:
            self._dup_mutex.lock()
            self._dup_wait.wakeAll()
        finally:
            self._dup_mutex.unlock()

    def on_duplicate_decided(self, action: str, new_name: str = "") -> None:
        try:
            self._dup_mutex.lock()
            self._dup_action = (action or "").strip().lower()
            self._dup_new_name = (new_name or "").strip()
            self._dup_wait.wakeAll()
        finally:
            self._dup_mutex.unlock()

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            if not self._url:
                self.download_error.emit("missing url")
                return

            svc = DownloadService()

            if self._action == "probe":
                meta = svc.probe(self._url, log=lambda m: self.progress_log.emit(str(m)))
                if isinstance(meta, dict):
                    self.meta_ready.emit(meta)
                return

            if self._action != "download":
                self.download_error.emit(f"unsupported action: {self._action}")
                return

            meta = svc.probe(self._url, log=lambda m: self.progress_log.emit(str(m)))
            title = str(meta.get("title") or meta.get("id") or "download")
            out_dir = Config.DOWNLOADS_DIR
            out_dir.mkdir(parents=True, exist_ok=True)

            stem = sanitize_filename(title) or "download"
            expected = out_dir / f"{stem}.{self._ext}"

            stem = self._resolve_duplicate(title, stem, expected)
            if not stem:
                return  # skipped / cancelled

            def _progress_cb(pct: float) -> None:
                try:
                    v = int(max(0, min(100, round(float(pct)))))
                    self.progress_pct.emit(v)
                except Exception:
                    pass

            def _cancel_check() -> bool:
                return self._cancel.is_cancelled

            path = svc.download(
                url=self._url,
                kind=self._kind,
                quality=self._quality,
                ext=self._ext,
                out_dir=out_dir,
                audio_lang=self._audio_lang,
                file_stem=stem,
                progress_cb=_progress_cb,
                log=lambda m: self.progress_log.emit(str(m)),
                cancel_check=_cancel_check,
            )

            if self._cancel.is_cancelled or path is None:
                return

            self.download_finished.emit(path)

        except Exception as ex:
            self.download_error.emit(str(ex))
        finally:
            self.finished.emit()

    def _resolve_duplicate(self, title: str, stem: str, expected: Path) -> str:
        if self._cancel.is_cancelled:
            return ""

        if not expected.exists():
            return stem

        self.duplicate_check.emit(title, str(expected))

        while not self._cancel.is_cancelled:
            if QtCore.QThread.currentThread().isInterruptionRequested():
                self._cancel.cancel()
                break

            try:
                self._dup_mutex.lock()
                if self._dup_action is None:
                    self._dup_wait.wait(self._dup_mutex, 150)
                action = self._dup_action
                new_name = self._dup_new_name
            finally:
                self._dup_mutex.unlock()

            if action is None:
                continue

            action = action.strip().lower()
            if action == "skip":
                return ""
            if action == "overwrite":
                try:
                    expected.unlink(missing_ok=True)
                except TypeError:
                    try:
                        if expected.exists():
                            expected.unlink()
                    except Exception:
                        pass
                return stem
            if action == "rename":
                cand = sanitize_filename(new_name) if new_name else ""
                if not cand:
                    cand = stem

                cand_path = expected.with_name(f"{cand}.{self._ext}")
                if not cand_path.exists():
                    return cand

                base = cand
                for i in range(2, 1000):
                    alt = f"{base}-{i}"
                    alt_path = expected.with_name(f"{alt}.{self._ext}")
                    if not alt_path.exists():
                        return alt
                return base

            return ""

        return ""
