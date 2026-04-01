# app/model/download/domain.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.model.core.domain.errors import AppError

class DownloadError(AppError):
    """Key-based error used for i18n-friendly download failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))

@dataclass(frozen=True)
class DownloadCookieContext:
    """Resolved cookie-source context for a single yt_dlp operation."""

    mode: str
    browser_policy: str = ""
    cookie_file_path: str = ""
    interactive: bool = False

@dataclass(frozen=True)
class ExtractorCapabilityReport:
    """Runtime capability snapshot for an extractor-specific access strategy."""

    extractor_key: str = "generic"
    supports_extended_access: bool = False
    enhanced_mode_available: bool = False
    provider_plugin_available: bool = False
    provider_name: str = ""
    provider_state: str = ""
    provider_install_hint: str = ""
    provider_detail: str = ""
    visitor_data_supported: bool = False
    po_token_supported: bool = False
    basic_only_reason: str = ""
    notes: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """Return a serialized capability payload for diagnostics and UI flows."""
        return {
            "extractor_key": str(self.extractor_key or "generic"),
            "supports_extended_access": bool(self.supports_extended_access),
            "enhanced_mode_available": bool(self.enhanced_mode_available),
            "provider_plugin_available": bool(self.provider_plugin_available),
            "provider_name": str(self.provider_name or ""),
            "provider_state": str(self.provider_state or ""),
            "provider_install_hint": str(self.provider_install_hint or ""),
            "provider_detail": str(self.provider_detail or ""),
            "visitor_data_supported": bool(self.visitor_data_supported),
            "po_token_supported": bool(self.po_token_supported),
            "basic_only_reason": str(self.basic_only_reason or ""),
            "notes": list(self.notes or ()),
        }

@dataclass(frozen=True)
class ExtractorAccessContext:
    """Resolved extractor-specific access strategy for a single operation."""

    extractor_key: str = "generic"
    operation: str = "download"
    access_mode: str = "basic"
    client: str = "default"
    allow_degraded_access: bool = True
    player_skip: tuple[str, ...] = ()
    visitor_data: str = ""
    po_token: str = ""
    fetch_po_token_policy: str = ""
    runtime_capabilities: ExtractorCapabilityReport = field(default_factory=ExtractorCapabilityReport)

    def as_payload(self) -> dict[str, Any]:
        """Return a serialized access payload for diagnostics and UI flows."""
        return {
            "extractor_key": str(self.extractor_key or "generic"),
            "operation": str(self.operation or "download"),
            "access_mode": str(self.access_mode or "basic"),
            "client": str(self.client or "default"),
            "allow_degraded_access": bool(self.allow_degraded_access),
            "player_skip": list(self.player_skip or ()),
            "visitor_data_present": bool(str(self.visitor_data or "").strip()),
            "po_token_present": bool(str(self.po_token or "").strip()),
            "fetch_po_token_policy": str(self.fetch_po_token_policy or ""),
            "runtime_capabilities": self.runtime_capabilities.as_payload(),
        }

    def with_client(self, client: str) -> ExtractorAccessContext:
        """Return a copy targeting a specific extractor client."""
        normalized_client = str(client or "").strip().lower() or "default"
        if normalized_client == str(self.client or "default").strip().lower():
            return self
        return ExtractorAccessContext(
            extractor_key=self.extractor_key,
            operation=self.operation,
            access_mode=self.access_mode,
            client=normalized_client,
            allow_degraded_access=self.allow_degraded_access,
            player_skip=self.player_skip,
            visitor_data=self.visitor_data,
            po_token=self.po_token,
            fetch_po_token_policy=self.fetch_po_token_policy,
            runtime_capabilities=self.runtime_capabilities,
        )

    def uses_enhanced_access(self) -> bool:
        """Return True when the current context prefers enhanced extractor access."""
        return str(self.access_mode or "").strip().lower() == "enhanced"

    def with_access_mode(self, access_mode: str, *, client: str | None = None) -> ExtractorAccessContext:
        """Return a copy with an updated access mode and optional client."""
        next_client = str(client or self.client or "").strip().lower() or "default"
        normalized_mode = str(access_mode or "").strip().lower() or "basic"
        if (
            normalized_mode == str(self.access_mode or "").strip().lower()
            and next_client == str(self.client or "default").strip().lower()
        ):
            return self
        return ExtractorAccessContext(
            extractor_key=self.extractor_key,
            operation=self.operation,
            access_mode=normalized_mode,
            client=next_client,
            allow_degraded_access=self.allow_degraded_access,
            player_skip=self.player_skip,
            visitor_data=self.visitor_data,
            po_token=self.po_token,
            fetch_po_token_policy=self.fetch_po_token_policy,
            runtime_capabilities=self.runtime_capabilities,
        )

@dataclass(frozen=True)
class ExtractorAccessDecision:
    """Operational decision describing the current extractor access state."""

    extractor_key: str = "generic"
    state: str = "basic_ok"
    action: str = "none"
    detail: str = ""
    scope: str = ""
    access_mode: str = "basic"
    suggested_access_mode: str = "basic"
    provider_state: str = ""
    can_continue_basic: bool = True

    def as_payload(self) -> dict[str, Any]:
        """Return the serialized decision payload for diagnostics and UI flows."""
        return {
            "extractor_key": str(self.extractor_key or "generic"),
            "state": str(self.state or "basic_ok"),
            "action": str(self.action or "none"),
            "detail": str(self.detail or ""),
            "scope": str(self.scope or ""),
            "access_mode": str(self.access_mode or "basic"),
            "suggested_access_mode": str(self.suggested_access_mode or self.access_mode or "basic"),
            "provider_state": str(self.provider_state or ""),
            "can_continue_basic": bool(self.can_continue_basic),
        }

@dataclass(frozen=True)
class SourceAccessContext:
    """Combined source-access context shared by yt_dlp operations."""

    cookie_context: DownloadCookieContext
    extractor_context: ExtractorAccessContext

    def as_payload(self) -> dict[str, Any]:
        """Return a serialized access payload for diagnostics and logs."""
        return {
            "cookie_context": {
                "mode": str(self.cookie_context.mode or "none"),
                "browser_policy": str(self.cookie_context.browser_policy or ""),
                "cookie_file_path": str(self.cookie_context.cookie_file_path or ""),
                "interactive": bool(self.cookie_context.interactive),
            },
            "extractor_context": self.extractor_context.as_payload(),
        }

    def with_client(self, client: str) -> SourceAccessContext:
        """Return a copy with an updated extractor client."""
        updated_extractor_context = self.extractor_context.with_client(client)
        if updated_extractor_context is self.extractor_context:
            return self
        return SourceAccessContext(
            cookie_context=self.cookie_context,
            extractor_context=updated_extractor_context,
        )

@dataclass(frozen=True)
class CookieBrowserAttempt:
    """Single browser-cookie probe attempt recorded during yt_dlp access."""

    browser: str
    detail: str = ""
    kind: str = ""

    def as_payload(self) -> dict[str, str]:
        """Return the serialized payload used by download diagnostics."""
        payload = {"browser": self.browser, "detail": self.detail}
        if self.kind:
            payload["kind"] = self.kind
        return payload

@dataclass(frozen=True)
class SourceAccessInterventionRequest:
    """User-actionable source-access issue that requires an explicit next step."""

    kind: str = "cookies"
    source_kind: str = ""
    source_label: str = ""
    detail: str = ""
    state: str = ""
    action: str = ""
    suggested_access_mode: str = ""
    provider_state: str = ""
    browser_policy: str = ""
    available_browser_policies: tuple[str, ...] = ()
    can_retry: bool = False
    can_choose_cookie_file: bool = False
    can_continue_without_cookies: bool = False
    can_retry_enhanced: bool = False
    can_continue_basic: bool = False
    can_continue_degraded: bool = False

    def as_payload(self) -> dict[str, Any]:
        """Return the serialized payload used by worker/controller UI flows."""
        return {
            "kind": str(self.kind or "cookies"),
            "source_kind": str(self.source_kind or ""),
            "source_label": str(self.source_label or ""),
            "detail": str(self.detail or ""),
            "state": str(self.state or ""),
            "action": str(self.action or ""),
            "suggested_access_mode": str(self.suggested_access_mode or ""),
            "provider_state": str(self.provider_state or ""),
            "browser_policy": str(self.browser_policy or ""),
            "available_browser_policies": [str(item or "") for item in (self.available_browser_policies or ())],
            "can_retry": bool(self.can_retry),
            "can_choose_cookie_file": bool(self.can_choose_cookie_file),
            "can_continue_without_cookies": bool(self.can_continue_without_cookies),
            "can_retry_enhanced": bool(self.can_retry_enhanced),
            "can_continue_basic": bool(self.can_continue_basic),
            "can_continue_degraded": bool(self.can_continue_degraded),
        }

@dataclass(frozen=True)
class SourceAccessInterventionResolution:
    """Normalized UI decision returned for a source-access intervention."""

    action: str = "cancel"
    cookie_file_path: str = ""
    browser_policy: str = ""

    def as_payload(self) -> dict[str, str]:
        """Return the serialized payload shared across panel/coordinator boundaries."""
        return {
            "action": str(self.action or "cancel"),
            "cookie_file_path": str(self.cookie_file_path or ""),
            "browser_policy": str(self.browser_policy or ""),
        }

    @classmethod
    def from_payload(cls, payload: Any) -> SourceAccessInterventionResolution:
        """Build a normalized resolution from a dict-like payload."""
        if isinstance(payload, cls):
            return payload
        if isinstance(payload, dict):
            return cls(
                action=str(payload.get("action") or "cancel").strip().lower() or "cancel",
                cookie_file_path=str(payload.get("cookie_file_path") or "").strip(),
                browser_policy=str(payload.get("browser_policy") or "").strip().lower(),
            )
        return cls()

class SourceAccessInterventionRequired(Exception):
    """Raised when a source-access flow needs a UI decision before it can continue."""

    def __init__(self, request: SourceAccessInterventionRequest) -> None:
        super().__init__(str(request.detail or request.source_label or "source access intervention required"))
        self.request = request
