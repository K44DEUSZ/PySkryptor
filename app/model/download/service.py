# app/model/download/service.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app.model.core.config.config import AppConfig
from app.model.core.domain.entities import PlaylistEntry, PlaylistResolveResult
from app.model.core.domain.errors import OperationCancelled
from app.model.core.utils.string_utils import sanitize_filename, sanitize_url_for_log
from app.model.download.artifacts import DownloadArtifactManager
from app.model.download.cookies import validate_cookie_file
from app.model.download.plan import DownloadPlanBuilder
from app.model.download.policy import DownloadPolicy
from app.model.download.inventory import TrackInventory
from app.model.download.domain import (
    SourceAccessInterventionRequest,
    DownloadCookieContext,
    DownloadError,
    SourceAccessInterventionRequired,
    ExtractorAccessContext,
    ExtractorAccessDecision,
    SourceAccessContext,
)
from app.model.download.gateway import YtdlpGateway, YtdlpLogger
from app.model.download.runtime import (
    available_cookie_browsers,
    detect_extractor_capabilities,
    resolve_cookie_browser_candidates,
    resolve_effective_cookie_browser,
)
from app.model.download.strategy import resolve_extractor_strategy, resolve_extractor_strategy_for_url

_LOG = logging.getLogger(__name__)


class DownloadService:
    """Download orchestration for probing, planning and yt_dlp execution."""

    @staticmethod
    def available_video_heights(
        info: dict[str, Any] | None,
        *,
        min_h: int | None = None,
        max_h: int | None = None,
    ) -> list[int]:
        return TrackInventory.available_video_heights(info, min_h=min_h, max_h=max_h)

    @staticmethod
    def available_audio_bitrates(info: dict[str, Any] | None) -> list[int]:
        return TrackInventory.available_audio_bitrates(info)

    @staticmethod
    def available_cookie_browser_names() -> tuple[str, ...]:
        """Return cookie browsers currently usable by the runtime."""
        return available_cookie_browsers()

    @staticmethod
    def resolve_effective_cookie_browser(policy_browser: str | None) -> str:
        """Resolve the primary browser shown for browser-cookie mode."""
        return resolve_effective_cookie_browser(policy_browser)

    @staticmethod
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
        safe_url = sanitize_url_for_log(url)
        source_access_context = DownloadService.resolve_source_access_context(
            url,
            operation=DownloadPolicy.DOWNLOAD_OPERATION_PLAYLIST,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            interactive=interactive,
        )
        cookie_context = source_access_context.cookie_context
        DownloadService._validate_cookie_context(cookie_context)
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
            info, _runtime = YtdlpGateway.extract_info_with_fallback(
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

        extractor_access_decision = DownloadService._build_extractor_access_decision(
            extractor_context=source_access_context.extractor_context,
            runtime=_runtime,
        )
        access_request = DownloadService._access_intervention_request_from_decision(
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

    @staticmethod
    def probe(
        url: str,
        *,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
        interactive: bool = False,
    ) -> dict[str, Any]:
        safe_url = sanitize_url_for_log(url)
        source_access_context = DownloadService.resolve_source_access_context(
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
        DownloadService._validate_cookie_context(cookie_context)
        enhanced_probe_mode = extractor_context.uses_enhanced_access()
        collect_probe_variants = resolve_extractor_strategy(
            extractor_context.extractor_key
        ).collect_probe_variants(extractor_context)
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
                raise DownloadError("error.down.probe_failed", detail="probe returned no metadata")

            primary_runtime = dict(primary_runtime_by_client.get(primary_probe_client) or {})
            extractor_access_decision = DownloadService._build_extractor_access_decision(
                extractor_context=extractor_context,
                runtime=primary_runtime,
            )

            inventory = TrackInventory.finalize_probe_inventory(
                inventories_by_client=inventories_by_client,
                attempted_clients=attempted_clients,
            )
            audio_tracks = list(inventory.get("tracks") or [])
            probe_diagnostics = TrackInventory.make_probe_diagnostics(
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
                extended_access_required_detail=(
                    str(primary_runtime.get("extended_access_required_detail") or "").strip()
                ),
                extractor_access_limited=bool(primary_runtime.get("extractor_access_limited")),
                extractor_access_limited_detail=(
                    str(primary_runtime.get("extractor_access_limited_detail") or "").strip()
                ),
                browser_cookie_requested=cookie_context.mode == "from_browser",
                enhanced_mode=enhanced_probe_mode,
                extractor_access_decision=extractor_access_decision.as_payload(),
            )
            if probe_diagnostics:
                details = probe_diagnostics.setdefault("details", {})
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
                "probe_diagnostics": probe_diagnostics,
                "extractor_capabilities": extractor_context.runtime_capabilities.as_payload(),
                "extractor_access_decision": extractor_access_decision.as_payload(),
                "source_access": source_access_context.as_payload(),
            }
            if collect_probe_variants and probe_variants:
                result["_probe_variants"] = probe_variants
            if probe_diagnostics:
                _LOG.info(
                    "Download probe diagnostics. url=%s warnings=%s errors=%s details=%s",
                    safe_url,
                    probe_diagnostics.get("warnings") or [],
                    probe_diagnostics.get("errors") or [],
                    probe_diagnostics.get("details") or {},
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
            raise DownloadError("error.down.probe_failed", detail=str(ex))

    @staticmethod
    def resolve_cookie_context(
        *,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        interactive: bool = False,
    ) -> DownloadCookieContext:
        token = str(browser_cookies_mode_override or "").strip().lower()
        mode = token if token in DownloadPolicy.COOKIE_BROWSER_MODES else AppConfig.browser_cookies_mode()
        browser_policy = ""
        if mode == "from_browser":
            preferred_policy = str(browser_policy_override or "").strip().lower()
            browser_policy = (
                DownloadPolicy.normalize_cookie_browser_policy(preferred_policy)
                if preferred_policy
                else AppConfig.browser_cookie_browser_policy()
            )
        cookie_file_path = ""
        if mode == "from_file":
            cookie_file_path = str(cookie_file_override or AppConfig.browser_cookie_file_path()).strip()
        return DownloadCookieContext(
            mode=mode,
            browser_policy=browser_policy,
            cookie_file_path=cookie_file_path,
            interactive=bool(interactive),
        )

    @staticmethod
    def resolve_extractor_access_context(
        url: str,
        *,
        operation: str,
        access_mode_override: str | None = None,
    ) -> ExtractorAccessContext:
        """Resolve extractor-specific access strategy for a single operation."""
        strategy = resolve_extractor_strategy_for_url(url)
        runtime_capabilities = detect_extractor_capabilities(strategy.extractor_key)
        context = strategy.build_access_context(
            operation=operation,
            runtime_capabilities=runtime_capabilities,
        )
        if access_mode_override is not None:
            normalized_override = DownloadPolicy.normalize_extractor_access_mode(access_mode_override)
            override_client = str(context.client or "default").strip().lower() or "default"
            if normalized_override == DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC:
                override_client = "default"
            elif context.extractor_key == DownloadPolicy.EXTRACTOR_KEY_YOUTUBE and normalized_override in {
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED,
                DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED,
            }:
                override_client = DownloadPolicy.youtube_enhanced_client()
            context = context.with_access_mode(normalized_override, client=override_client)
        return context

    @staticmethod
    def resolve_source_access_context(
        url: str,
        *,
        operation: str,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
        interactive: bool = False,
    ) -> SourceAccessContext:
        """Resolve the combined cookie and extractor access context for an operation."""
        cookie_context = DownloadService.resolve_cookie_context(
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            interactive=interactive,
        )
        extractor_context = DownloadService.resolve_extractor_access_context(
            url,
            operation=operation,
            access_mode_override=access_mode_override,
        )
        return SourceAccessContext(
            cookie_context=cookie_context,
            extractor_context=extractor_context,
        )

    @staticmethod
    def _available_cookie_browser_policies(context: DownloadCookieContext) -> tuple[str, ...]:
        if context.mode != "from_browser":
            return tuple()

        ordered: list[str] = []
        seen: set[str] = set()

        preferred = resolve_effective_cookie_browser(context.browser_policy)
        if preferred and preferred not in seen:
            ordered.append(preferred)
            seen.add(preferred)

        for browser in resolve_cookie_browser_candidates(context.browser_policy):
            if browser and browser not in seen:
                ordered.append(browser)
                seen.add(browser)

        for browser in available_cookie_browsers():
            if browser and browser not in seen:
                ordered.append(browser)
                seen.add(browser)
        return tuple(ordered)

    @staticmethod
    def _cookie_intervention_request(
        context: DownloadCookieContext,
        *,
        detail: str,
        can_retry: bool,
        can_choose_cookie_file: bool = True,
        can_continue_without_cookies: bool = True,
    ) -> SourceAccessInterventionRequest:
        return SourceAccessInterventionRequest(
            kind="cookies",
            source_kind="file" if context.mode == "from_file" else "browser",
            source_label=DownloadService._cookie_source_label(context),
            detail=str(detail or "").strip(),
            browser_policy=(
                resolve_effective_cookie_browser(context.browser_policy) if context.mode == "from_browser" else ""
            ),
            available_browser_policies=DownloadService._available_cookie_browser_policies(context),
            can_retry=bool(can_retry),
            can_choose_cookie_file=bool(can_choose_cookie_file),
            can_continue_without_cookies=bool(can_continue_without_cookies),
        )

    @staticmethod
    def _cookie_source_label(context: DownloadCookieContext) -> str:
        if context.mode == "from_browser":
            resolved = resolve_effective_cookie_browser(context.browser_policy)
            return str(resolved or context.browser_policy or "").strip()
        if context.mode == "from_file":
            raw_path = str(context.cookie_file_path or "").strip()
            if not raw_path:
                return ""
            try:
                path = Path(raw_path).expanduser()
            except (OSError, RuntimeError, TypeError, ValueError):
                return raw_path
            return str(path.name or path)
        return ""

    @staticmethod
    def _validate_cookie_context(context: DownloadCookieContext) -> None:
        if context.mode != "from_file":
            return
        result = validate_cookie_file(context.cookie_file_path)
        if result.ok:
            return
        if context.interactive:
            raise SourceAccessInterventionRequired(
                DownloadService._cookie_intervention_request(
                    context,
                    detail=result.detail,
                    can_retry=False,
                )
            )
        raise DownloadError("error.down.cookie_file_invalid", detail=result.detail)

    @staticmethod
    def _available_track_probe_clients(
        selected_audio_track: dict[str, Any],
        *,
        meta: dict[str, Any] | None,
    ) -> tuple[str, ...]:
        ordered_clients = list(TrackInventory.ordered_probe_clients_for_track(selected_audio_track))
        if not TrackInventory.probe_variants_from_meta(meta) and "default" not in ordered_clients:
            ordered_clients.append("default")
        return tuple(ordered_clients)

    @staticmethod
    def _ordered_track_download_clients(
        selected_audio_track: dict[str, Any],
        *,
        meta: dict[str, Any] | None,
        extractor_context: ExtractorAccessContext,
    ) -> tuple[str, ...]:
        available_clients = DownloadService._available_track_probe_clients(selected_audio_track, meta=meta)
        strategy = resolve_extractor_strategy(extractor_context.extractor_key)
        ordered_clients = strategy.select_download_clients(extractor_context, available_clients)
        return ordered_clients or available_clients

    @staticmethod
    def _build_extractor_access_decision(
        *,
        extractor_context: ExtractorAccessContext,
        runtime: dict[str, Any] | None,
    ) -> ExtractorAccessDecision:
        runtime_payload = dict(runtime or {})
        detail = str(
            runtime_payload.get("extended_access_required_detail")
            or runtime_payload.get("extractor_access_limited_detail")
            or ""
        ).strip()
        scope = DownloadPolicy.normalize_extractor_access_scope(runtime_payload.get("extended_access_scope"))
        capabilities = extractor_context.runtime_capabilities
        access_mode = DownloadPolicy.normalize_extractor_access_mode(extractor_context.access_mode)
        provider_state = DownloadPolicy.normalize_provider_state(capabilities.provider_state)
        suggested_access_mode = (
            DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED
            if capabilities.enhanced_mode_available
            else DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC
        )

        if bool(runtime_payload.get("extended_access_required")):
            if capabilities.enhanced_mode_available and access_mode != DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED:
                return ExtractorAccessDecision(
                    extractor_key=extractor_context.extractor_key,
                    state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_ENHANCED_REQUIRED,
                    action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED,
                    detail=detail,
                    scope=scope,
                    access_mode=access_mode,
                    suggested_access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED,
                    provider_state=provider_state,
                    can_continue_basic=False,
                )
            unavailable_state = (
                DownloadPolicy.EXTRACTOR_ACCESS_STATE_PROVIDER_MISSING
                if provider_state == DownloadPolicy.EXTRACTOR_PROVIDER_STATE_MISSING
                else DownloadPolicy.EXTRACTOR_ACCESS_STATE_UNAVAILABLE
            )
            unavailable_action = (
                DownloadPolicy.EXTRACTOR_ACCESS_ACTION_INSTALL_PROVIDER
                if unavailable_state == DownloadPolicy.EXTRACTOR_ACCESS_STATE_PROVIDER_MISSING
                else DownloadPolicy.EXTRACTOR_ACCESS_ACTION_NONE
            )
            return ExtractorAccessDecision(
                extractor_key=extractor_context.extractor_key,
                state=unavailable_state,
                action=unavailable_action,
                detail=detail or capabilities.provider_detail or capabilities.basic_only_reason,
                scope=scope,
                access_mode=access_mode,
                suggested_access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_UNAVAILABLE,
                provider_state=provider_state,
                can_continue_basic=False,
            )

        if bool(runtime_payload.get("extractor_access_limited")):
            if access_mode == DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED:
                return ExtractorAccessDecision(
                    extractor_key=extractor_context.extractor_key,
                    state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_DEGRADED,
                    action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_LIMITED_FORMATS,
                    detail=detail,
                    scope=scope,
                    access_mode=access_mode,
                    suggested_access_mode=access_mode,
                    provider_state=provider_state,
                    can_continue_basic=True,
                )
            if capabilities.enhanced_mode_available:
                return ExtractorAccessDecision(
                    extractor_key=extractor_context.extractor_key,
                    state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_ENHANCED_RECOMMENDED,
                    action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED,
                    detail=detail,
                    scope=scope,
                    access_mode=access_mode,
                    suggested_access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED,
                    provider_state=provider_state,
                    can_continue_basic=True,
                )
            return ExtractorAccessDecision(
                extractor_key=extractor_context.extractor_key,
                state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_BASIC_LIMITED,
                action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC,
                detail=detail or capabilities.provider_detail or capabilities.basic_only_reason,
                scope=scope,
                access_mode=access_mode,
                suggested_access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC,
                provider_state=provider_state,
                can_continue_basic=True,
            )

        if access_mode == DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED:
            return ExtractorAccessDecision(
                extractor_key=extractor_context.extractor_key,
                state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_ENHANCED_ACTIVE,
                action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_NONE,
                detail=detail,
                scope=scope,
                access_mode=access_mode,
                suggested_access_mode=access_mode,
                provider_state=provider_state,
                can_continue_basic=True,
            )

        if access_mode == DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED:
            return ExtractorAccessDecision(
                extractor_key=extractor_context.extractor_key,
                state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_DEGRADED,
                action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC,
                detail=detail or capabilities.provider_detail or capabilities.basic_only_reason,
                scope=scope,
                access_mode=access_mode,
                suggested_access_mode=suggested_access_mode,
                provider_state=provider_state,
                can_continue_basic=True,
            )

        if access_mode == DownloadPolicy.EXTRACTOR_ACCESS_MODE_UNAVAILABLE:
            return ExtractorAccessDecision(
                extractor_key=extractor_context.extractor_key,
                state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_UNAVAILABLE,
                action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_NONE,
                detail=detail or capabilities.provider_detail or capabilities.basic_only_reason,
                scope=scope,
                access_mode=access_mode,
                suggested_access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_UNAVAILABLE,
                provider_state=provider_state,
                can_continue_basic=False,
            )

        return ExtractorAccessDecision(
            extractor_key=extractor_context.extractor_key,
            state=DownloadPolicy.EXTRACTOR_ACCESS_STATE_BASIC_OK,
            action=DownloadPolicy.EXTRACTOR_ACCESS_ACTION_NONE,
            detail=detail,
            scope=scope,
            access_mode=access_mode,
            suggested_access_mode=suggested_access_mode,
            provider_state=provider_state,
            can_continue_basic=True,
        )

    @staticmethod
    def _access_intervention_request_from_decision(
        decision: ExtractorAccessDecision,
        *,
        source_label: str = "",
    ) -> SourceAccessInterventionRequest | None:
        """Build a user-actionable source-access intervention from an extractor decision."""
        state = str(decision.state or "").strip().lower()
        action = str(decision.action or "").strip().lower()
        if action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_NONE and not decision.can_continue_basic:
            return None
        can_retry_enhanced = action == DownloadPolicy.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED
        can_continue_basic = bool(decision.can_continue_basic)
        can_continue_degraded = (
            str(decision.suggested_access_mode or "").strip().lower()
            == DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED
        )
        if not (can_retry_enhanced or can_continue_basic or can_continue_degraded):
            return None
        return SourceAccessInterventionRequest(
            kind="enhanced_access",
            source_kind=str(decision.extractor_key or "generic"),
            source_label=str(source_label or decision.extractor_key or "").strip(),
            detail=str(decision.detail or "").strip(),
            state=state,
            action=action,
            suggested_access_mode=str(decision.suggested_access_mode or "").strip(),
            provider_state=str(decision.provider_state or "").strip(),
            can_retry_enhanced=can_retry_enhanced,
            can_continue_basic=can_continue_basic,
            can_continue_degraded=can_continue_degraded,
        )

    @staticmethod
    def access_intervention_request_from_meta(meta: dict[str, Any] | None) -> SourceAccessInterventionRequest | None:
        """Return a user-actionable source-access intervention derived from probe metadata."""
        if not isinstance(meta, dict):
            return None
        payload = meta.get("extractor_access_decision")
        if not isinstance(payload, dict):
            diagnostics = meta.get("probe_diagnostics")
            if isinstance(diagnostics, dict):
                payload = dict((diagnostics.get("details") or {})).get("extractor_access_decision")
        if not isinstance(payload, dict):
            return None
        decision = ExtractorAccessDecision(
            extractor_key=str(payload.get("extractor_key") or "generic"),
            state=str(payload.get("state") or DownloadPolicy.EXTRACTOR_ACCESS_STATE_BASIC_OK),
            action=str(payload.get("action") or DownloadPolicy.EXTRACTOR_ACCESS_ACTION_NONE),
            detail=str(payload.get("detail") or ""),
            scope=str(payload.get("scope") or ""),
            access_mode=str(payload.get("access_mode") or DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC),
            suggested_access_mode=str(
                payload.get("suggested_access_mode") or DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC
            ),
            provider_state=str(payload.get("provider_state") or ""),
            can_continue_basic=bool(payload.get("can_continue_basic", True)),
        )
        source_label = str(
            meta.get("title")
            or meta.get("webpage_url")
            or meta.get("extractor")
            or meta.get("extractor_key")
            or decision.extractor_key
            or ""
        ).strip()
        request = DownloadService._access_intervention_request_from_decision(decision, source_label=source_label)
        if request is None:
            return None
        if request.can_retry_enhanced:
            return request
        if decision.state in {
            DownloadPolicy.EXTRACTOR_ACCESS_STATE_PROVIDER_MISSING,
            DownloadPolicy.EXTRACTOR_ACCESS_STATE_UNAVAILABLE,
        } and (request.can_continue_basic or request.can_continue_degraded):
            return request
        return None

    @staticmethod
    def should_offer_source_access_intervention(
        ex: DownloadError,
        *,
        url: str,
        operation: str,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
    ) -> bool:
        """Return True when a download error can be resolved through an access intervention."""
        err_key = str(ex.key or "").strip()
        if err_key in {
            "error.down.authentication_required",
            "error.down.browser_cookies_unavailable",
            "error.down.cookie_file_invalid",
        }:
            context = DownloadService.resolve_cookie_context(
                browser_cookies_mode_override=browser_cookies_mode_override,
                cookie_file_override=cookie_file_override,
                browser_policy_override=browser_policy_override,
                interactive=True,
            )
            if err_key in {"error.down.authentication_required", "error.down.browser_cookies_unavailable"}:
                return context.mode == "from_browser"
            return context.mode == "from_file"
        if err_key == "error.down.extended_access_required":
            source_access_context = DownloadService.resolve_source_access_context(
                url,
                operation=operation,
                browser_cookies_mode_override=browser_cookies_mode_override,
                cookie_file_override=cookie_file_override,
                browser_policy_override=browser_policy_override,
                access_mode_override=access_mode_override,
                interactive=True,
            )
            decision = DownloadService._build_extractor_access_decision(
                extractor_context=source_access_context.extractor_context,
                runtime={
                    "extended_access_required": True,
                    "extended_access_required_detail": str((ex.params or {}).get("detail") or ""),
                },
            )
            return DownloadService._access_intervention_request_from_decision(
                decision,
                source_label=sanitize_url_for_log(url),
            ) is not None
        return False

    @staticmethod
    def intervention_request_from_error(
        ex: DownloadError,
        *,
        url: str,
        operation: str,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
    ) -> SourceAccessInterventionRequired | None:
        """Build a source-access intervention from a recoverable download error."""
        if not DownloadService.should_offer_source_access_intervention(
            ex,
            url=url,
            operation=operation,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
        ):
            return None

        detail = str((ex.params or {}).get("detail") or "").strip()
        err_key = str(ex.key or "").strip()
        if err_key in {
            "error.down.authentication_required",
            "error.down.browser_cookies_unavailable",
            "error.down.cookie_file_invalid",
        }:
            context = DownloadService.resolve_cookie_context(
                browser_cookies_mode_override=browser_cookies_mode_override,
                cookie_file_override=cookie_file_override,
                browser_policy_override=browser_policy_override,
                interactive=True,
            )
            return SourceAccessInterventionRequired(
                DownloadService._cookie_intervention_request(
                    context,
                    detail=detail,
                    can_retry=context.mode == "from_browser",
                )
            )

        source_access_context = DownloadService.resolve_source_access_context(
            url,
            operation=operation,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            interactive=True,
        )
        decision = DownloadService._build_extractor_access_decision(
            extractor_context=source_access_context.extractor_context,
            runtime={
                "extended_access_required": True,
                "extended_access_required_detail": detail,
            },
        )
        request = DownloadService._access_intervention_request_from_decision(
            decision,
            source_label=sanitize_url_for_log(url),
        )
        if request is None:
            return None
        return SourceAccessInterventionRequired(request)

    @staticmethod
    def _probe_diagnostics(meta: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(meta, dict):
            return {}
        diagnostics = meta.get("probe_diagnostics")
        return dict(diagnostics) if isinstance(diagnostics, dict) else {}

    @staticmethod
    def _raise_if_probe_blocks_download(
        meta: dict[str, Any] | None,
        *,
        cookie_context: DownloadCookieContext,
    ) -> None:
        diagnostics = DownloadService._probe_diagnostics(meta)
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
            raise DownloadError("error.down.authentication_required", detail=detail)

        if "extended_access_required" in warnings:
            detail = str(details.get("extended_access_required_detail") or "").strip()
            raise DownloadError("error.down.extended_access_required", detail=detail)

        if {"media_unavailable", "no_downloadable_formats", "no_public_formats"} & warnings:
            detail = str(details.get("no_downloadable_formats_detail") or "").strip()
            if not detail:
                detail = str(details.get("extractor_access_limited_detail") or "").strip()
            if not detail:
                detail = "no downloadable media formats found during probe"
            raise DownloadError("error.down.no_downloadable_formats", detail=detail)

        if cookie_context.mode != "from_browser" or "browser_cookies_unavailable" not in warnings:
            return

        failures = list(details.get("cookie_browser_failures") or [])
        detail = ""
        if failures and isinstance(failures[0], dict):
            detail = str(failures[0].get("detail") or "").strip()
        detail = detail or str(details.get("authentication_detail") or "").strip()
        raise DownloadError("error.down.browser_cookies_unavailable", detail=detail)

    @staticmethod
    def _emit_download_progress(
        progress_cb: Callable[[int, str], None] | None,
        *,
        pct: int,
        status: str,
    ) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(int(max(0, min(100, int(pct)))), str(status or ""))
        except (RuntimeError, TypeError, ValueError):
            return

    @staticmethod
    def _download_progress_pct(payload: dict[str, Any]) -> int:
        raw_pct = str(payload.get("_percent_str") or "").strip().replace("%", "")
        if raw_pct:
            try:
                return int(max(0.0, min(100.0, float(raw_pct))))
            except (TypeError, ValueError, OverflowError):
                return 0

        downloaded = payload.get("downloaded_bytes") or 0
        total = payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0
        try:
            if total:
                return int(max(0.0, min(100.0, (float(downloaded) / float(total)) * 100.0)))
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            return 0
        return 0

    def _build_download_hooks(
        self,
        *,
        progress_cb: Callable[[int, str], None] | None,
        cancel_check: Callable[[], bool] | None,
    ) -> tuple[Callable[[dict[str, Any]], None], Callable[[dict[str, Any]], None]]:
        def _hook(payload: dict[str, Any]) -> None:
            if cancel_check and cancel_check():
                raise OperationCancelled()

            status = str(payload.get("status") or "").strip().lower()
            if status == "downloading":
                self._emit_download_progress(
                    progress_cb,
                    pct=self._download_progress_pct(payload),
                    status="downloading",
                )
                return
            if status == "finished":
                self._emit_download_progress(progress_cb, pct=100, status="downloaded")

        def _post_hook(payload: dict[str, Any]) -> None:
            if cancel_check and cancel_check():
                raise OperationCancelled()

            status = str(payload.get("status") or "").strip().lower()
            if status == "started":
                self._emit_download_progress(progress_cb, pct=100, status="postprocessing")
                return
            if status == "finished":
                self._emit_download_progress(progress_cb, pct=100, status="postprocessed")

        return _hook, _post_hook

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
    ) -> Path | None:
        min_h = AppConfig.downloader_min_video_height()
        max_h = AppConfig.downloader_max_video_height()
        ext_l = (ext or "").lower().strip().lstrip(".")
        purpose_l = str(purpose or DownloadPolicy.DOWNLOAD_DEFAULT_PURPOSE).strip().lower()
        contract = DownloadPolicy.resolve_download_contract(
            kind=kind,
            purpose=purpose_l,
            keep_output=bool(keep_output),
            ext=ext_l,
        )
        plan_ext = str(contract.get("plan_ext") or "").strip().lower()
        final_ext = str(contract.get("final_ext") or "").strip().lower()
        artifact_policy = str(
            contract.get("artifact_policy") or DownloadPolicy.DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT
        ).strip().lower()

        audio_track_id_norm = str(audio_track_id or "").strip() or None
        lang_base = ""

        if meta is None:
            try:
                meta = self.probe(
                    url,
                    browser_cookies_mode_override=browser_cookies_mode_override,
                    cookie_file_override=cookie_file_override,
                    browser_policy_override=browser_policy_override,
                    access_mode_override=access_mode_override,
                    interactive=True,
                )
            except DownloadError as ex:
                err_key = str(ex.key or "").strip()
                if audio_track_id_norm or err_key in {
                    "error.down.authentication_required",
                    "error.down.browser_cookies_unavailable",
                    "error.down.cookie_file_invalid",
                    "error.down.extended_access_required",
                }:
                    raise
                meta = None

        source_access_context = self.resolve_source_access_context(
            url,
            operation=DownloadPolicy.DOWNLOAD_OPERATION_DOWNLOAD,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            interactive=True,
        )
        cookie_context = source_access_context.cookie_context
        extractor_context = source_access_context.extractor_context
        selected_probe_client = str(extractor_context.client or "").strip().lower() or "default"
        access_request = self.access_intervention_request_from_meta(meta)
        if access_request is not None:
            current_mode = DownloadPolicy.normalize_extractor_access_mode(extractor_context.access_mode)
            suggested_mode = DownloadPolicy.normalize_extractor_access_mode(access_request.suggested_access_mode)
            explicit_mode = (
                DownloadPolicy.normalize_extractor_access_mode(access_mode_override)
                if access_mode_override
                else ""
            )
            should_raise_access_request = not (explicit_mode and explicit_mode == current_mode)
            if should_raise_access_request and suggested_mode != current_mode:
                raise SourceAccessInterventionRequired(access_request)
        self._raise_if_probe_blocks_download(meta, cookie_context=cookie_context)

        selected_audio_track = None
        if audio_track_id_norm:
            selected_audio_track = TrackInventory.find_audio_track(meta, audio_track_id_norm)
            if selected_audio_track is None:
                raise DownloadError(
                    "error.down.download_failed",
                    detail="selected audio track is no longer available",
                )

        if selected_audio_track is not None:
            ordered_probe_clients = self._ordered_track_download_clients(
                selected_audio_track,
                meta=meta,
                extractor_context=extractor_context,
            )
            plan, selected_probe_client = DownloadPlanBuilder.build_explicit_plan(
                kind=kind,
                quality=quality,
                plan_ext=plan_ext,
                lang_base=lang_base,
                selected_audio_track=selected_audio_track,
                ordered_probe_clients=ordered_probe_clients,
                purpose=purpose_l,
                keep_output=bool(keep_output),
                meta=meta,
                min_h=min_h,
                max_h=max_h,
            )
            source_access_context = source_access_context.with_client(selected_probe_client)
        else:
            if kind == "audio":
                plan = DownloadPlanBuilder.build_audio_plan(
                    info=meta,
                    quality=quality,
                    ext_l=plan_ext,
                    lang_base=lang_base,
                    selected_audio_track=None,
                    purpose=purpose_l,
                    keep_output=bool(keep_output),
                )
            else:
                plan = DownloadPlanBuilder.build_video_plan(
                    info=meta,
                    quality=quality,
                    ext_l=plan_ext,
                    lang_base=lang_base,
                    selected_audio_track=None,
                    purpose=purpose_l,
                    keep_output=bool(keep_output),
                    min_h=min_h,
                    max_h=max_h,
                )

        progress_hook, post_hook = self._build_download_hooks(progress_cb=progress_cb, cancel_check=cancel_check)
        stem = sanitize_filename(file_stem or "%(title)s") or DownloadPolicy.DOWNLOAD_DEFAULT_STEM
        stage_dir = DownloadArtifactManager.create_download_stage(stem=stem)
        outtmpl = DownloadArtifactManager.build_stage_outtmpl(stage_dir=stage_dir, stem=stem)

        self._validate_cookie_context(cookie_context)
        ydl_opts: dict[str, Any] = YtdlpGateway.base_ydl_opts(
            url=url,
            quiet=not _LOG.isEnabledFor(logging.DEBUG),
            skip_download=False,
            cookie_context=cookie_context,
            source_access_context=source_access_context,
        )
        ydl_opts.update(
            {
                "format": plan.get("format")
                or (
                    DownloadPolicy.DOWNLOAD_FALLBACK_AUDIO_SELECTOR
                    if kind == "audio"
                    else DownloadPolicy.DOWNLOAD_FALLBACK_VIDEO_SELECTOR
                ),
                "outtmpl": outtmpl,
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [post_hook],
                "postprocessors": list(plan.get("postprocessors") or []),
            }
        )
        format_sort = list(plan.get("format_sort") or [])
        if format_sort:
            ydl_opts["format_sort"] = format_sort
        merge_output_format = str(plan.get("merge_output_format") or "").strip().lower()
        if merge_output_format:
            ydl_opts["merge_output_format"] = merge_output_format

        _LOG.debug(
            (
                "Download started. url=%s kind=%s quality=%s ext=%s audio_track_id=%s purpose=%s "
                "keep_output=%s probe_client=%s final_out_dir=%s stage_dir=%s stem=%s plan=%s"
            ),
            sanitize_url_for_log(url),
            kind,
            quality,
            ext_l,
            audio_track_id_norm or "",
            purpose_l,
            bool(keep_output),
            selected_probe_client,
            out_dir,
            stage_dir,
            stem,
            {
                "format": ydl_opts.get("format"),
                "format_sort": format_sort,
                "merge_output_format": merge_output_format,
                "postprocessors": ydl_opts.get("postprocessors"),
                "extractor_args": ydl_opts.get("extractor_args"),
                "plan_ext": plan_ext,
                "final_ext": final_ext,
                "artifact_policy": artifact_policy,
            },
        )

        info: dict[str, Any] | None = None
        try:
            info, download_runtime = YtdlpGateway.extract_info_with_fallback(
                url=url,
                ydl_opts=ydl_opts,
                download=True,
                allow_cookie_intervention=True,
            )
            if download_runtime.get("js_runtime_fallback"):
                _LOG.info(
                    "Download continued after JS runtime fallback. url=%s detail=%s",
                    sanitize_url_for_log(url),
                    str(download_runtime.get("js_runtime_error") or ""),
                )

            stage_files = DownloadArtifactManager.stage_files(stage_dir)
            _LOG.debug(
                (
                    "Download postprocess state. url=%s requested_ext=%s info_ext=%s "
                    "info_filepath=%s stage_dir=%s stage_files=%s"
                ),
                sanitize_url_for_log(url),
                ext_l,
                DownloadArtifactManager.normalize_ext((info or {}).get("ext")),
                str((info or {}).get("filepath") or (info or {}).get("_filename") or ""),
                str(stage_dir),
                [path.name for path in stage_files],
            )

            artifact = DownloadArtifactManager.resolve_stage_artifact(
                info=info,
                stage_dir=stage_dir,
                stem=stem,
                requested_ext=final_ext,
                artifact_policy=artifact_policy,
            )
            if artifact is None:
                _LOG.warning(
                    (
                        "Download finished without stage artifact. url=%s requested_ext=%s final_ext=%s "
                        "artifact_policy=%s info_ext=%s stage_dir=%s stage_files=%s"
                    ),
                    sanitize_url_for_log(url),
                    ext_l,
                    final_ext,
                    artifact_policy,
                    DownloadArtifactManager.normalize_ext((info or {}).get("ext")),
                    str(stage_dir),
                    [path.name for path in stage_files],
                )
                DownloadArtifactManager.cleanup_stage_dir(stage_dir)
                raise DownloadError(
                    "error.down.download_failed",
                    detail="download finished without a final stage artifact",
                )

            should_promote = purpose_l == DownloadPolicy.DOWNLOAD_PURPOSE_DOWNLOAD or bool(keep_output)
            if should_promote:
                promoted = DownloadArtifactManager.promote_stage_artifact(
                    artifact=artifact,
                    final_dir=out_dir,
                    stem=stem,
                    requested_ext=final_ext,
                )
                DownloadArtifactManager.cleanup_stage_dir(stage_dir)
                _LOG.info(
                    (
                        "Download finished. url=%s requested_ext=%s final_ext=%s artifact_policy=%s "
                        "resolved_artifact=%s promoted=%s"
                    ),
                    sanitize_url_for_log(url),
                    ext_l,
                    final_ext,
                    artifact_policy,
                    artifact.name,
                    promoted.name,
                )
                return promoted

            _LOG.info(
                "Download finished in staging. url=%s requested_ext=%s final_ext=%s artifact_policy=%s path=%s",
                sanitize_url_for_log(url),
                ext_l,
                final_ext,
                artifact_policy,
                artifact.name,
            )
            return artifact
        except OperationCancelled:
            stage_files = DownloadArtifactManager.stage_files(stage_dir)
            DownloadArtifactManager.cleanup_stage_dir(stage_dir)
            _LOG.debug(
                "Download cancelled. url=%s stage_dir=%s stage_files=%s",
                sanitize_url_for_log(url),
                str(stage_dir),
                [path.name for path in stage_files],
            )
            raise
        except DownloadError:
            DownloadArtifactManager.cleanup_stage_dir(stage_dir)
            raise
        except SourceAccessInterventionRequired:
            DownloadArtifactManager.cleanup_stage_dir(stage_dir)
            raise
        except Exception as ex:
            stage_files = DownloadArtifactManager.stage_files(stage_dir)
            DownloadArtifactManager.cleanup_stage_dir(stage_dir)
            network_key = YtdlpGateway.classify_network_error(ex)
            if network_key:
                YtdlpGateway.log_network_error(action="download", url=url, ex=ex)
                raise DownloadError(network_key)
            _LOG.debug(
                (
                    "Download failed. url=%s requested_ext=%s final_ext=%s artifact_policy=%s info_ext=%s "
                    "info_filepath=%s stage_dir=%s stage_files=%s detail=%s"
                ),
                sanitize_url_for_log(url),
                ext_l,
                final_ext,
                artifact_policy,
                DownloadArtifactManager.normalize_ext((info or {}).get("ext")),
                str((info or {}).get("filepath") or (info or {}).get("_filename") or ""),
                str(stage_dir),
                [path.name for path in stage_files],
                str(ex),
            )
            raise DownloadError("error.down.download_failed", detail=str(ex))
