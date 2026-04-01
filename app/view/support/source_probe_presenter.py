# app/view/support/source_probe_presenter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.model.core.runtime.localization import tr
from app.model.download.policy import DownloadPolicy


@dataclass(frozen=True)
class SourceProbePresentation:
    """Ready-to-render probe notice state for one source-table row."""

    status_tooltip: str = ""
    audio_tooltip: str = ""
    status_visible_for: tuple[str, ...] = ()
    keep_row: bool = True


_DEFAULT_IDLE_STATUS_KEYS = ("status.queued", "status.offline")


def build_probe_success_presentation(
    meta: dict[str, Any],
    *,
    visible_status_keys: tuple[str, ...] = _DEFAULT_IDLE_STATUS_KEYS,
) -> SourceProbePresentation:
    """Build row presentation for a successful source probe result."""
    diagnostics = meta.get("probe_diagnostics") or {}
    if not isinstance(diagnostics, dict):
        return SourceProbePresentation(status_visible_for=tuple(str(key) for key in visible_status_keys if str(key).strip()))

    warnings = {str(item).strip() for item in (diagnostics.get("warnings") or ()) if str(item).strip()}
    details = dict(diagnostics.get("details") or {})
    decision = dict(details.get("extractor_access_decision") or {})
    decision_state = str(decision.get("state") or details.get("extractor_access_state") or "").strip()
    decision_action = str(decision.get("action") or details.get("extractor_action") or "").strip()
    has_diagnostics = bool(warnings or (diagnostics.get("errors") or []) or decision_state)
    has_choices = _has_audio_track_choices(meta)

    notice = _resolve_probe_notice(warnings=warnings, decision_state=decision_state, decision_action=decision_action)
    should_notice = has_diagnostics and (
        not has_choices
        or "browser_cookies_unavailable" in warnings
        or "authentication_required" in warnings
        or "extended_access_required" in warnings
        or "extractor_access_limited" in warnings
        or bool(decision_state)
        or "media_unavailable" in warnings
        or "no_downloadable_formats" in warnings
        or "no_public_formats" in warnings
        or "audio_tracks_probe_only" in warnings
    )
    visible = tuple(str(key).strip() for key in visible_status_keys if str(key).strip())
    if not should_notice:
        return SourceProbePresentation(status_visible_for=visible)
    return SourceProbePresentation(
        status_tooltip=notice,
        audio_tooltip=notice,
        status_visible_for=visible,
    )


def build_probe_error_presentation(err_key: str, params: dict[str, Any]) -> SourceProbePresentation:
    """Build row presentation for a failed source probe."""
    tooltip = tr(str(err_key or "error.generic").strip() or "error.generic", **(params or {}))
    return SourceProbePresentation(
        status_tooltip=tooltip,
        status_visible_for=("status.error",),
        keep_row=True,
    )


def _has_audio_track_choices(meta: dict[str, Any]) -> bool:
    raw_tracks = [track for track in list(meta.get("audio_tracks") or []) if isinstance(track, dict)]
    track_ids = {
        str(track.get("track_id") or "").strip()
        for track in raw_tracks
        if str(track.get("track_id") or "").strip()
    }
    return len(track_ids) > 1


def _resolve_probe_notice(*, warnings: set[str], decision_state: str, decision_action: str) -> str:
    default_notice = tr("status.notice.metadata_incomplete")
    if "browser_cookies_unavailable" in warnings:
        return tr("status.notice.browser_cookies_unavailable")
    if "authentication_required" in warnings:
        return tr("status.notice.authentication_required")
    if DownloadPolicy.is_unavailable_extractor_access_state(decision_state):
        return tr("status.notice.extended_access_unavailable")
    if DownloadPolicy.is_limited_extractor_access_decision(decision_state, decision_action):
        return tr("status.notice.extended_access_limited")
    if "extended_access_required" in warnings or "extractor_access_limited" in warnings:
        return tr("status.notice.extended_access_required")
    if (
        "media_unavailable" in warnings
        or "no_downloadable_formats" in warnings
        or "no_public_formats" in warnings
    ):
        return tr("status.notice.media_unavailable")
    if "audio_tracks_probe_only" in warnings:
        return tr("status.notice.audio_tracks_probe_only")
    return default_notice
