# app/model/download/gateway.py
from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from app.model.core.config.config import AppConfig
from app.model.core.domain.errors import OperationCancelled
from app.model.core.utils.string_utils import is_youtube_url, sanitize_url_for_log
from app.model.download.domain import (
    CookieBrowserAttempt,
    CookieInterventionRequest,
    DownloadError,
    DownloadInterventionRequired,
)
from app.model.download.policy import DownloadPolicy
from app.model.download.runtime import resolve_cookie_browser_candidates

_LOG = logging.getLogger(__name__)

_NOISE_PATTERNS: tuple[str, ...] = (
    "UNPLAYABLE formats",
    "developer option intended for debugging",
    "impersonation",
    "SABR streaming",
    "SABR-only",
    "GVS PO Token",
    "[debug]",
)

_NETWORK_TIMEOUT_MARKERS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "read timed out",
    "connection timed out",
)

_NETWORK_DNS_MARKERS: tuple[str, ...] = (
    "getaddrinfo",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname provided",
    "dns",
)

_NETWORK_OFFLINE_MARKERS: tuple[str, ...] = (
    "offline",
    "no internet",
    "network is down",
)

_NETWORK_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "network is unreachable",
    "connection refused",
    "connection reset",
    "connection aborted",
    "host unreachable",
    "unreachable",
)

_JS_RUNTIME_ERROR_MARKERS: tuple[str, ...] = (
    "js runtime",
    "js_runtimes",
    "remote component",
    "remote_components",
    "deno",
    "ejs",
)

_COOKIE_BROWSER_ERROR_MARKERS: tuple[str, ...] = (
    "could not copy",
    "could not find",
    "cookie database",
    "cookies database",
    "cookies from browser",
    "cookiesfrombrowser",
    "failed to decrypt",
    "database is locked",
)

_COOKIE_BROWSER_INTERVENTION_MARKERS: tuple[str, ...] = (
    "could not copy",
    "database is locked",
    "failed to decrypt",
)

_AUTH_REQUIRED_ERROR_MARKERS: tuple[str, ...] = (
    "sign in to confirm you’re not a bot",
    "sign in to confirm you're not a bot",
    "use --cookies-from-browser or --cookies for the authentication",
    "authentication is required",
    "login required",
)

_NO_DOWNLOADABLE_FORMAT_MARKERS: tuple[str, ...] = (
    "requested format is not available",
    "only images are available for download",
)

_COOKIE_BROWSER_LOCKED_MARKERS: tuple[str, ...] = (
    "could not copy",
    "database is locked",
    "sharing violation",
)

_COOKIE_BROWSER_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "could not find",
    "no such file or directory",
    "not found",
)

_COOKIE_BROWSER_DECRYPT_MARKERS: tuple[str, ...] = (
    "failed to decrypt",
    "decryption failed",
    "dpapi",
)

_YTDLP_EXCEPTIONS = (yt_dlp.DownloadError, OSError, ValueError, RuntimeError)


def _is_noisy(msg: str, extra_noise: tuple[str, ...] = ()) -> bool:
    text = str(msg)
    for key in _NOISE_PATTERNS:
        if key in text:
            return True
    for key in extra_noise:
        if key and str(key) in text:
            return True
    return False


def _normalize_ytdlp_detail(detail: Any) -> str:
    text = str(detail or "").strip()
    while text.lower().startswith("error:"):
        text = text[6:].strip()
    return text


class YtdlpLogger:
    """Minimal logger adapter for yt_dlp."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        extra_noise: tuple[str, ...] = (),
        cancel_check: Callable[[], bool] | None = None,
        event_sink: Callable[[str, str], None] | None = None,
    ) -> None:
        self._logger = logger
        self._extra_noise = tuple(extra_noise or ())
        self._cancel_check = cancel_check
        self._event_sink = event_sink

    def _guard_cancel(self) -> None:
        if self._cancel_check is not None and bool(self._cancel_check()):
            raise OperationCancelled()

    def _record(self, kind: str, text: str) -> str:
        normalized = _normalize_ytdlp_detail(text)
        if self._event_sink is not None and normalized:
            self._event_sink(kind, normalized)
        return normalized

    def debug(self, msg) -> None:
        self._guard_cancel()
        text = self._record("debug", str(msg))
        if self._logger.isEnabledFor(logging.DEBUG) and not _is_noisy(text, self._extra_noise):
            self._logger.debug("yt_dlp raw debug output. text=%s", text)

    def info(self, msg) -> None:
        self._guard_cancel()
        text = self._record("info", str(msg))
        if not _is_noisy(text, self._extra_noise):
            self._logger.info("yt_dlp raw info output. text=%s", text)

    def warning(self, msg) -> None:
        self._guard_cancel()
        text = self._record("warning", str(msg))
        if not _is_noisy(text, self._extra_noise):
            self._logger.warning("yt_dlp raw warning output. text=%s", text)

    def error(self, msg) -> None:
        self._guard_cancel()
        text = self._record("error", str(msg))
        if not _is_noisy(text, self._extra_noise):
            self._logger.error("yt_dlp raw error output. text=%s", text)


class YtdlpGateway:
    """Build yt_dlp options and runtime-aware extractor calls."""

    @staticmethod
    def normalize_probe_client(probe_client: str | None) -> str:
        normalized = str(probe_client or "").strip().lower()
        return normalized or "default"

    @staticmethod
    def probe_client_sort_key(probe_client: str | None) -> tuple[int, str]:
        normalized = YtdlpGateway.normalize_probe_client(probe_client)
        order = DownloadPolicy.youtube_advanced_probe_clients()
        try:
            idx = order.index(normalized)
        except ValueError:
            idx = len(order)
        return idx, normalized

    @staticmethod
    def classify_network_error(ex: Exception) -> str:
        text = str(ex or "").strip().lower()
        if isinstance(ex, (TimeoutError, socket.timeout)) or any(marker in text for marker in _NETWORK_TIMEOUT_MARKERS):
            return "error.down.network_timeout"
        if isinstance(ex, socket.gaierror) or any(marker in text for marker in _NETWORK_DNS_MARKERS):
            return "error.down.network_dns_failed"
        if any(marker in text for marker in _NETWORK_OFFLINE_MARKERS):
            return "error.down.network_offline"
        if isinstance(ex, ConnectionError) or any(marker in text for marker in _NETWORK_UNREACHABLE_MARKERS):
            return "error.down.network_unreachable"
        return ""

    @staticmethod
    def log_network_error(*, action: str, url: str, ex: Exception) -> None:
        _LOG.debug(
            "Download network error classified. action=%s url=%s detail=%s",
            action,
            sanitize_url_for_log(url),
            _normalize_ytdlp_detail(ex),
        )

    @staticmethod
    def pick_thumbnail_url(info: dict[str, Any]) -> str:
        direct = info.get("thumbnail")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        thumbs = info.get("thumbnails") or []
        if isinstance(thumbs, list):
            for thumb in reversed(thumbs):
                if not isinstance(thumb, dict):
                    continue
                url = thumb.get("url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
        return ""

    @staticmethod
    def js_runtimes_for(url: str) -> dict[str, Any] | None:
        if not is_youtube_url(url):
            return None
        deno_bin = AppConfig.PATHS.DENO_BIN
        if isinstance(deno_bin, Path) and deno_bin.exists():
            return {"deno": {"path": str(deno_bin)}}
        return None

    @staticmethod
    def without_js_runtime_opts(opts: dict[str, Any]) -> dict[str, Any]:
        clean = dict(opts or {})
        clean.pop("js_runtimes", None)
        clean.pop("remote_components", None)
        return clean

    @staticmethod
    def without_cookie_browser_opts(opts: dict[str, Any]) -> dict[str, Any]:
        clean = dict(opts or {})
        clean.pop("cookiesfrombrowser", None)
        return clean

    @staticmethod
    def with_cookie_browser_opts(opts: dict[str, Any], browser: str) -> dict[str, Any]:
        clean = dict(opts or {})
        clean["cookiesfrombrowser"] = (str(browser or "").strip().lower(),)
        return clean

    @staticmethod
    def without_cookie_file_opts(opts: dict[str, Any]) -> dict[str, Any]:
        clean = dict(opts or {})
        clean.pop("cookiefile", None)
        return clean

    @staticmethod
    def with_cookie_file_opts(opts: dict[str, Any], cookie_file_path: str) -> dict[str, Any]:
        clean = dict(opts or {})
        clean["cookiefile"] = str(cookie_file_path or "").strip()
        return clean

    @staticmethod
    def probe_clients_for_url(
        url: str,
        *,
        browser_cookies_mode: str | None = None,
    ) -> tuple[str, ...]:
        return DownloadPolicy.probe_clients_for_url(
            url=url,
            browser_cookies_mode=browser_cookies_mode,
        )

    @staticmethod
    def _cookie_browser_candidates(opts: dict[str, Any]) -> tuple[str, ...]:
        raw = opts.get("cookiesfrombrowser")
        if isinstance(raw, (tuple, list)) and raw:
            requested_policy = DownloadPolicy.normalize_cookie_browser_policy(raw[0])
            return resolve_cookie_browser_candidates(requested_policy)
        return tuple()

    @staticmethod
    def _cookie_browser_requested(opts: dict[str, Any]) -> bool:
        raw = opts.get("cookiesfrombrowser")
        return bool(isinstance(raw, (tuple, list)) and raw)

    @staticmethod
    def _cookie_file_requested(opts: dict[str, Any]) -> bool:
        return bool(str(opts.get("cookiefile") or "").strip())

    @staticmethod
    def with_probe_client_opts(opts: dict[str, Any], *, probe_client: str) -> dict[str, Any]:
        normalized_probe_client = YtdlpGateway.normalize_probe_client(probe_client)
        if normalized_probe_client == "default":
            clean_opts = dict(opts or {})
            existing = clean_opts.get("extractor_args")
            if isinstance(existing, dict) and "youtube" in existing:
                updated_existing = dict(existing)
                youtube_args = updated_existing.get("youtube")
                if isinstance(youtube_args, dict):
                    updated_youtube_args = dict(youtube_args)
                    updated_youtube_args.pop("player_client", None)
                    if updated_youtube_args:
                        updated_existing["youtube"] = updated_youtube_args
                    else:
                        updated_existing.pop("youtube", None)
                    clean_opts["extractor_args"] = updated_existing if updated_existing else None
                    if not clean_opts.get("extractor_args"):
                        clean_opts.pop("extractor_args", None)
            return clean_opts

        updated_opts = dict(opts or {})
        extractor_args = updated_opts.get("extractor_args")
        normalized_extractor_args = dict(extractor_args) if isinstance(extractor_args, dict) else {}
        youtube_args = normalized_extractor_args.get("youtube")
        normalized_youtube_args = dict(youtube_args) if isinstance(youtube_args, dict) else {}
        normalized_youtube_args["player_client"] = [normalized_probe_client]
        normalized_extractor_args["youtube"] = normalized_youtube_args
        updated_opts["extractor_args"] = normalized_extractor_args
        return updated_opts

    @staticmethod
    def is_js_runtime_error(ex: Exception) -> bool:
        text = _normalize_ytdlp_detail(ex).lower()
        if isinstance(ex, FileNotFoundError):
            return True
        return any(marker in text for marker in _JS_RUNTIME_ERROR_MARKERS)

    @staticmethod
    def is_cookie_browser_error(ex: Exception) -> bool:
        text = _normalize_ytdlp_detail(ex).lower()
        return any(marker in text for marker in _COOKIE_BROWSER_ERROR_MARKERS)

    @staticmethod
    def is_cookie_browser_intervention_error(ex: Exception) -> bool:
        text = _normalize_ytdlp_detail(ex).lower()
        return any(marker in text for marker in _COOKIE_BROWSER_INTERVENTION_MARKERS)

    @staticmethod
    def is_auth_required_error(ex: Exception) -> bool:
        text = _normalize_ytdlp_detail(ex).lower()
        return any(marker in text for marker in _AUTH_REQUIRED_ERROR_MARKERS)

    @staticmethod
    def is_no_downloadable_formats_error(ex: Exception) -> bool:
        text = _normalize_ytdlp_detail(ex).lower()
        return any(marker in text for marker in _NO_DOWNLOADABLE_FORMAT_MARKERS)

    @staticmethod
    def classify_cookie_browser_error_kind(detail: Any) -> str:
        text = _normalize_ytdlp_detail(detail).lower()
        if any(marker in text for marker in _COOKIE_BROWSER_LOCKED_MARKERS):
            return "locked"
        if any(marker in text for marker in _COOKIE_BROWSER_DECRYPT_MARKERS):
            return "decrypt_failed"
        if any(marker in text for marker in _COOKIE_BROWSER_NOT_FOUND_MARKERS):
            return "not_found"
        return "browser_error"

    @staticmethod
    def _record_cookie_failure(diag: dict[str, Any], *, browser: str, detail: str) -> None:
        diag["cookie_browser_failures"].append(
            CookieBrowserAttempt(
                browser=browser,
                detail=_normalize_ytdlp_detail(detail),
                kind=YtdlpGateway.classify_cookie_browser_error_kind(detail),
            ).as_payload()
        )

    @staticmethod
    def _normalize_info(info: Any) -> dict[str, Any]:
        if not isinstance(info, dict):
            return {}
        normalized_info: dict[str, Any] = {}
        for key, value in info.items():
            normalized_info[str(key)] = value
        return normalized_info

    @staticmethod
    def _append_logger_event(diag: dict[str, Any], kind: str, text: str) -> None:
        normalized = _normalize_ytdlp_detail(text)
        if not normalized:
            return
        bucket = "raw_warning_messages" if kind == "warning" else "raw_error_messages"
        if kind in {"warning", "error"}:
            events = list(diag.get(bucket) or [])
            events.append(normalized)
            diag[bucket] = list(dict.fromkeys(events))

    @staticmethod
    def _attach_diagnostic_logger(opts: dict[str, Any], diag: dict[str, Any]) -> dict[str, Any]:
        updated = dict(opts or {})
        existing_logger = updated.get("logger")
        if isinstance(existing_logger, YtdlpLogger):
            logger = YtdlpLogger(
                existing_logger._logger,
                extra_noise=existing_logger._extra_noise,
                cancel_check=existing_logger._cancel_check,
                event_sink=lambda kind, text: YtdlpGateway._append_logger_event(diag, kind, text),
            )
        else:
            logger = YtdlpLogger(
                _LOG,
                event_sink=lambda kind, text: YtdlpGateway._append_logger_event(diag, kind, text),
            )
        updated["logger"] = logger
        return updated

    @staticmethod
    def _first_matching_message(messages: list[str], predicate: Callable[[Exception], bool]) -> str:
        for message in messages:
            error = RuntimeError(str(message or "").strip())
            if predicate(error):
                return _normalize_ytdlp_detail(message)
        return ""

    @staticmethod
    def _update_diag_flags_from_logger_messages(diag: dict[str, Any]) -> None:
        warnings = [
            str(item or "").strip()
            for item in list(diag.get("raw_warning_messages") or [])
            if str(item or "").strip()
        ]
        errors = [
            str(item or "").strip()
            for item in list(diag.get("raw_error_messages") or [])
            if str(item or "").strip()
        ]
        messages = warnings + errors
        if not messages:
            return

        if not diag.get("no_downloadable_formats"):
            detail = YtdlpGateway._first_matching_message(messages, YtdlpGateway.is_no_downloadable_formats_error)
            if detail:
                diag["no_downloadable_formats"] = True
                diag["no_downloadable_formats_detail"] = detail

        if not diag.get("authentication_required"):
            detail = YtdlpGateway._first_matching_message(messages, YtdlpGateway.is_auth_required_error)
            if detail:
                diag["authentication_required"] = True
                diag["authentication_error"] = detail

    @staticmethod
    def _extract_once(
        *,
        url: str,
        ydl_opts: dict[str, Any],
        download: bool,
        diag: dict[str, Any],
    ) -> dict[str, Any]:
        current_opts = YtdlpGateway._attach_diagnostic_logger(ydl_opts, diag)
        while True:
            try:
                with yt_dlp.YoutubeDL(current_opts) as ydl:
                    info = ydl.extract_info(url, download=download)
                normalized_info = YtdlpGateway._normalize_info(info)
                YtdlpGateway._update_diag_flags_from_logger_messages(diag)
                return normalized_info
            except _YTDLP_EXCEPTIONS as ex:
                if "js_runtimes" in current_opts and YtdlpGateway.is_js_runtime_error(ex):
                    diag["js_runtime_fallback"] = True
                    diag["js_runtime_error"] = _normalize_ytdlp_detail(ex)
                    _LOG.warning(
                        "yt_dlp JS runtime fallback activated. url=%s download=%s detail=%s",
                        sanitize_url_for_log(url),
                        bool(download),
                        _normalize_ytdlp_detail(ex),
                    )
                    current_opts = YtdlpGateway._attach_diagnostic_logger(
                        YtdlpGateway.without_js_runtime_opts(current_opts),
                        diag,
                    )
                    continue
                raise
        return {}

    @staticmethod
    def extract_info_with_fallback(
        *,
        url: str,
        ydl_opts: dict[str, Any],
        download: bool,
        allow_cookie_intervention: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        diag: dict[str, Any] = {
            "js_runtime_fallback": False,
            "js_runtime_error": "",
            "cookie_runtime_fallback": False,
            "cookie_runtime_error": "",
            "cookie_browser_used": "",
            "cookie_browser_attempts": [],
            "cookie_browser_failures": [],
            "cookie_intervention_required": False,
            "cookie_intervention_browser": "",
            "cookie_intervention_detail": "",
            "authentication_required": False,
            "authentication_error": "",
            "no_downloadable_formats": False,
            "no_downloadable_formats_detail": "",
            "raw_warning_messages": [],
            "raw_error_messages": [],
            "cookie_file_used": "",
        }
        base_opts = dict(ydl_opts or {})
        cookie_browsers = YtdlpGateway._cookie_browser_candidates(base_opts)
        cookie_browser_requested = YtdlpGateway._cookie_browser_requested(base_opts)
        cookie_file_requested = YtdlpGateway._cookie_file_requested(base_opts)
        cookie_file_path = str(base_opts.get("cookiefile") or "").strip()
        if cookie_file_requested:
            diag["cookie_file_used"] = cookie_file_path
        base_opts = YtdlpGateway.without_cookie_browser_opts(base_opts)
        last_cookie_error: Exception | None = None

        if cookie_browser_requested and not cookie_browsers:
            diag["cookie_runtime_fallback"] = True
            diag["cookie_runtime_error"] = "no usable browser cookie store detected"

        for browser in cookie_browsers:
            diag["cookie_browser_attempts"].append(browser)
            attempt_opts = YtdlpGateway.with_cookie_browser_opts(base_opts, browser)
            try:
                info = YtdlpGateway._extract_once(url=url, ydl_opts=attempt_opts, download=download, diag=diag)
            except _YTDLP_EXCEPTIONS as ex:
                detail = _normalize_ytdlp_detail(ex)
                if YtdlpGateway.is_cookie_browser_error(ex):
                    diag["cookie_runtime_fallback"] = True
                    diag["cookie_runtime_error"] = detail
                    YtdlpGateway._record_cookie_failure(diag, browser=browser, detail=detail)
                    if YtdlpGateway.is_cookie_browser_intervention_error(ex):
                        diag["cookie_intervention_required"] = True
                        diag["cookie_intervention_browser"] = browser
                        diag["cookie_intervention_detail"] = detail
                        if allow_cookie_intervention:
                            raise DownloadInterventionRequired(
                                CookieInterventionRequest(
                                    browser=browser,
                                    detail=detail,
                                    can_continue_without_cookies=True,
                                )
                            )
                    _LOG.warning(
                        "yt_dlp browser-cookie probe failed. url=%s download=%s browser=%s detail=%s",
                        sanitize_url_for_log(url),
                        bool(download),
                        browser,
                        detail,
                    )
                    last_cookie_error = ex
                    continue
                if YtdlpGateway.is_auth_required_error(ex):
                    diag["authentication_required"] = True
                    diag["authentication_error"] = detail
                    YtdlpGateway._record_cookie_failure(diag, browser=browser, detail=detail)
                    _LOG.warning(
                        "yt_dlp browser-cookie auth failed. url=%s download=%s browser=%s detail=%s",
                        sanitize_url_for_log(url),
                        bool(download),
                        browser,
                        detail,
                    )
                    last_cookie_error = ex
                    continue
                if YtdlpGateway.is_no_downloadable_formats_error(ex):
                    diag["no_downloadable_formats"] = True
                    diag["no_downloadable_formats_detail"] = detail
                raise
            diag["cookie_browser_used"] = browser
            return info, diag

        try:
            info = YtdlpGateway._extract_once(url=url, ydl_opts=base_opts, download=download, diag=diag)
            return info, diag
        except _YTDLP_EXCEPTIONS as ex:
            detail = _normalize_ytdlp_detail(ex)
            if YtdlpGateway.is_no_downloadable_formats_error(ex):
                diag["no_downloadable_formats"] = True
                diag["no_downloadable_formats_detail"] = detail
                raise DownloadError("error.down.no_downloadable_formats", detail=detail)
            if YtdlpGateway.is_auth_required_error(ex):
                diag["authentication_required"] = True
                diag["authentication_error"] = detail
                if cookie_browsers or cookie_browser_requested:
                    detail = diag["authentication_error"] or diag["cookie_runtime_error"] or detail
                    raise DownloadError("error.down.browser_cookies_unavailable", detail=detail)
                if cookie_file_requested:
                    raise DownloadError("error.down.authentication_required", detail=detail)
                raise DownloadError("error.down.authentication_required", detail=detail)
            if (
                (cookie_browsers or cookie_browser_requested)
                and last_cookie_error is not None
                and YtdlpGateway.is_cookie_browser_error(last_cookie_error)
            ):
                raise DownloadError(
                    "error.down.browser_cookies_unavailable",
                    detail=_normalize_ytdlp_detail(last_cookie_error),
                )
            raise

    @staticmethod
    def base_ydl_opts(
        *,
        url: str,
        quiet: bool,
        skip_download: bool,
        logger: YtdlpLogger | None = None,
        browser_cookies_mode_override: str | None = None,
        cookie_file_override: str | None = None,
    ) -> dict[str, Any]:
        max_bandwidth_kbps = AppConfig.network_max_bandwidth_kbps()
        concurrent_fragments = AppConfig.network_concurrent_fragments()
        opts: dict[str, Any] = {
            "quiet": bool(quiet),
            "skip_download": bool(skip_download),
            "logger": logger or YtdlpLogger(_LOG),
            "retries": AppConfig.network_retries(),
            "socket_timeout": AppConfig.network_http_timeout_s(),
            "noprogress": True,
        }
        if max_bandwidth_kbps:
            opts["ratelimit"] = int(max_bandwidth_kbps) * 1024
        if concurrent_fragments:
            opts["concurrent_fragment_downloads"] = int(concurrent_fragments)

        ffmpeg_dir = AppConfig.PATHS.FFMPEG_BIN_DIR
        if isinstance(ffmpeg_dir, Path) and ffmpeg_dir.exists():
            opts["ffmpeg_location"] = str(ffmpeg_dir)

        cookies_mode = str(browser_cookies_mode_override or "").strip().lower()
        if cookies_mode not in DownloadPolicy.COOKIE_BROWSER_MODES:
            cookies_mode = AppConfig.browser_cookies_mode()

        if cookies_mode == "from_browser":
            opts["cookiesfrombrowser"] = (AppConfig.browser_cookie_browser_policy(),)
        elif cookies_mode == "from_file":
            cookie_file_path = str(cookie_file_override or AppConfig.browser_cookie_file_path()).strip()
            if cookie_file_path:
                opts["cookiefile"] = cookie_file_path

        js_runtimes = YtdlpGateway.js_runtimes_for(url)
        if js_runtimes:
            opts["cachedir"] = False
            opts["js_runtimes"] = js_runtimes
            opts["remote_components"] = ["ejs:npm", "ejs:github"]
        return opts
