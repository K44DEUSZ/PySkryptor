# app/controller/tasks/download_task.py
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.tasks.base_worker import BaseWorker, PendingDecision
from app.model.config.app_config import AppConfig as Config
from app.model.helpers.string_utils import sanitize_filename
from app.model.io.file_manager import FileManager
from app.model.services.download_service import DownloadService, DownloadError

_LOG = logging.getLogger(__name__)


class DownloadWorker(BaseWorker):

    meta_ready = QtCore.pyqtSignal(dict)

    progress_pct = QtCore.pyqtSignal(int)
    stage_changed = QtCore.pyqtSignal(str)
    duplicate_check = QtCore.pyqtSignal(str, str)

    download_finished = QtCore.pyqtSignal(Path)
    download_error = QtCore.pyqtSignal(str, dict)

    def __init__(
        self,
        *,
        action: str,
        url: str,
        kind: Optional[str] = None,
        quality: Optional[str] = None,
        ext: Optional[str] = None,
        audio_lang: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._action = str(action or '').strip().lower()
        self._url = str(url or '').strip()

        self._kind = str(kind or '').strip().lower()
        self._quality = str(quality or '').strip().lower()
        self._ext = str(ext or '').strip().lower()
        self._audio_lang = audio_lang

        self._duplicate_decision = PendingDecision(default_action="skip")
        self._duplicate_lock = threading.Lock()

    def cancel(self) -> None:
        super().cancel()
        with self._duplicate_lock:
            self._cancel_pending_decision(self._duplicate_decision)

    @QtCore.pyqtSlot(str, str)
    def on_duplicate_decided(self, action: str, new_name: str = '') -> None:
        with self._duplicate_lock:
            self._set_pending_decision(
                self._duplicate_decision,
                action=str(action or '').strip().lower(),
                value=str(new_name or '').strip(),
            )

    # ----- Errors -----

    def _handle_failure(self, ex: BaseException) -> None:
        key, params = self._exception_to_i18n(ex)
        self._emit_failure(str(key), dict(params or {}), self.download_error)

    # ----- Core -----

    def _execute(self) -> None:
        if not self._url:
            self.download_error.emit('error.generic', {'detail': 'missing url'})
            return

        svc = DownloadService()

        if self._action == 'probe':
            meta = svc.probe(self._url)
            if isinstance(meta, dict):
                self.meta_ready.emit(meta)
            return

        if self._action != 'download':
            self.download_error.emit('error.generic', {'detail': f'unsupported action: {self._action}'})
            return

        if not self._kind or not self._quality or not self._ext:
            self.download_error.emit('error.generic', {'detail': 'missing download options'})
            return

        meta = svc.probe(self._url)
        title = str(meta.get('title') or meta.get('id') or 'download')
        extractor = str(meta.get('extractor') or '').strip()
        source_id = str(meta.get('id') or '').strip()

        if extractor and source_id:
            key = f'{extractor}-{source_id}'
        else:
            h = hashlib.sha1(self._url.encode('utf-8', errors='ignore')).hexdigest()[:10]
            key = f'url-{h}'

        title_stem = sanitize_filename(title) or 'download'
        key_stem = sanitize_filename(key) or key
        file_stem = f'{title_stem} [{key_stem}]'

        out_dir = Config.DOWNLOADS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        expected = out_dir / f'{file_stem}.{self._ext}'
        final_stem = self._resolve_duplicate(title, expected)

        if not final_stem or self._cancel.is_cancelled:
            return

        def _progress_cb(pct: int, status: str) -> None:
            try:
                st = str(status or '').strip().lower()
                if st == 'postprocessing':
                    self.stage_changed.emit('postprocessing')
                    return
                if st == 'postprocessed':
                    self.stage_changed.emit('postprocessed')
                    return
                v = int(max(0, min(100, int(pct))))
                self.progress_pct.emit(v)
            except Exception:
                return

        path = svc.download(
            url=self._url,
            kind=self._kind,
            quality=self._quality,
            ext=self._ext,
            out_dir=out_dir,
            audio_lang=self._audio_lang,
            file_stem=final_stem,
            progress_cb=_progress_cb,
            cancel_check=lambda: self._cancel.is_cancelled,
            meta=meta,
        )

        if self._cancel.is_cancelled:
            return

        if path is None:
            _LOG.warning(
                "Download worker finished without output path. url=%s kind=%s quality=%s ext=%s",
                self._url,
                self._kind,
                self._quality,
                self._ext,
            )
            raise DownloadError("error.down.download_failed", detail="download returned no output path")

        self.download_finished.emit(path)

    # ----- Duplicate -----

    def _resolve_duplicate(self, title: str, expected: Path) -> str:
        if self._cancel.is_cancelled:
            return ''

        if not expected.exists():
            return expected.stem

        with self._duplicate_lock:
            self._duplicate_decision.reset()
        self.duplicate_check.emit(str(title), str(expected))
        action, new_name = self._wait_for_pending_decision(self._duplicate_decision)

        if self.cancel_check():
            return ''

        action = str(action or '').strip().lower()
        if action == 'skip':
            return ''

        if action == 'overwrite':
            self._remove_existing(expected)
            return expected.stem

        if action == 'rename':
            cand = sanitize_filename(new_name) if new_name else ''
            cand_path = expected.with_name(f'{cand or expected.stem}{expected.suffix}')
            unique = FileManager.ensure_unique_path(cand_path)
            return unique.stem

        return ''

    @staticmethod
    def _remove_existing(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

        part = Path(str(path) + '.part')
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass
