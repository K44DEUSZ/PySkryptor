# app/controller/workers/source_expansion_worker.py
from __future__ import annotations

from PyQt5 import QtCore

from app.controller.workers.access_task_worker import AccessTaskWorker
from app.model.core.domain.results import SourceExpansionResult
from app.model.download.policy import DownloadPolicy
from app.model.download.service import DownloadService
from app.model.download.domain import DownloadError, SourceAccessInterventionRequired
from app.model.sources.service import SourceExpansionService


class SourceExpansionWorker(AccessTaskWorker):
    """Background worker that expands a user add action into normalized sources."""

    expanded = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str, dict)

    def __init__(self, *, mode: str, raw: str = "", paths: list[str] | None = None, origin_kind: str = "") -> None:
        super().__init__()
        self._mode = str(mode or "").strip().lower()
        self._raw = str(raw or "")
        self._paths = list(paths or [])
        self._origin_kind = str(origin_kind or "").strip().lower()
        self._browser_cookies_mode_override: str | None = None
        self._cookie_file_override: str | None = None
        self._access_mode_override: str | None = None

    def _emit_status(self, key: str, params: dict | None = None) -> None:
        self.status_changed.emit(str(key or ""), dict(params or {}))

    def _execute_manual_input(self, svc: SourceExpansionService) -> SourceExpansionResult:
        browser_cookies_mode_override = self._browser_cookies_mode_override
        cookie_file_override = self._cookie_file_override
        access_mode_override = self._access_mode_override
        while True:
            try:
                result = svc.expand_manual_input(
                    self._raw,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    access_mode_override=access_mode_override,
                    interactive=True,
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._access_mode_override = access_mode_override
                return result
            except SourceAccessInterventionRequired as ex:
                browser_cookies_mode_override, cookie_file_override, access_mode_override = (
                    self._next_access_intervention_overrides(
                        ex,
                        payload_key_name="source_key",
                        payload_key=self._raw,
                        browser_cookies_mode_override=browser_cookies_mode_override,
                        cookie_file_override=cookie_file_override,
                        access_mode_override=access_mode_override,
                    )
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._access_mode_override = access_mode_override
                continue
            except DownloadError as ex:
                intervention = DownloadService.intervention_request_from_error(
                    ex,
                    url=self._raw,
                    operation=DownloadPolicy.DOWNLOAD_OPERATION_PLAYLIST,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    access_mode_override=access_mode_override,
                )
                if intervention is None:
                    raise
                browser_cookies_mode_override, cookie_file_override, access_mode_override = (
                    self._next_access_intervention_overrides(
                        intervention,
                        payload_key_name="source_key",
                        payload_key=self._raw,
                        browser_cookies_mode_override=browser_cookies_mode_override,
                        cookie_file_override=cookie_file_override,
                        access_mode_override=access_mode_override,
                    )
                )
                self._browser_cookies_mode_override = browser_cookies_mode_override
                self._cookie_file_override = cookie_file_override
                self._access_mode_override = access_mode_override
                continue

        raise RuntimeError("Source expansion intervention loop ended unexpectedly")

    def _execute(self) -> None:
        svc = SourceExpansionService(cancel_check=self.cancel_check, status_callback=self._emit_status)
        if self._mode == "manual_input":
            result = self._execute_manual_input(svc)
        elif self._mode == "local_paths":
            result = svc.expand_local_paths(self._paths, origin_kind=self._origin_kind or "local_paths")
        else:
            raise ValueError(f"Unsupported source expansion mode: {self._mode}")
        self.expanded.emit(result)
