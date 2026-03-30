# app/model/download/strategy.py
from __future__ import annotations

from typing import Any

from app.model.download.domain import ExtractorAccessContext, ExtractorCapabilityReport
from app.model.download.policy import DownloadPolicy


class ExtractorStrategy:
    """Base extractor access strategy used by yt_dlp operations."""

    extractor_key: str = DownloadPolicy.EXTRACTOR_KEY_GENERIC

    def build_access_context(
        self,
        *,
        operation: str,
        runtime_capabilities: ExtractorCapabilityReport,
    ) -> ExtractorAccessContext:
        """Build the default extractor access context for an operation."""
        return ExtractorAccessContext(
            extractor_key=self.extractor_key,
            operation=DownloadPolicy.normalize_download_operation(operation),
            access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC,
            client="default",
            allow_degraded_access=True,
            runtime_capabilities=runtime_capabilities,
        )

    def probe_clients(self, context: ExtractorAccessContext) -> tuple[str, ...]:
        """Return ordered probe clients for the current context."""
        return ("default",)

    def collect_probe_variants(self, context: ExtractorAccessContext) -> bool:
        """Return True when probe variants should be kept for diagnostics."""
        return False

    def build_extractor_args(self, context: ExtractorAccessContext) -> dict[str, Any]:
        """Return extractor_args for the current access context."""
        return {}

    def select_download_clients(
        self,
        context: ExtractorAccessContext,
        available_clients: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return ordered download clients compatible with the current strategy."""
        normalized_available: list[str] = []
        seen: set[str] = set()
        for client in tuple(available_clients or ()):
            normalized_client = str(client or "").strip().lower() or "default"
            if normalized_client in seen:
                continue
            seen.add(normalized_client)
            normalized_available.append(normalized_client)

        if not normalized_available:
            return ()

        preferred_client = str(context.client or "").strip().lower() or "default"
        ordered: list[str] = []

        if preferred_client in normalized_available:
            ordered.append(preferred_client)

        for client in self.probe_clients(context):
            normalized_client = str(client or "").strip().lower() or "default"
            if normalized_client in normalized_available and normalized_client not in ordered:
                ordered.append(normalized_client)

        for client in normalized_available:
            if client not in ordered:
                ordered.append(client)

        if not context.allow_degraded_access and ordered:
            return (ordered[0],)
        return tuple(ordered)


class GenericStrategy(ExtractorStrategy):
    """Fallback extractor strategy for services without custom access rules."""

    extractor_key = DownloadPolicy.EXTRACTOR_KEY_GENERIC


class YouTubeStrategy(ExtractorStrategy):
    """YouTube-specific extractor strategy built around provider-backed client selection."""

    extractor_key = DownloadPolicy.EXTRACTOR_KEY_YOUTUBE

    def build_access_context(
        self,
        *,
        operation: str,
        runtime_capabilities: ExtractorCapabilityReport,
    ) -> ExtractorAccessContext:
        """Build the default YouTube access context for an operation.

        The plain no-auth path intentionally starts in basic mode. Extended
        access is only entered after an explicit override or user decision.
        """
        normalized_operation = DownloadPolicy.normalize_download_operation(operation)
        player_skip: tuple[str, ...] = ()

        if normalized_operation == DownloadPolicy.DOWNLOAD_OPERATION_PLAYLIST:
            player_skip = ("configs",)

        return ExtractorAccessContext(
            extractor_key=self.extractor_key,
            operation=normalized_operation,
            access_mode=DownloadPolicy.EXTRACTOR_ACCESS_MODE_BASIC,
            client="default",
            allow_degraded_access=True,
            player_skip=player_skip,
            fetch_po_token_policy="never",
            runtime_capabilities=runtime_capabilities,
        )

    def probe_clients(self, context: ExtractorAccessContext) -> tuple[str, ...]:
        """Return ordered probe clients for the YouTube access context."""
        normalized_mode = DownloadPolicy.normalize_extractor_access_mode(context.access_mode)
        preferred_client = str(context.client or "").strip().lower() or "default"
        ordered: list[str] = []
        if normalized_mode in {
            DownloadPolicy.EXTRACTOR_ACCESS_MODE_ENHANCED,
            DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED,
        }:
            for candidate in DownloadPolicy.youtube_enhanced_probe_clients():
                normalized_candidate = str(candidate or "").strip().lower() or "default"
                if normalized_candidate not in ordered:
                    ordered.append(normalized_candidate)
            if preferred_client in ordered:
                ordered.remove(preferred_client)
                ordered.insert(0, preferred_client)
            elif preferred_client:
                ordered.insert(0, preferred_client)
            return tuple(ordered)
        return DownloadPolicy.youtube_basic_probe_clients()

    def collect_probe_variants(self, context: ExtractorAccessContext) -> bool:
        """Return True when probe diagnostics should keep per-client variants."""
        return (
            DownloadPolicy.normalize_download_operation(context.operation)
            == DownloadPolicy.DOWNLOAD_OPERATION_PROBE
            and len(self.probe_clients(context)) > 1
        )

    def build_extractor_args(self, context: ExtractorAccessContext) -> dict[str, Any]:
        """Return YouTube extractor args for the selected access context."""
        youtube_args: dict[str, Any] = {}
        client = str(context.client or "").strip().lower() or "default"
        if client != "default":
            youtube_args["player_client"] = [client]

        player_skip = [
            str(item or "").strip()
            for item in tuple(context.player_skip or ())
            if str(item or "").strip()
        ]
        access_mode = DownloadPolicy.normalize_extractor_access_mode(context.access_mode)
        if access_mode == DownloadPolicy.EXTRACTOR_ACCESS_MODE_DEGRADED and "webpage" not in player_skip:
            player_skip.append("webpage")
        if player_skip:
            youtube_args["player_skip"] = player_skip

        visitor_data = str(context.visitor_data or "").strip()
        if visitor_data and context.runtime_capabilities.visitor_data_supported:
            youtube_args["visitor_data"] = [visitor_data]
            if not player_skip:
                youtube_args["player_skip"] = ["webpage", "configs"]

        po_token = str(context.po_token or "").strip()
        if po_token and context.runtime_capabilities.po_token_supported:
            youtube_args["po_token"] = [po_token]

        fetch_po_token_policy = str(context.fetch_po_token_policy or "").strip().lower()
        if fetch_po_token_policy:
            youtube_args["fetch_pot"] = [fetch_po_token_policy]

        return {"youtube": youtube_args} if youtube_args else {}


_STRATEGIES: dict[str, ExtractorStrategy] = {
    DownloadPolicy.EXTRACTOR_KEY_GENERIC: GenericStrategy(),
    DownloadPolicy.EXTRACTOR_KEY_YOUTUBE: YouTubeStrategy(),
}


def resolve_extractor_strategy(extractor_key: str | None) -> ExtractorStrategy:
    """Return the strategy object for a normalized extractor key."""
    normalized_extractor_key = DownloadPolicy.normalize_extractor_key(extractor_key)
    return _STRATEGIES.get(normalized_extractor_key, _STRATEGIES[DownloadPolicy.EXTRACTOR_KEY_GENERIC])


def resolve_extractor_strategy_for_url(url: str | None) -> ExtractorStrategy:
    """Return the strategy object chosen for a source URL."""
    return resolve_extractor_strategy(DownloadPolicy.extractor_key_for_url(url))
