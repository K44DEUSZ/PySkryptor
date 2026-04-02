# app/model/transcription/materialize.py
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import AppError, OperationCancelled
from app.model.core.utils.string_utils import sanitize_filename, sanitize_url_for_log
from app.model.download.access import intervention_request_from_error
from app.model.download.domain import DownloadError, SourceAccessInterventionRequired
from app.model.download.policy import DownloadPolicy
from app.model.sources.probe import is_url_source
from app.model.transcription import workspace
from app.model.transcription.whisper import debug_source_key

from .processing import build_stage_progress_callback, emit_item_error, report_item_stage_progress
from .session import (
    EntryRequest,
    MaterializeBatchResult,
    MaterializedWorkItem,
    SessionCallbacks,
    SessionRuntime,
    SourceEntry,
)

_LOG = logging.getLogger(__name__)

_DownloadResult = TypeVar("_DownloadResult")
ErrorFactoryFn = Callable[..., Exception]


@runtime_checkable
class DownloadUseCaseProtocol(Protocol):
    """Minimal download use-case surface needed by transcription materialization."""

    def probe(
        self,
        url: str,
        *,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
        interactive: bool = False,
    ) -> dict[str, Any]: ...

    def download(
        self,
        *,
        url: str,
        kind: str,
        quality: str,
        ext: str,
        out_dir: Path,
        progress_cb: Callable[[int, str], None] | None = None,
        audio_track_id: str | None = None,
        file_stem: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        purpose: str = DownloadPolicy.DOWNLOAD_DEFAULT_PURPOSE,
        keep_output: bool = True,
        meta: dict[str, Any] | None = None,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
    ) -> Path | None: ...


def build_entry_request(entry: SourceEntry) -> EntryRequest:
    """Normalize one queued source entry before materialization."""

    if isinstance(entry, dict):
        source_key = str(entry.get("src") or "")
        forced_stem = str(entry.get("stem") or "").strip() or None
        audio_track_id = str(entry.get("audio_track_id") or "").strip() or None
    else:
        source_key = str(entry or "")
        forced_stem = None
        audio_track_id = None
    return EntryRequest(
        source_key=source_key,
        forced_stem=forced_stem,
        audio_track_id=audio_track_id,
        is_url=is_url_source(source_key),
    )


def materialize_local_entry(
    request: EntryRequest,
    *,
    missing_error_factory: Callable[[Path], Exception],
) -> MaterializedWorkItem:
    """Resolve one local source entry to an existing filesystem path."""

    path = Path(request.source_key).expanduser()
    if not path.exists():
        raise missing_error_factory(path)
    return MaterializedWorkItem(
        source_key=request.source_key,
        source_path=path,
        forced_stem=request.forced_stem,
    )


def emit_materialize_error(
    *,
    request: EntryRequest,
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
    error: Exception,
) -> None:
    """Emit one materialization failure for a queued source entry."""

    _LOG.debug(
        "Transcription materialize failed. session_id=%s source_key=%s detail=%s",
        runtime.session_id,
        debug_source_key(request.source_key),
        str(error),
    )
    emit_item_error(
        callbacks=callbacks,
        key=request.source_key,
        error=error,
    )


def materialize_work_items(
    *,
    entries: list[SourceEntry],
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
    download_service: DownloadUseCaseProtocol,
    error_factory: ErrorFactoryFn,
) -> MaterializeBatchResult:
    """Materialize queued sources into local work items for the batch session."""

    work: list[MaterializedWorkItem] = []
    had_errors = False
    was_cancelled = False

    for entry in entries:
        request = build_entry_request(entry)
        if callbacks.cancel_check():
            was_cancelled = True
            break
        try:
            work.append(
                materialize_entry(
                    request=request,
                    runtime=runtime,
                    callbacks=callbacks,
                    download_service=download_service,
                    error_factory=error_factory,
                )
            )
        except OperationCancelled:
            was_cancelled = True
            break
        except Exception as ex:
            had_errors = True
            emit_materialize_error(
                request=request,
                runtime=runtime,
                callbacks=callbacks,
                error=ex,
            )

    return MaterializeBatchResult(work=work, had_errors=had_errors, was_cancelled=was_cancelled)


def call_download_service_with_intervention(
    *,
    source_key: str,
    source_url: str,
    operation_name: str,
    callbacks: SessionCallbacks,
    browser_cookies_mode_override: str | None,
    cookie_file_override: str | None,
    browser_policy_override: str | None,
    access_mode_override: str | None,
    operation: Callable[[str | None, str | None, str | None, str | None], _DownloadResult],
) -> tuple[_DownloadResult, str | None, str | None, str | None, str | None]:
    """Run one download-service operation with user-driven access retries."""

    resolver = callbacks.access_intervention_resolver
    while True:
        try:
            result = operation(
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                access_mode_override,
            )
            return (
                result,
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                access_mode_override,
            )
        except SourceAccessInterventionRequired as ex:
            if resolver is None:
                raise
            (
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                access_mode_override,
            ) = resolver(
                source_key,
                ex.request,
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                access_mode_override,
            )
        except DownloadError as ex:
            if resolver is None:
                raise
            intervention = intervention_request_from_error(
                ex,
                url=source_url,
                operation=operation_name,
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
            ) = resolver(
                source_key,
                intervention.request,
                browser_cookies_mode_override,
                cookie_file_override,
                browser_policy_override,
                access_mode_override,
            )


def materialize_entry(
    *,
    request: EntryRequest,
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
    download_service: DownloadUseCaseProtocol,
    error_factory: ErrorFactoryFn,
) -> MaterializedWorkItem:
    """Materialize a queued entry into one concrete local source path."""

    if request.is_url:
        return materialize_url(
            request=request,
            runtime=runtime,
            callbacks=callbacks,
            download_service=download_service,
        )
    return materialize_local_entry(
        request,
        missing_error_factory=lambda path: error_factory(
            "error.input.file_not_found",
            path=str(path),
        ),
    )


def materialize_url(
    *,
    request: EntryRequest,
    runtime: SessionRuntime,
    callbacks: SessionCallbacks,
    download_service: DownloadUseCaseProtocol,
) -> MaterializedWorkItem:
    """Probe and download one remote source into a local work item."""

    old_key = str(request.source_key)
    safe_url = sanitize_url_for_log(request.source_key)
    callbacks.item_status(old_key, "status.processing")
    download_started = time.perf_counter()
    browser_cookies_mode_override: str | None = None
    cookie_file_override: str | None = None
    browser_policy_override: str | None = None
    access_mode_override: str | None = None
    (
        meta,
        browser_cookies_mode_override,
        cookie_file_override,
        browser_policy_override,
        access_mode_override,
    ) = call_download_service_with_intervention(
        source_key=old_key,
        source_url=request.source_key,
        operation_name=DownloadPolicy.DOWNLOAD_OPERATION_PROBE,
        callbacks=callbacks,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        operation=lambda mode_override, file_override, browser_override, access_override: download_service.probe(
            request.source_key,
            browser_cookies_mode_override=mode_override,
            cookie_file_override=file_override,
            browser_policy_override=browser_override,
            access_mode_override=access_override,
            interactive=True,
        ),
    )
    dur = meta.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        runtime.tracker.set_weight(old_key, weight=max(15.0, min(3600.0, float(dur))))

    title = sanitize_filename(str(meta.get("title") or "").strip())
    stem = request.forced_stem or title or DownloadPolicy.DOWNLOAD_DEFAULT_STEM
    kind = runtime.options.url_download_kind
    ext = runtime.options.url_download_ext
    keep = runtime.options.url_keep_download
    quality = runtime.options.url_download_quality

    _LOG.debug(
        (
            "Transcription URL materialization started. source_key=%s kind=%s ext=%s "
            "keep_download=%s audio_track_id=%s"
        ),
        safe_url,
        kind,
        ext,
        bool(keep),
        request.audio_track_id or "",
    )

    on_dl = build_stage_progress_callback(
        tracker=runtime.tracker,
        key=old_key,
        stage="download",
        item_progress=callbacks.item_progress,
    )

    callbacks.item_status(old_key, "status.downloading")
    out_dir = workspace.downloads_dir() if keep else workspace.url_tmp_dir()
    (
        dst,
        browser_cookies_mode_override,
        cookie_file_override,
        browser_policy_override,
        access_mode_override,
    ) = call_download_service_with_intervention(
        source_key=old_key,
        source_url=request.source_key,
        operation_name=DownloadPolicy.DOWNLOAD_OPERATION_DOWNLOAD,
        callbacks=callbacks,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        operation=lambda mode_override, file_override, browser_override, access_override: download_service.download(
            url=request.source_key,
            kind=kind,
            quality=quality,
            ext=ext,
            out_dir=out_dir,
            progress_cb=on_dl,
            audio_track_id=request.audio_track_id,
            file_stem=stem,
            cancel_check=callbacks.cancel_check,
            purpose=DownloadPolicy.DOWNLOAD_PURPOSE_TRANSCRIPTION,
            keep_output=keep,
            meta=meta,
            browser_cookies_mode_override=mode_override,
            cookie_file_override=file_override,
            browser_policy_override=browser_override,
            access_mode_override=access_override,
        ),
    )
    if not dst:
        raise AppError(key="error.download.download_failed", params={"detail": "download returned no file path"})
    if not keep:
        runtime.downloaded_to_delete.add(dst)

    new_key = str(dst)
    callbacks.item_path_update(old_key, new_key)
    runtime.tracker.rename_key(old_key, new_key)
    report_item_stage_progress(
        tracker=runtime.tracker,
        key=new_key,
        stage="download",
        pct=100,
        item_progress=callbacks.item_progress,
    )
    _LOG.debug(
        "Transcription URL materialization finished. source_key=%s new_key=%s duration_ms=%s",
        safe_url,
        debug_source_key(new_key),
        int((time.perf_counter() - download_started) * 1000.0),
    )
    return MaterializedWorkItem(
        source_key=new_key,
        source_path=dst,
        forced_stem=stem,
    )


def cleanup_downloaded_sources(downloaded_to_delete: set[Path]) -> None:
    """Remove remote download artifacts that were created only for transcription."""

    for path in downloaded_to_delete:
        try:
            path.unlink(missing_ok=True)
        except OSError as ex:
            _LOG.debug("Downloaded source cleanup skipped. path=%s detail=%s", path, ex)

        parent = path.parent
        try:
            tmp_root = AppConfig.PATHS.DOWNLOADS_TMP_DIR.resolve()
            if parent != tmp_root and tmp_root in parent.resolve().parents:
                shutil.rmtree(parent, ignore_errors=True)
        except OSError as ex:
            _LOG.debug("Downloaded source directory cleanup skipped. path=%s detail=%s", parent, ex)
