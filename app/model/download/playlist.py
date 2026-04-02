# app/model/download/playlist.py
from __future__ import annotations

import logging
from typing import Any, Callable

from app.model.core.domain.entities import PlaylistEntry, PlaylistResolveResult
from app.model.core.domain.errors import OperationCancelled
from app.model.core.utils.string_utils import sanitize_url_for_log
from app.model.download.domain import DownloadError, SourceAccessInterventionRequired
from app.model.download.gateway import YtdlpGateway, YtdlpLogger
from app.model.download.policy import DownloadPolicy

from .access import (
    access_intervention_request_from_decision,
    build_extractor_access_decision,
    resolve_source_access_context,
    validate_cookie_context,
)

_LOG = logging.getLogger(__name__)


def resolve_playlist(
    url: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
    browser_cookies_mode_override: str | None = None,
    cookie_file_override: str | None = None,
    browser_policy_override: str | None = None,
    access_mode_override: str | None = None,
    interactive: bool = False,
) -> PlaylistResolveResult:
    """Resolve one remote playlist into normalized playlist entries."""

    safe_url = sanitize_url_for_log(url)
    source_access_context = resolve_source_access_context(
        url,
        operation=DownloadPolicy.DOWNLOAD_OPERATION_PLAYLIST,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        interactive=interactive,
    )
    cookie_context = source_access_context.cookie_context
    validate_cookie_context(cookie_context)
    ydl_opts: dict[str, Any] = YtdlpGateway.base_ydl_opts(
        url=url,
        quiet=not _LOG.isEnabledFor(logging.DEBUG),
        skip_download=True,
        logger=YtdlpLogger(_LOG, cancel_check=cancel_check),
        cookie_context=cookie_context,
        source_access_context=source_access_context,
    )
    ydl_opts["noplaylist"] = False
    ydl_opts["extract_flat"] = "in_playlist"

    try:
        info, runtime_payload = YtdlpGateway.extract_info_with_fallback(
            url=url,
            ydl_opts=ydl_opts,
            download=False,
            allow_cookie_intervention=bool(cookie_context.interactive and cookie_context.mode == "from_browser"),
        )
    except OperationCancelled:
        raise
    except Exception as ex:
        if isinstance(ex, (DownloadError, SourceAccessInterventionRequired)):
            raise
        network_key = YtdlpGateway.classify_network_error(ex)
        if network_key:
            YtdlpGateway.log_network_error(action="playlist", url=url, ex=ex)
        _LOG.debug("Playlist resolve failed. url=%s detail=%s", safe_url, str(ex))
        raise DownloadError("error.playlist.resolve_failed", detail=str(ex)) from ex

    extractor_access_decision = build_extractor_access_decision(
        extractor_context=source_access_context.extractor_context,
        runtime=runtime_payload,
    )
    access_request = access_intervention_request_from_decision(
        extractor_access_decision,
        source_label=safe_url,
    )
    if access_request is not None:
        current_mode = DownloadPolicy.normalize_extractor_access_mode(
            source_access_context.extractor_context.access_mode
        )
        suggested_mode = DownloadPolicy.normalize_extractor_access_mode(access_request.suggested_access_mode)
        explicit_mode = (
            DownloadPolicy.normalize_extractor_access_mode(access_mode_override)
            if access_mode_override
            else ""
        )
        if not (explicit_mode and explicit_mode == current_mode) and suggested_mode != current_mode:
            raise SourceAccessInterventionRequired(access_request)

    info_dict = info if isinstance(info, dict) else {}
    raw_type = str(info_dict.get("_type") or "").strip().lower()
    raw_entries = (
        list(info_dict.get("entries") or [])
        if isinstance(info_dict.get("entries"), (list, tuple))
        else []
    )
    playlist_markers = (
        str(info_dict.get("playlist") or "").strip(),
        str(info_dict.get("playlist_id") or "").strip(),
        str(info_dict.get("playlist_title") or "").strip(),
    )
    is_playlist = bool(raw_entries) and (
        raw_type in {"playlist", "multi_video"}
        or "playlist" in raw_type
        or any(playlist_markers)
        or len(raw_entries) > 1
    )
    if not is_playlist:
        raise DownloadError("error.playlist.not_playlist", url=url)
    if not raw_entries:
        raise DownloadError("error.playlist.empty", url=url)

    playlist_title = str(info_dict.get("title") or info_dict.get("playlist_title") or "").strip()
    playlist_url = str(info_dict.get("webpage_url") or info_dict.get("original_url") or url).strip()

    out: list[PlaylistEntry] = []
    for idx, entry in enumerate(raw_entries, start=1):
        if not isinstance(entry, dict):
            continue
        entry_url = str(entry.get("webpage_url") or entry.get("original_url") or "").strip()
        raw_entry_url = str(entry.get("url") or "").strip()
        if not entry_url and raw_entry_url.startswith(("http://", "https://")):
            entry_url = raw_entry_url
        if not entry_url:
            entry_id = str(entry.get("id") or "").strip()
            ie_key = (
                str(entry.get("ie_key") or entry.get("extractor_key") or entry.get("extractor") or "")
                .strip()
                .lower()
            )
            if entry_id and "youtube" in ie_key:
                entry_url = f"https://www.youtube.com/watch?v={entry_id}"
        if not entry_url:
            continue
        try:
            duration_s = int(entry.get("duration")) if entry.get("duration") is not None else None
        except (TypeError, ValueError):
            duration_s = None
        out.append(
            PlaylistEntry(
                entry_url=entry_url,
                title=str(entry.get("title") or "").strip(),
                duration_s=duration_s,
                uploader=str(entry.get("uploader") or entry.get("channel") or "").strip(),
                position=idx,
            )
        )

    if not out:
        raise DownloadError("error.playlist.empty", url=url)

    _LOG.info(
        "Playlist resolved (flat). url=%s title=%s count=%s",
        safe_url,
        playlist_title or playlist_url,
        len(out),
    )
    return PlaylistResolveResult(
        playlist_title=playlist_title or playlist_url,
        playlist_url=playlist_url,
        total_count=len(out),
        entries=tuple(out),
    )
