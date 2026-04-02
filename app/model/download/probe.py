# app/model/download/probe.py
from __future__ import annotations

import logging
from typing import Any

from app.model.core.utils.string_utils import sanitize_url_for_log
from app.model.download.domain import DownloadCookieContext, DownloadError, SourceAccessInterventionRequired
from app.model.download.gateway import YtdlpGateway
from app.model.download.inventory import TrackInventory
from app.model.download.policy import DownloadPolicy
from app.model.download.strategy import resolve_extractor_strategy

from .access import (
    build_extractor_access_decision,
    resolve_source_access_context,
    validate_cookie_context,
)

_LOG = logging.getLogger(__name__)


def probe(
    url: str,
    *,
    browser_cookies_mode_override: str | None = None,
    cookie_file_override: str | None = None,
    browser_policy_override: str | None = None,
    access_mode_override: str | None = None,
    interactive: bool = False,
) -> dict[str, Any]:
    """Probe one remote source and return normalized download metadata."""

    safe_url = sanitize_url_for_log(url)
    source_access_context = resolve_source_access_context(
        url,
        operation=DownloadPolicy.DOWNLOAD_OPERATION_PROBE,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        interactive=interactive,
    )
    cookie_context = source_access_context.cookie_context
    extractor_context = source_access_context.extractor_context
    validate_cookie_context(cookie_context)
    enhanced_probe_mode = extractor_context.uses_enhanced_access()
    collect_probe_variants = resolve_extractor_strategy(extractor_context.extractor_key).collect_probe_variants(
        extractor_context
    )
    base_ydl_opts: dict[str, Any] = YtdlpGateway.base_ydl_opts(
        url=url,
        quiet=not _LOG.isEnabledFor(logging.DEBUG),
        skip_download=True,
        cookie_context=cookie_context,
        source_access_context=source_access_context,
    )
    _LOG.debug(
        "Download probe started. url=%s quiet=%s cookies_mode=%s enhanced=%s",
        safe_url,
        bool(base_ydl_opts.get("quiet", False)),
        cookie_context.mode,
        enhanced_probe_mode,
    )

    try:
        attempted_clients = YtdlpGateway.probe_clients_for_access_context(source_access_context)
        inventories_by_client: dict[str, dict[str, Any]] = {}
        probe_variants: dict[str, dict[str, Any]] = {}
        primary_runtime_by_client: dict[str, dict[str, Any]] = {}
        primary_probe_client = ""
        primary_info: dict[str, Any] | None = None
        last_error: Exception | None = None

        preferred_probe_client = YtdlpGateway.normalize_probe_client(extractor_context.client)
        for probe_client in attempted_clients:
            ydl_opts = YtdlpGateway.with_probe_client_opts(
                base_ydl_opts,
                probe_client=probe_client,
                extractor_access_context=extractor_context,
            )
            try:
                info, probe_runtime = YtdlpGateway.extract_info_with_fallback(
                    url=url,
                    ydl_opts=ydl_opts,
                    download=False,
                    allow_cookie_intervention=bool(
                        cookie_context.interactive and cookie_context.mode == "from_browser"
                    ),
                )
            except Exception as ex:
                last_error = ex
                _LOG.debug(
                    "Download probe variant failed. url=%s probe_client=%s detail=%s",
                    safe_url,
                    probe_client,
                    str(ex),
                )
                if isinstance(ex, SourceAccessInterventionRequired):
                    raise
                if len(attempted_clients) == 1:
                    raise
                if primary_info is not None:
                    continue
                continue

            normalized_probe_client = YtdlpGateway.normalize_probe_client(probe_client)
            primary_runtime_by_client[normalized_probe_client] = dict(probe_runtime or {})
            if (
                primary_info is None
                or normalized_probe_client == preferred_probe_client
                or (preferred_probe_client == "default" and normalized_probe_client == "default")
            ):
                primary_info = info
                primary_probe_client = normalized_probe_client

            inventory = TrackInventory.build_audio_track_inventory(info, probe_client=probe_client)
            inventories_by_client[normalized_probe_client] = inventory
            if collect_probe_variants:
                probe_variants[normalized_probe_client] = TrackInventory.build_probe_variant_payload(
                    info,
                    probe_client=normalized_probe_client,
                    inventory=inventory,
                )

        if primary_info is None:
            if last_error is not None:
                raise last_error
            raise DownloadError("error.download.probe_failed", detail="probe returned no metadata")

        primary_runtime = dict(primary_runtime_by_client.get(primary_probe_client) or {})
        extractor_access_decision = build_extractor_access_decision(
            extractor_context=extractor_context,
            runtime=primary_runtime,
        )

        inventory = TrackInventory.finalize_probe_inventory(
            inventories_by_client=inventories_by_client,
            attempted_clients=attempted_clients,
        )
        audio_tracks = list(inventory.get("tracks") or [])
        probe_diagnostics_payload = TrackInventory.make_probe_diagnostics(
            info=primary_info,
            audio_tracks=audio_tracks,
            inventory=inventory,
            js_runtime_fallback=bool(primary_runtime.get("js_runtime_fallback")),
            js_runtime_detail=str(primary_runtime.get("js_runtime_error") or "").strip(),
            cookie_runtime_fallback=bool(primary_runtime.get("cookie_runtime_fallback")),
            cookie_runtime_failures=list(primary_runtime.get("cookie_browser_failures") or []),
            authentication_required=bool(primary_runtime.get("authentication_required")),
            authentication_detail=str(primary_runtime.get("authentication_error") or "").strip(),
            no_downloadable_formats=bool(primary_runtime.get("no_downloadable_formats")),
            no_downloadable_formats_detail=str(primary_runtime.get("no_downloadable_formats_detail") or "").strip(),
            extended_access_required=bool(primary_runtime.get("extended_access_required")),
            extended_access_required_detail=str(primary_runtime.get("extended_access_required_detail") or "").strip(),
            extractor_access_limited=bool(primary_runtime.get("extractor_access_limited")),
            extractor_access_limited_detail=str(primary_runtime.get("extractor_access_limited_detail") or "").strip(),
            browser_cookie_requested=cookie_context.mode == "from_browser",
            enhanced_mode=enhanced_probe_mode,
            extractor_access_decision=extractor_access_decision.as_payload(),
        )
        if probe_diagnostics_payload:
            details = probe_diagnostics_payload.setdefault("details", {})
            details["extractor_access_mode"] = extractor_context.access_mode
            details["extractor_client"] = extractor_context.client
            details["extractor_capabilities"] = extractor_context.runtime_capabilities.as_payload()
            details["extractor_access_decision"] = extractor_access_decision.as_payload()
            details["extractor_action"] = extractor_access_decision.action
        webpage_url = primary_info.get("webpage_url") or primary_info.get("original_url") or url
        result = {
            "id": primary_info.get("id") or primary_info.get("display_id") or "",
            "title": primary_info.get("title"),
            "duration": primary_info.get("duration"),
            "filesize": primary_info.get("filesize") or primary_info.get("filesize_approx"),
            "extractor": primary_info.get("extractor_key") or primary_info.get("extractor"),
            "extractor_key": DownloadPolicy.normalize_extractor_key(
                primary_info.get("extractor_key") or primary_info.get("extractor")
            ),
            "webpage_url": webpage_url,
            "uploader": primary_info.get("uploader") or primary_info.get("channel") or "",
            "uploader_id": primary_info.get("uploader_id") or primary_info.get("channel_id") or "",
            "uploader_url": primary_info.get("uploader_url") or primary_info.get("channel_url") or "",
            "upload_date": primary_info.get("upload_date") or "",
            "view_count": primary_info.get("view_count"),
            "like_count": primary_info.get("like_count"),
            "tags": primary_info.get("tags") or [],
            "categories": primary_info.get("categories") or [],
            "description": primary_info.get("description") or "",
            "thumbnail_url": YtdlpGateway.pick_thumbnail_url(primary_info),
            "formats": primary_info.get("formats") or [],
            "audio_tracks": audio_tracks,
            "probe_diagnostics": probe_diagnostics_payload,
            "extractor_capabilities": extractor_context.runtime_capabilities.as_payload(),
            "extractor_access_decision": extractor_access_decision.as_payload(),
            "source_access": source_access_context.as_payload(),
        }
        if collect_probe_variants and probe_variants:
            result["_probe_variants"] = probe_variants
        if probe_diagnostics_payload:
            _LOG.info(
                "Download probe diagnostics. url=%s warnings=%s errors=%s details=%s",
                safe_url,
                probe_diagnostics_payload.get("warnings") or [],
                probe_diagnostics_payload.get("errors") or [],
                probe_diagnostics_payload.get("details") or {},
            )
        _LOG.debug(
            "Download probe finished. url=%s title=%s extractor=%s duration=%s audio_tracks=%s",
            safe_url,
            str(result.get("title") or result.get("id") or ""),
            str(result.get("extractor") or ""),
            result.get("duration"),
            len(audio_tracks),
        )
        return result
    except Exception as ex:
        if isinstance(ex, (DownloadError, SourceAccessInterventionRequired)):
            raise
        network_key = YtdlpGateway.classify_network_error(ex)
        if network_key:
            YtdlpGateway.log_network_error(action="probe", url=url, ex=ex)
            raise DownloadError(network_key)
        _LOG.debug("Download probe failed. url=%s detail=%s", safe_url, str(ex))
        raise DownloadError("error.download.probe_failed", detail=str(ex))


def probe_diagnostics(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Return normalized probe diagnostics payload from download metadata."""

    if not isinstance(meta, dict):
        return {}
    diagnostics = meta.get("probe_diagnostics")
    return dict(diagnostics) if isinstance(diagnostics, dict) else {}


def raise_if_probe_blocks_download(
    meta: dict[str, Any] | None,
    *,
    cookie_context: DownloadCookieContext,
) -> None:
    """Raise a download error when probe diagnostics already block download."""

    diagnostics = probe_diagnostics(meta)
    warnings = {
        str(item or "").strip()
        for item in list(diagnostics.get("warnings") or [])
        if str(item or "").strip()
    }
    if not warnings:
        return

    details = dict(diagnostics.get("details") or {})
    if "authentication_required" in warnings:
        detail = str(details.get("authentication_detail") or "").strip()
        raise DownloadError("error.download.authentication_required", detail=detail)

    if "extended_access_required" in warnings:
        detail = str(details.get("extended_access_required_detail") or "").strip()
        raise DownloadError("error.download.extended_access_required", detail=detail)

    if {"media_unavailable", "no_downloadable_formats", "no_public_formats"} & warnings:
        detail = str(details.get("no_downloadable_formats_detail") or "").strip()
        if not detail:
            detail = str(details.get("extractor_access_limited_detail") or "").strip()
        if not detail:
            detail = "no downloadable media formats found during probe"
        raise DownloadError("error.download.no_downloadable_formats", detail=detail)

    if cookie_context.mode != "from_browser" or "browser_cookies_unavailable" not in warnings:
        return

    failures = list(details.get("cookie_browser_failures") or [])
    detail = ""
    if failures and isinstance(failures[0], dict):
        detail = str(failures[0].get("detail") or "").strip()
    detail = detail or str(details.get("authentication_detail") or "").strip()
    raise DownloadError("error.download.browser_cookies_unavailable", detail=detail)
