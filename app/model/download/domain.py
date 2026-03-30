# app/model/download/domain.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.model.core.domain.errors import AppError


class DownloadError(AppError):
    """Key-based error used for i18n-friendly download failures."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


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
class CookieInterventionRequest:
    """User-actionable browser-cookie failure requiring an explicit next step."""

    browser: str
    detail: str = ""
    can_continue_without_cookies: bool = True

    def as_payload(self) -> dict[str, Any]:
        """Return the serialized payload used by worker/controller UI flows."""
        return {
            "browser": self.browser,
            "detail": self.detail,
            "can_continue_without_cookies": bool(self.can_continue_without_cookies),
        }


class DownloadInterventionRequired(Exception):
    """Raised when a download flow needs a UI decision before it can continue."""

    def __init__(self, request: CookieInterventionRequest) -> None:
        super().__init__(str(request.detail or request.browser or "download intervention required"))
        self.request = request
