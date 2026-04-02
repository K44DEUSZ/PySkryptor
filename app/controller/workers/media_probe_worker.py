# app/controller/workers/media_probe_worker.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt5 import QtCore

from app.controller.support.cancellation import CancellationToken
from app.controller.workers.access_task_worker import AccessTaskWorker
from app.model.core.domain.errors import OperationCancelled
from app.model.download.domain import DownloadError, SourceAccessInterventionRequired
from app.model.download.policy import DownloadPolicy
from app.model.download.service import DownloadService
from app.model.sources.probe import MediaProbeReader

_LOG = logging.getLogger(__name__)


def _probe_url(
    url: str,
    *,
    browser_cookies_mode_override: str | None = None,
    cookie_file_override: str | None = None,
    browser_policy_override: str | None = None,
    access_mode_override: str | None = None,
    interactive: bool = False,
) -> dict[str, Any]:
    """Probe a remote URL through the shared download service."""
    return DownloadService.probe(
        url,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        interactive=interactive,
    )


class MediaProbeWorker(AccessTaskWorker):
    """Background worker that probes queued local or remote media entries."""

    table_ready = QtCore.pyqtSignal(list)
    item_error = QtCore.pyqtSignal(str, str, dict)

    def __init__(
        self,
        entries: list[dict[str, Any]],
        *,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        super().__init__(cancel_token=cancel_token)
        self._entries = list(entries or [])

    def cancel(self) -> None:
        super().cancel()

    def _probe_remote_entry(self, svc: MediaProbeReader, url: str) -> dict[str, Any] | None:
        browser_cookies_mode_override: str | None = None
        cookie_file_override: str | None = None
        browser_policy_override: str | None = None
        access_mode_override: str | None = None

        while True:
            try:
                meta = svc.from_url(
                    url,
                    interactive=True,
                    allow_degraded_probe=False,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                return meta.as_files_row()
            except SourceAccessInterventionRequired as ex:
                (
                    browser_cookies_mode_override,
                    cookie_file_override,
                    browser_policy_override,
                    access_mode_override,
                ) = self._next_access_intervention_overrides(
                    ex,
                    payload_key_name="source_key",
                    payload_key=url,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                continue
            except DownloadError as ex:
                intervention = DownloadService.intervention_request_from_error(
                    ex,
                    url=url,
                    operation=DownloadPolicy.DOWNLOAD_OPERATION_PROBE,
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
                    payload_key_name="source_key",
                    payload_key=url,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                )
                continue

        raise RuntimeError("Media probe intervention loop ended unexpectedly")

    def _execute(self) -> None:
        svc = MediaProbeReader(_probe_url)

        out: list[dict[str, Any]] = []
        for ent in self._entries:
            if self._cancel.is_cancelled:
                break

            src = str(ent.get("type") or "").strip().lower()
            val = str(ent.get("value") or "").strip()
            if not src or not val:
                continue

            try:
                if src == "url":
                    row = self._probe_remote_entry(svc, val)
                else:
                    meta = svc.from_local(Path(val))
                    row = meta.as_files_row() if meta else None
                if row:
                    out.append(row)
            except OperationCancelled:
                raise
            except Exception as ex:
                _LOG.error(
                    "Media probe failed.",
                    exc_info=True,
                    extra={"source": src, "value": val},
                )
                self.item_error.emit(
                    val,
                    "error.media_probe.failed",
                    {"source": src, "value": val, "detail": str(ex)},
                )

        if out and not self._cancel.is_cancelled:
            self.table_ready.emit(out)
