# app/model/download/access.py
from __future__ import annotations

from pathlib import Path

from app.model.core.config.config import AppConfig
from app.model.core.utils.string_utils import sanitize_url_for_log
from app.model.download.cookies import validate_cookie_file
from app.model.download.domain import (
    DownloadCookieContext,
    DownloadError,
    ExtractorAccessContext,
    ExtractorAccessDecision,
    SourceAccessContext,
    SourceAccessInterventionRequest,
    SourceAccessInterventionRequired,
)
from app.model.download.policy import DownloadPolicy
from app.model.download.runtime import (
    available_cookie_browsers,
    detect_extractor_capabilities,
    resolve_cookie_browser_candidates,
    resolve_effective_cookie_browser,
)
from app.model.download.strategy import resolve_extractor_strategy_for_url


def resolve_cookie_context(
    *,
    browser_cookies_mode_override: str | None = None,
    cookie_file_override: str | None = None,
    browser_policy_override: str | None = None,
    interactive: bool = False,
) -> DownloadCookieContext:
    """Resolve the cookie source used by one yt_dlp operation."""

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

    cookie_context = resolve_cookie_context(
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        interactive=interactive,
    )
    extractor_context = resolve_extractor_access_context(
        url,
        operation=operation,
        access_mode_override=access_mode_override,
    )
    return SourceAccessContext(
        cookie_context=cookie_context,
        extractor_context=extractor_context,
    )


def available_cookie_browser_policies(context: DownloadCookieContext) -> tuple[str, ...]:
    """Return usable browser-cookie policies for the current cookie context."""

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


def cookie_intervention_request(
    context: DownloadCookieContext,
    *,
    detail: str,
    can_retry: bool,
    can_choose_cookie_file: bool = True,
    can_continue_without_cookies: bool = True,
) -> SourceAccessInterventionRequest:
    """Build a cookie-specific user intervention request."""

    return SourceAccessInterventionRequest(
        kind="cookies",
        source_kind="file" if context.mode == "from_file" else "browser",
        source_label=cookie_source_label(context),
        detail=str(detail or "").strip(),
        browser_policy=(
            resolve_effective_cookie_browser(context.browser_policy)
            if context.mode == "from_browser"
            else ""
        ),
        available_browser_policies=available_cookie_browser_policies(context),
        can_retry=bool(can_retry),
        can_choose_cookie_file=bool(can_choose_cookie_file),
        can_continue_without_cookies=bool(can_continue_without_cookies),
    )


def cookie_source_label(context: DownloadCookieContext) -> str:
    """Return a human-friendly cookie source label used in UI flows."""

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


def validate_cookie_context(context: DownloadCookieContext) -> None:
    """Validate cookie input and raise an intervention or download error when needed."""

    if context.mode != "from_file":
        return
    result = validate_cookie_file(context.cookie_file_path)
    if result.ok:
        return
    if context.interactive:
        raise SourceAccessInterventionRequired(
            cookie_intervention_request(
                context,
                detail=result.detail,
                can_retry=False,
            )
        )
    raise DownloadError("error.download.cookie_file_invalid", detail=result.detail)


def build_extractor_access_decision(
    *,
    extractor_context: ExtractorAccessContext,
    runtime: dict[str, object] | None,
) -> ExtractorAccessDecision:
    """Build the effective extractor access decision from runtime diagnostics."""

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


def access_intervention_request_from_decision(
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


def access_intervention_request_from_meta(meta: dict[str, object] | None) -> SourceAccessInterventionRequest | None:
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
        suggested_access_mode=str(payload.get("suggested_access_mode") or DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC),
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
    request = access_intervention_request_from_decision(decision, source_label=source_label)
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
        "error.download.authentication_required",
        "error.download.browser_cookies_unavailable",
        "error.download.cookie_file_invalid",
    }:
        context = resolve_cookie_context(
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            interactive=True,
        )
        if err_key in {"error.download.authentication_required", "error.download.browser_cookies_unavailable"}:
            return context.mode == "from_browser"
        return context.mode == "from_file"
    if err_key == "error.download.extended_access_required":
        source_access_context = resolve_source_access_context(
            url,
            operation=operation,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            interactive=True,
        )
        decision = build_extractor_access_decision(
            extractor_context=source_access_context.extractor_context,
            runtime={
                "extended_access_required": True,
                "extended_access_required_detail": str((ex.params or {}).get("detail") or ""),
            },
        )
        return access_intervention_request_from_decision(
            decision,
            source_label=sanitize_url_for_log(url),
        ) is not None
    return False


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

    if not should_offer_source_access_intervention(
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
        "error.download.authentication_required",
        "error.download.browser_cookies_unavailable",
        "error.download.cookie_file_invalid",
    }:
        context = resolve_cookie_context(
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            interactive=True,
        )
        return SourceAccessInterventionRequired(
            cookie_intervention_request(
                context,
                detail=detail,
                can_retry=context.mode == "from_browser",
            )
        )

    source_access_context = resolve_source_access_context(
        url,
        operation=operation,
        browser_cookies_mode_override=browser_cookies_mode_override,
        cookie_file_override=cookie_file_override,
        browser_policy_override=browser_policy_override,
        access_mode_override=access_mode_override,
        interactive=True,
    )
    decision = build_extractor_access_decision(
        extractor_context=source_access_context.extractor_context,
        runtime={
            "extended_access_required": True,
            "extended_access_required_detail": detail,
        },
    )
    request = access_intervention_request_from_decision(
        decision,
        source_label=sanitize_url_for_log(url),
    )
    if request is None:
        return None
    return SourceAccessInterventionRequired(request)
