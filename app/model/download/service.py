# app/model/download/service.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.model.core.domain.entities import PlaylistResolveResult
from app.model.download.domain import DownloadError, SourceAccessInterventionRequired
from app.model.download.inventory import TrackInventory
from app.model.download.runtime import available_cookie_browsers, resolve_effective_cookie_browser

from .access import intervention_request_from_error
from .playlist import resolve_playlist
from .probe import probe
from .transfer import download


class DownloadService:
    """Public download facade for playlist resolve, probe and transfer flows."""

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
        return available_cookie_browsers()

    @staticmethod
    def resolve_effective_cookie_browser(policy_browser: str | None) -> str:
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
        return resolve_playlist(
            url,
            cancel_check=cancel_check,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            interactive=interactive,
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
        return probe(
            url,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
            interactive=interactive,
        )

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
        return intervention_request_from_error(
            ex,
            url=url,
            operation=operation,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
        )

    @staticmethod
    def download(
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
        purpose: str = "download",
        keep_output: bool = True,
        meta: dict[str, Any] | None = None,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
        browser_policy_override: str | None = None,
        access_mode_override: str | None = None,
    ) -> Path | None:
        return download(
            url=url,
            kind=kind,
            quality=quality,
            ext=ext,
            out_dir=out_dir,
            progress_cb=progress_cb,
            audio_track_id=audio_track_id,
            file_stem=file_stem,
            cancel_check=cancel_check,
            purpose=purpose,
            keep_output=keep_output,
            meta=meta,
            browser_cookies_mode_override=browser_cookies_mode_override,
            cookie_file_override=cookie_file_override,
            browser_policy_override=browser_policy_override,
            access_mode_override=access_mode_override,
        )
