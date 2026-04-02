# app/controller/workers/download_worker.py
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.workers.access_task_worker import AccessTaskWorker
from app.controller.workers.task_worker import PendingDecision
from app.model.core.config.config import AppConfig
from app.model.core.utils.path_utils import ensure_unique_path
from app.model.core.utils.string_utils import sanitize_filename
from app.model.download.domain import DownloadError, SourceAccessInterventionRequired
from app.model.download.service import DownloadService

_LOG = logging.getLogger(__name__)


class DownloadWorker(AccessTaskWorker):
    """Background worker that probes or downloads a single remote media source."""

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
        job_key: str = "",
        kind: str | None = None,
        quality: str | None = None,
        ext: str | None = None,
        audio_track_id: str | None = None,
        browser_cookies_mode_override: str | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._action = str(action or "").strip().lower()
        self._url = str(url or "").strip()
        self._job_key = str(job_key or self._url).strip() or self._url

        self._kind = str(kind or "").strip().lower()
        self._quality = str(quality or "").strip().lower()
        self._ext = str(ext or "").strip().lower()
        self._audio_track_id = str(audio_track_id or "").strip() or None
        self._browser_cookies_mode_override = str(browser_cookies_mode_override or "").strip().lower() or None
        self._cookie_file_override: str | None = None
        self._browser_policy_override: str | None = None
        self._access_mode_override: str | None = None

        self._duplicate_decision = PendingDecision(default_action="skip")
        self._duplicate_lock = threading.Lock()

    @property
    def job_key(self) -> str:
        return self._job_key

    def cancel(self) -> None:
        super().cancel()
        with self._duplicate_lock:
            self._cancel_pending_decision(self._duplicate_decision)

    @QtCore.pyqtSlot(str, str)
    def on_duplicate_decided(self, action: str, new_name: str = "") -> None:
        with self._duplicate_lock:
            self._set_pending_decision(
                self._duplicate_decision,
                action=str(action or "").strip().lower(),
                value=str(new_name or "").strip(),
            )

    def _emit_download_failure(self, key: str, params: dict[str, object] | None = None) -> None:
        self._emit_failure(str(key), dict(params or {}), self.download_error)

    def _handle_failure(self, ex: BaseException) -> None:
        key, params = self._exception_to_i18n(ex)
        self._emit_download_failure(str(key), dict(params or {}))

    def _probe(self, svc: DownloadService) -> dict[str, Any]:
        browser_cookies_mode_override = self._browser_cookies_mode_override
        cookie_file_override = self._cookie_file_override
        browser_policy_override = self._browser_policy_override
        access_mode_override = self._access_mode_override
        while True:
            try:
                meta = svc.probe(
                    self._url,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                    interactive=True,
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._browser_policy_override = browser_policy_override
                self._access_mode_override = access_mode_override
                return meta
            except SourceAccessInterventionRequired as ex:
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = self._next_access_intervention_overrides(
                    ex,
                    payload_key_name="job_key",
                    payload_key=self._job_key,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._browser_policy_override = browser_policy_override
                self._access_mode_override = access_mode_override
                continue
            except DownloadError as ex:
                intervention = DownloadService.intervention_request_from_error(
                    ex,
                    url=self._url,
                    operation="probe",
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                if intervention is None:
                    raise
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = self._next_access_intervention_overrides(
                    intervention,
                    payload_key_name="job_key",
                    payload_key=self._job_key,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._browser_policy_override = browser_policy_override
                self._access_mode_override = access_mode_override
                continue

        raise RuntimeError("Download probe intervention loop ended unexpectedly")

    def _execute_download(self, svc: DownloadService) -> Path | None:
        while True:
            meta = self._probe(svc)
            browser_cookies_mode_override = self._browser_cookies_mode_override
            cookie_file_override = self._cookie_file_override
            browser_policy_override = self._browser_policy_override
            access_mode_override = self._access_mode_override
            title = str(meta.get("title") or meta.get("id") or "download")
            extractor = str(meta.get("extractor") or "").strip()
            source_id = str(meta.get("id") or "").strip()

            if extractor and source_id:
                key = f"{extractor}-{source_id}"
            else:
                digest = hashlib.sha1(self._url.encode("utf-8", errors="ignore")).hexdigest()[:10]
                key = f"url-{digest}"

            title_stem = sanitize_filename(title) or "download"
            key_stem = sanitize_filename(key) or key
            file_stem = f"{title_stem} [{key_stem}]"

            out_dir = AppConfig.PATHS.DOWNLOADS_DIR
            out_dir.mkdir(parents=True, exist_ok=True)

            expected = out_dir / f"{file_stem}.{self._ext}"
            final_stem = self._resolve_duplicate(title, expected)
            if not final_stem or self._cancel.is_cancelled:
                return None

            def _progress_cb(pct: int, status: str) -> None:
                try:
                    normalized_status = str(status or "").strip().lower()
                    if normalized_status == "postprocessing":
                        self.stage_changed.emit("postprocessing")
                        return
                    if normalized_status == "postprocessed":
                        self.stage_changed.emit("postprocessed")
                        return
                    value = int(max(0, min(100, int(pct))))
                    self.progress_pct.emit(value)
                except (TypeError, ValueError, RuntimeError):
                    return

            try:
                path = svc.download(
                    url=self._url,
                    kind=self._kind,
                    quality=self._quality,
                    ext=self._ext,
                    out_dir=out_dir,
                    audio_track_id=self._audio_track_id,
                    file_stem=final_stem,
                    progress_cb=_progress_cb,
                    cancel_check=lambda: self._cancel.is_cancelled,
                    meta=meta,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
            except SourceAccessInterventionRequired as ex:
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = self._next_access_intervention_overrides(
                    ex,
                    payload_key_name="job_key",
                    payload_key=self._job_key,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._browser_policy_override = browser_policy_override
                self._access_mode_override = access_mode_override
                continue
            except DownloadError as ex:
                intervention = DownloadService.intervention_request_from_error(
                    ex,
                    url=self._url,
                    operation="download",
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                if intervention is None:
                    raise
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = self._next_access_intervention_overrides(
                    intervention,
                    payload_key_name="job_key",
                    payload_key=self._job_key,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._browser_policy_override = browser_policy_override
                self._access_mode_override = access_mode_override
                continue

            if self._cancel.is_cancelled:
                return None
            return path

    def _execute(self) -> None:
        if not self._url:
            raise DownloadError("error.generic", detail="missing url")

        svc = DownloadService()

        if self._action == "probe":
            meta = self._probe(svc)
            if isinstance(meta, dict):
                self.meta_ready.emit(meta)
            return

        if self._action != "download":
            raise DownloadError("error.generic", detail=f"unsupported action: {self._action}")

        if not self._kind or not self._quality or not self._ext:
            raise DownloadError("error.generic", detail="missing download options")

        path = self._execute_download(svc)
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
            raise DownloadError("error.download.download_failed", detail="download returned no output path")

        self.download_finished.emit(path)

    def _resolve_duplicate(self, title: str, expected: Path) -> str:
        if self._cancel.is_cancelled:
            return ""

        if not expected.exists():
            return expected.stem

        with self._duplicate_lock:
            self._duplicate_decision.reset()
        self.duplicate_check.emit(str(title), str(expected))
        action, new_name = self._wait_for_pending_decision(self._duplicate_decision)

        if self.cancel_check():
            return ""

        action = str(action or "").strip().lower()
        if action == "skip":
            return ""

        if action == "overwrite":
            self._remove_existing(expected)
            return expected.stem

        if action == "rename":
            candidate = sanitize_filename(new_name) if new_name else ""
            candidate_path = expected.with_name(f"{candidate or expected.stem}{expected.suffix}")
            unique = ensure_unique_path(candidate_path)
            return unique.stem

        return ""

    @staticmethod
    def _remove_existing(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError as ex:
            _LOG.debug("Download worker existing target cleanup skipped. path=%s detail=%s", path, ex)

        part = Path(str(path) + ".part")
        try:
            if part.exists():
                part.unlink()
        except OSError as ex:
            _LOG.debug("Download worker partial target cleanup skipped. path=%s detail=%s", part, ex)
