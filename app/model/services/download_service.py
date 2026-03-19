# app/model/services/download_service.py
from __future__ import annotations

import logging
import re
import shutil
import socket
import tempfile
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from app.controller.platform.logging import sanitize_url_for_log
from app.model.config.app_config import AppConfig as Config
from app.model.helpers.errors import AppError, OperationCancelled
from app.model.helpers.string_utils import sanitize_filename, normalize_lang_code, is_youtube_url

_LOG = logging.getLogger(__name__)

# ----- yt-dlp logger adapter -----

_NOISE_PATTERNS: tuple[str, ...] = (
    "UNPLAYABLE formats",
    "developer option intended for debugging",
    "impersonation",
    "SABR streaming",
    "SABR-only",
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

def _is_noisy(msg: str, extra_noise: tuple[str, ...] = ()) -> bool:
    text = str(msg)
    for k in _NOISE_PATTERNS:
        if k in text:
            return True
    for k in extra_noise:
        if k and str(k) in text:
            return True
    return False


class YtdlpLogger:
    """Minimal logger adapter for yt_dlp."""

    def __init__(self, logger: logging.Logger, *, extra_noise: tuple[str, ...] = ()) -> None:
        self._logger = logger
        self._extra_noise = tuple(extra_noise or ())

    def debug(self, msg) -> None:
        text = str(msg)
        if self._logger.isEnabledFor(logging.DEBUG) and not _is_noisy(text, self._extra_noise):
            self._logger.debug("yt_dlp debug message. text=%s", text)

    def info(self, msg) -> None:
        text = str(msg)
        if not _is_noisy(text, self._extra_noise):
            self._logger.info("yt_dlp info message. text=%s", text)

    def warning(self, msg) -> None:
        text = str(msg)
        if not _is_noisy(text, self._extra_noise):
            self._logger.warning("yt_dlp warning message. text=%s", text)

    def error(self, msg) -> None:
        text = str(msg)
        if not _is_noisy(text, self._extra_noise):
            self._logger.error("yt_dlp error message. text=%s", text)


class DownloadError(AppError):
    """Error with i18n key and params to be localized by UI."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(str(key), dict(params or {}))


class DownloadService:
    """Thin wrapper over yt_dlp with simple probe + download API."""

    # ----- Network / probe helpers -----

    @staticmethod
    def _classify_network_error(ex: Exception) -> str:
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
    def _log_network_error(*, action: str, url: str, ex: Exception, error_key: str) -> None:
        _LOG.debug(
            "Download network error classified. action=%s url=%s key=%s detail=%s",
            action,
            sanitize_url_for_log(url),
            error_key,
            str(ex),
        )

    @staticmethod
    def _pick_thumbnail_url(info: dict[str, Any]) -> str:
        direct = info.get("thumbnail")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        thumbs = info.get("thumbnails") or []
        if isinstance(thumbs, list):
            for t in reversed(thumbs):
                if isinstance(t, dict):
                    u = t.get("url")
                    if isinstance(u, str) and u.strip():
                        return u.strip()
        return ""

    @staticmethod
    def _collect_audio_tracks(info: dict[str, Any]) -> list[dict[str, Any]]:
        formats = info.get("formats") or []
        by_lang: dict[str, dict[str, Any]] = {}

        def _from_text(val: Any) -> str | None:
            if not isinstance(val, str) or not val:
                return None
            m = re.search(r"\[([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)]", val)
            if not m:
                return None
            return m.group(1)

        for f in formats:
            if not isinstance(f, dict):
                continue
            acodec = f.get("acodec")
            if acodec in (None, "none"):
                continue

            raw_lang = None
            audio_track = f.get("audio_track")
            if isinstance(audio_track, dict):
                raw_lang = (
                    audio_track.get("lang_code")
                    or audio_track.get("language")
                    or audio_track.get("lang")
                    or audio_track.get("id")
                )

            if not raw_lang:
                raw_lang = f.get("language") or f.get("lang") or f.get("audio_lang")

            if not raw_lang:
                raw_lang = _from_text(f.get("format_note")) or _from_text(f.get("format"))

            lang = normalize_lang_code(raw_lang, drop_region=False)
            if not lang:
                continue

            bitrate = f.get("abr") or f.get("tbr") or 0
            cur = by_lang.get(lang)
            if not cur or bitrate > cur.get("bitrate", 0):
                by_lang[lang] = {"lang_code": lang, "bitrate": bitrate}

        if by_lang:
            return list(by_lang.values())

        return []

    @staticmethod
    def _js_runtimes_for(url: str) -> dict[str, Any] | None:
        if not is_youtube_url(url):
            return None

        deno_bin = Config.DENO_BIN
        if isinstance(deno_bin, Path) and deno_bin.exists():
            return {"deno": {"path": str(deno_bin)}}

        return None

    @staticmethod
    def _without_js_runtime_opts(opts: dict[str, Any]) -> dict[str, Any]:
        clean = dict(opts or {})
        clean.pop("js_runtimes", None)
        clean.pop("remote_components", None)
        return clean

    @staticmethod
    def _is_js_runtime_error(ex: Exception) -> bool:
        text = str(ex or "").strip().lower()
        if isinstance(ex, FileNotFoundError):
            return True
        return any(marker in text for marker in _JS_RUNTIME_ERROR_MARKERS)

    @staticmethod
    def _make_probe_diag(
        *,
        info: dict[str, Any] | None,
        audio_tracks: list[dict[str, Any]],
        js_runtime_fallback: bool,
        js_runtime_detail: str,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        details: dict[str, Any] = {}

        formats = DownloadService._formats(info)
        audio_formats = [fmt for fmt in formats if DownloadService._has_audio(fmt)]
        unresolved_audio = 0
        for fmt in audio_formats:
            raw_lang = None
            audio_track = fmt.get("audio_track")
            if isinstance(audio_track, dict):
                raw_lang = (
                    audio_track.get("lang_code")
                    or audio_track.get("language")
                    or audio_track.get("lang")
                    or audio_track.get("id")
                )
            if not raw_lang:
                raw_lang = fmt.get("language") or fmt.get("lang") or fmt.get("audio_lang")
            if not normalize_lang_code(raw_lang, drop_region=False):
                unresolved_audio += 1

        if js_runtime_fallback:
            warnings.append("runtime_fallback")
            if js_runtime_detail:
                details["runtime_fallback_detail"] = js_runtime_detail

        if unresolved_audio > 0:
            warnings.append("audio_metadata_partial")
            details["unresolved_audio_formats"] = unresolved_audio

        if js_runtime_fallback and audio_formats and len(audio_tracks) <= 1:
            warnings.append("audio_tracks_incomplete")

        if not warnings and not errors:
            return {}

        details.setdefault("audio_format_count", len(audio_formats))
        details.setdefault("audio_track_count", len(audio_tracks))
        return {
            "warnings": warnings,
            "errors": errors,
            "details": details,
        }

    @staticmethod
    def _extract_info_with_fallback(
        *,
        url: str,
        ydl_opts: dict[str, Any],
        download: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        diag = {
            "js_runtime_fallback": False,
            "js_runtime_error": "",
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=download), diag
        except Exception as ex:
            if "js_runtimes" not in ydl_opts:
                raise
            if not DownloadService._is_js_runtime_error(ex):
                raise

            diag["js_runtime_fallback"] = True
            diag["js_runtime_error"] = str(ex)
            _LOG.warning(
                "yt_dlp JS runtime fallback activated. url=%s download=%s detail=%s",
                sanitize_url_for_log(url),
                bool(download),
                str(ex),
            )

        fallback_opts = DownloadService._without_js_runtime_opts(ydl_opts)
        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
            return ydl.extract_info(url, download=download), diag

    @staticmethod
    # ----- Format / selector helpers -----

    def _parse_video_quality_height(quality: str) -> int | None:
        q = str(quality or "").strip().lower()
        if not q or q == "auto":
            return None

        m = re.fullmatch(r"(\d{3,4})p?", q)
        if not m:
            return None

        try:
            value = int(m.group(1))
        except ValueError:
            return None

        return value if value > 0 else None

    @staticmethod
    def _height_filter(*, min_h: int | None = None, max_h: int | None = None) -> str:
        parts: list[str] = []

        if isinstance(min_h, int) and min_h > 0:
            parts.append(f"[height>={min_h}]")

        if isinstance(max_h, int) and max_h > 0:
            parts.append(f"[height<={max_h}]")

        return "".join(parts)

    @staticmethod
    def _video_format_selector(*, min_h: int | None, max_h: int | None, target_h: int | None, lang_base: str) -> str:
        if isinstance(target_h, int) and target_h > 0:
            video_filter = DownloadService._height_filter(max_h=target_h)
            video_sel = f"bv*{video_filter}"
            merged_sel = f"b{video_filter}"
        else:
            video_filter = DownloadService._height_filter(min_h=min_h, max_h=max_h)
            video_sel = f"bv*{video_filter}" if video_filter else "bv*"
            merged_sel = "b"

        if lang_base:
            return f"{video_sel}+ba[language^={lang_base}]/{video_sel}+ba/{merged_sel}"

        return f"{video_sel}+ba/{merged_sel}"

    @staticmethod
    def _has_audio(fmt: dict[str, Any]) -> bool:
        return fmt.get("acodec") not in (None, "none")

    @staticmethod
    def _has_video(fmt: dict[str, Any]) -> bool:
        return fmt.get("vcodec") not in (None, "none")

    @staticmethod
    def _formats(info: dict[str, Any] | None) -> list[dict[str, Any]]:
        raw = [] if not isinstance(info, dict) else list(info.get("formats") or [])
        return [f for f in raw if isinstance(f, dict)]

    @staticmethod
    def _has_audio_only_ext(info: dict[str, Any] | None, *exts: str) -> bool:
        wanted = {str(x or '').strip().lower() for x in exts if str(x or '').strip()}
        if not wanted:
            return False
        for fmt in DownloadService._formats(info):
            if not DownloadService._has_audio(fmt) or DownloadService._has_video(fmt):
                continue
            if str(fmt.get("ext") or "").strip().lower() in wanted:
                return True
        return False

    @staticmethod
    def _has_combined_ext(info: dict[str, Any] | None, ext: str) -> bool:
        wanted = str(ext or '').strip().lower()
        if not wanted:
            return False
        for fmt in DownloadService._formats(info):
            if not (DownloadService._has_audio(fmt) and DownloadService._has_video(fmt)):
                continue
            if str(fmt.get("ext") or "").strip().lower() == wanted:
                return True
        return False

    @staticmethod
    def _has_video_only_ext(info: dict[str, Any] | None, ext: str) -> bool:
        wanted = str(ext or '').strip().lower()
        if not wanted:
            return False
        for fmt in DownloadService._formats(info):
            if not DownloadService._has_video(fmt) or DownloadService._has_audio(fmt):
                continue
            if str(fmt.get("ext") or "").strip().lower() == wanted:
                return True
        return False

    @staticmethod
    def _audio_selector(*, lang_base: str, exts: tuple[str, ...] = ()) -> str:
        selectors: list[str] = []
        if exts:
            for ext in exts:
                ext_l = str(ext or '').strip().lower()
                if not ext_l:
                    continue
                if lang_base:
                    selectors.append(f"ba[ext={ext_l}][language^={lang_base}]")
                selectors.append(f"ba[ext={ext_l}]")
        if lang_base:
            selectors.append(f"ba[language^={lang_base}]")
        selectors.extend(str(Config.DOWNLOAD_FALLBACK_AUDIO_SELECTOR or "").split("/"))
        return "/".join(dict.fromkeys(selectors))

    @staticmethod
    def _video_target_selector(
        *,
        min_h: int | None,
        max_h: int | None,
        target_h: int | None,
        target_ext: str,
        lang_base: str,
        audio_exts: tuple[str, ...] = (),
    ) -> str:
        target_ext_l = str(target_ext or '').strip().lower()
        audio_sel = DownloadService._audio_selector(lang_base=lang_base, exts=tuple(audio_exts or ()))
        selectors: list[str] = []

        def _append(video_filter: str) -> None:
            video_sel = f"bv*[ext={target_ext_l}]{video_filter}"
            combined_sel = f"b[ext={target_ext_l}]{video_filter}"
            selectors.append(f"{video_sel}+{audio_sel}")
            selectors.append(combined_sel)

        if isinstance(target_h, int) and target_h > 0:
            _append(f"[height={target_h}]")
            _append(DownloadService._height_filter(max_h=target_h))
        else:
            _append(DownloadService._height_filter(min_h=min_h, max_h=max_h))

        return "/".join(dict.fromkeys(selectors))

    @staticmethod
    def _build_audio_plan(
        *,
        info: dict[str, Any] | None,
        quality: str,
        ext_l: str,
        lang_base: str,
        purpose: str,
        keep_output: bool,
    ) -> dict[str, Any]:
        profile = Config.download_audio_format_profile(ext_l)
        selector_exts = tuple(profile.get("selector_exts") or ())
        preferred_codec = str(profile.get("preferredcodec") or ext_l or "").strip().lower()

        plan: dict[str, Any] = {
            "format": DownloadService._audio_selector(lang_base=lang_base),
            "format_sort": ["acodec", "abr:desc", "tbr:desc"],
            "postprocessors": [],
            "merge_output_format": None,
        }

        if purpose == Config.DOWNLOAD_PURPOSE_TRANSCRIPTION and not keep_output:
            return plan

        if selector_exts and DownloadService._has_audio_only_ext(info, *selector_exts):
            plan["format"] = DownloadService._audio_selector(lang_base=lang_base, exts=selector_exts)
            return plan

        if preferred_codec and ext_l and ext_l not in {"", "auto"}:
            plan["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": preferred_codec,
                "preferredquality": str(quality or ""),
            }]
        return plan

    @staticmethod
    def _build_video_plan(
        *,
        info: dict[str, Any] | None,
        quality: str,
        ext_l: str,
        lang_base: str,
        purpose: str,
        keep_output: bool,
        min_h: int | None,
        max_h: int | None,
    ) -> dict[str, Any]:
        target_h = DownloadService._parse_video_quality_height(quality)
        profile = Config.download_video_format_profile(ext_l)
        video_exts = tuple(profile.get("video_exts") or ())
        audio_exts = tuple(profile.get("audio_exts") or ())
        strategy = str(profile.get("strategy") or "").strip().lower()
        strict_final_ext = bool(profile.get("strict_final_ext"))

        plan: dict[str, Any] = {
            "format": DownloadService._video_format_selector(
                min_h=min_h,
                max_h=max_h,
                target_h=target_h,
                lang_base=lang_base,
            ),
            "format_sort": [f"height:{target_h}"] if target_h else [],
            "postprocessors": [],
            "merge_output_format": None,
        }

        if purpose == Config.DOWNLOAD_PURPOSE_TRANSCRIPTION and not keep_output:
            return plan

        if not ext_l or ext_l in {"", "auto"}:
            return plan

        target_video_ext = str((video_exts or (ext_l,))[0] or ext_l).strip().lower()
        direct_combined = DownloadService._has_combined_ext(info, ext_l)
        direct_video = any(
            DownloadService._has_video_only_ext(info, candidate_ext)
            for candidate_ext in (video_exts or (ext_l,))
        )
        has_audio_family = DownloadService._has_audio_only_ext(info, *audio_exts) if audio_exts else False

        if strategy in {"native_or_merge", "native_or_merge_or_convert"}:
            if direct_combined or (direct_video and has_audio_family):
                plan["format"] = DownloadService._video_target_selector(
                    min_h=min_h,
                    max_h=max_h,
                    target_h=target_h,
                    target_ext=target_video_ext,
                    lang_base=lang_base,
                    audio_exts=audio_exts,
                )
                if direct_video and has_audio_family:
                    plan["merge_output_format"] = ext_l
                return plan

            if strategy == "native_or_merge_or_convert" or strict_final_ext:
                plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
            return plan

        if strategy == "remux":
            plan["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": ext_l}]
            return plan

        if strategy == "convert":
            plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
            return plan

        plan["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": ext_l}]
        return plan

    @staticmethod
    # ----- Stage / artifact helpers -----

    def _create_download_stage(*, stem: str) -> Path:
        root = Config.DOWNLOADS_TMP_DIR
        root.mkdir(parents=True, exist_ok=True)
        prefix = f"{sanitize_filename(stem) or Config.DOWNLOAD_DEFAULT_STEM}_"
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))

    @staticmethod
    def _build_stage_outtmpl(*, stage_dir: Path, stem: str) -> str:
        return str(stage_dir / f"{sanitize_filename(stem) or Config.DOWNLOAD_DEFAULT_STEM}.%(ext)s")

    @staticmethod
    def _normalize_ext(value: Any) -> str:
        return str(value or '').strip().lower().lstrip('.')

    @staticmethod
    def _is_partial_artifact(path: Path) -> bool:
        name_l = path.name.lower()
        if name_l.endswith((".part", ".ytdl", ".temp")):
            return True
        if ".part-" in name_l or name_l.endswith(".frag"):
            return True
        return False

    @staticmethod
    def _stage_files(stage_dir: Path) -> list[Path]:
        try:
            matches = [
                p for p in stage_dir.iterdir()
                if p.is_file() and not DownloadService._is_partial_artifact(p)
            ]
        except Exception:
            return []

        return sorted(
            matches,
            key=lambda p: (p.stat().st_mtime_ns if p.exists() else 0, p.stat().st_size if p.exists() else 0),
            reverse=True,
        )

    @staticmethod
    def _candidate_paths_from_info(info: dict[str, Any]) -> list[Path]:
        candidates: list[Path] = []

        def _add(value: Any) -> None:
            if not value:
                return
            try:
                p = Path(value)
            except Exception:
                return
            if p.exists() and p.is_file() and not DownloadService._is_partial_artifact(p):
                candidates.append(p)

        _add(info.get("filepath"))
        _add(info.get("_filename"))

        return list(dict.fromkeys(candidates))

    @staticmethod
    def _requested_component_paths(info: dict[str, Any], stage_dir: Path) -> list[Path]:
        paths: list[Path] = []
        req = info.get("requested_downloads") or []
        if not isinstance(req, list):
            return paths

        for item in req:
            if not isinstance(item, dict):
                continue
            for key in ("filepath", "_filename"):
                value = item.get(key)
                if not value:
                    continue
                try:
                    p = Path(value)
                except Exception:
                    continue
                if stage_dir in p.parents and p.exists() and p.is_file() and not DownloadService._is_partial_artifact(p):
                    paths.append(p)

        return list(dict.fromkeys(paths))

    @staticmethod
    def _select_matching_ext(paths: list[Path], ext_l: str) -> Path | None:
        if not ext_l:
            return None
        for path in paths:
            if DownloadService._normalize_ext(path.suffix) == ext_l:
                return path
        return None

    @staticmethod
    def _resolve_stage_artifact(
        *,
        info: dict[str, Any],
        stage_dir: Path,
        stem: str,
        requested_ext: str,
        artifact_policy: str,
    ) -> Path | None:
        requested_ext_l = DownloadService._normalize_ext(requested_ext)
        info_ext_l = DownloadService._normalize_ext(info.get("ext"))
        safe_stem = sanitize_filename(stem) or Config.DOWNLOAD_DEFAULT_STEM

        stage_files = DownloadService._stage_files(stage_dir)
        if not stage_files:
            return None

        component_paths = set(DownloadService._requested_component_paths(info, stage_dir))
        info_candidates = [
            p for p in DownloadService._candidate_paths_from_info(info)
            if stage_dir in p.parents and p in stage_files
        ]
        exact_stem = [p for p in stage_files if p.stem == safe_stem]

        def _prefer_non_component(paths: list[Path]) -> list[Path]:
            preferred = [p for p in paths if p not in component_paths]
            return preferred or list(paths)

        info_preferred = _prefer_non_component(info_candidates)
        exact_preferred = _prefer_non_component(exact_stem)
        stage_preferred = _prefer_non_component(stage_files)

        for pool in (info_preferred, exact_preferred, stage_preferred):
            pick = DownloadService._select_matching_ext(pool, requested_ext_l)
            if pick is not None:
                return pick

        for pool in (info_preferred, exact_preferred, stage_preferred):
            pick = DownloadService._select_matching_ext(pool, info_ext_l)
            if pick is not None:
                return pick

        if artifact_policy == Config.DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT:
            if len(info_preferred) == 1:
                return info_preferred[0]
            if len(exact_preferred) == 1:
                return exact_preferred[0]
            if len(stage_preferred) == 1:
                return stage_preferred[0]

        if len(info_candidates) == 1:
            return info_candidates[0]

        if len(exact_stem) == 1:
            return exact_stem[0]

        if len(stage_files) == 1:
            return stage_files[0]

        return None

    @staticmethod
    def _unique_destination_path(dst: Path) -> Path:
        if not dst.exists():
            return dst

        stem = dst.stem
        suffix = dst.suffix
        parent = dst.parent
        idx = 2
        while True:
            candidate = parent / f"{stem} ({idx}){suffix}"
            if not candidate.exists():
                return candidate
            idx += 1

    @staticmethod
    def _promote_stage_artifact(
        *,
        artifact: Path,
        final_dir: Path,
        stem: str,
        requested_ext: str,
    ) -> Path:
        final_dir.mkdir(parents=True, exist_ok=True)

        requested_ext_l = DownloadService._normalize_ext(requested_ext)
        artifact_ext_l = DownloadService._normalize_ext(artifact.suffix)
        if requested_ext_l and artifact_ext_l and requested_ext_l != artifact_ext_l:
            raise DownloadError(
                "error.down.download_failed",
                detail=f"staged artifact ext mismatch: expected {requested_ext_l}, got {artifact_ext_l}",
            )

        suffix = artifact.suffix or (f".{requested_ext_l}" if requested_ext_l else "")
        dst = final_dir / f"{sanitize_filename(stem) or artifact.stem}{suffix}"
        dst = DownloadService._unique_destination_path(dst)
        shutil.move(str(artifact), str(dst))
        return dst

    @staticmethod
    def _cleanup_stage_dir(stage_dir: Path | None) -> None:
        if not stage_dir:
            return
        shutil.rmtree(stage_dir, ignore_errors=True)

    @staticmethod
    # ----- yt-dlp option builders -----

    def _base_ydl_opts(*, url: str, quiet: bool, skip_download: bool) -> dict[str, Any]:
        max_bandwidth_kbps = Config.network_max_bandwidth_kbps()
        concurrent_fragments = Config.network_concurrent_fragments()
        opts: dict[str, Any] = {
            "quiet": bool(quiet),
            "skip_download": bool(skip_download),
            "nocheckcertificate": bool(Config.network_no_check_certificate()),
            "logger": YtdlpLogger(_LOG),
            "retries": Config.network_retries(),
            "socket_timeout": Config.network_http_timeout_s(),
            "noprogress": True,
        }
        if max_bandwidth_kbps:
            opts["ratelimit"] = int(max_bandwidth_kbps) * 1024
        if concurrent_fragments:
            opts["concurrent_fragment_downloads"] = int(concurrent_fragments)

        ffmpeg_dir = Config.FFMPEG_BIN_DIR
        if isinstance(ffmpeg_dir, Path) and ffmpeg_dir.exists():
            opts["ffmpeg_location"] = str(ffmpeg_dir)

        jsr = DownloadService._js_runtimes_for(url)
        if jsr:
            opts["cachedir"] = False
            opts["js_runtimes"] = jsr
            opts["remote_components"] = ["ejs:npm", "ejs:github"]
        return opts

    # ----- Public API: probe -----

    def probe(self, url: str) -> dict[str, Any]:
        safe_url = sanitize_url_for_log(url)
        ydl_opts: dict[str, Any] = self._base_ydl_opts(url=url, quiet=not _LOG.isEnabledFor(logging.DEBUG), skip_download=True)
        _LOG.debug("Download probe started. url=%s quiet=%s", safe_url, bool(ydl_opts.get("quiet", False)))

        try:
            info, probe_runtime = self._extract_info_with_fallback(url=url, ydl_opts=ydl_opts, download=False)

            audio_tracks = self._collect_audio_tracks(info)
            probe_diag = self._make_probe_diag(
                info=info,
                audio_tracks=audio_tracks,
                js_runtime_fallback=bool(probe_runtime.get("js_runtime_fallback")),
                js_runtime_detail=str(probe_runtime.get("js_runtime_error") or ""),
            )
            webpage_url = info.get("webpage_url") or info.get("original_url") or url
            result = {
                "id": info.get("id") or info.get("display_id") or "",
                "title": info.get("title"),
                "duration": info.get("duration"),
                "filesize": info.get("filesize") or info.get("filesize_approx"),
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "webpage_url": webpage_url,
                "uploader": info.get("uploader") or info.get("channel") or "",
                "uploader_id": info.get("uploader_id") or info.get("channel_id") or "",
                "uploader_url": info.get("uploader_url") or info.get("channel_url") or "",
                "upload_date": info.get("upload_date") or "",
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "tags": info.get("tags") or [],
                "categories": info.get("categories") or [],
                "description": info.get("description") or "",
                "thumbnail_url": self._pick_thumbnail_url(info),
                "formats": info.get("formats") or [],
                "audio_tracks": audio_tracks,
                "probe_diag": probe_diag,
            }
            if probe_diag:
                _LOG.info(
                    "Download probe diagnostics. url=%s warnings=%s errors=%s details=%s",
                    safe_url,
                    probe_diag.get("warnings") or [],
                    probe_diag.get("errors") or [],
                    probe_diag.get("details") or {},
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
            network_key = self._classify_network_error(ex)
            if network_key:
                self._log_network_error(action="probe", url=url, ex=ex, error_key=network_key)
                raise DownloadError(network_key)
            _LOG.debug("Download probe failed. url=%s detail=%s", safe_url, str(ex))
            raise DownloadError("error.down.probe_failed", detail=str(ex))

    # ----- Public API: download -----

    def download(
        self,
        *,
        url: str,
        kind: str,
        quality: str,
        ext: str,
        out_dir: Path,
        progress_cb: Callable[[int, str], None] | None = None,
        audio_lang: str | None = None,
        file_stem: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        purpose: str = Config.DOWNLOAD_DEFAULT_PURPOSE,
        keep_output: bool = True,
        meta: dict[str, Any] | None = None,
    ) -> Path | None:

        min_h = Config.downloader_min_video_height()
        max_h = Config.downloader_max_video_height()
        ext_l = (ext or "").lower().strip().lstrip(".")
        purpose_l = str(purpose or Config.DOWNLOAD_DEFAULT_PURPOSE).strip().lower()
        contract = Config.resolve_download_contract(
            kind=kind,
            purpose=purpose_l,
            keep_output=bool(keep_output),
            ext=ext_l,
        )
        plan_ext = str(contract.get("plan_ext") or "").strip().lower()
        final_ext = str(contract.get("final_ext") or "").strip().lower()
        artifact_policy = str(contract.get("artifact_policy") or Config.DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT).strip().lower()

        if Config.is_download_audio_auto_value(audio_lang):
            audio_lang = None
        audio_lang_norm = normalize_lang_code(audio_lang, drop_region=False) if audio_lang else None
        lang_base = (audio_lang_norm.split("-")[0] or "").lower() if audio_lang_norm else ""

        if meta is None:
            try:
                meta = self.probe(url)
            except Exception:
                meta = None

        if kind == "audio":
            plan = self._build_audio_plan(
                info=meta,
                quality=quality,
                ext_l=plan_ext,
                lang_base=lang_base,
                purpose=purpose_l,
                keep_output=bool(keep_output),
            )
        else:
            plan = self._build_video_plan(
                info=meta,
                quality=quality,
                ext_l=plan_ext,
                lang_base=lang_base,
                purpose=purpose_l,
                keep_output=bool(keep_output),
                min_h=min_h,
                max_h=max_h,
            )

        def _emit_progress(pct: int, status: str) -> None:
            if not progress_cb:
                return
            try:
                progress_cb(int(max(0, min(100, int(pct)))), str(status or ""))
            except Exception:
                return

        def _download_pct(d: dict[str, Any]) -> int:
            raw_pct = str(d.get("_percent_str") or "").strip().replace("%", "")
            if raw_pct:
                try:
                    return int(max(0.0, min(100.0, float(raw_pct))))
                except Exception:
                    pass

            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            try:
                if total:
                    return int(max(0.0, min(100.0, (float(downloaded) / float(total)) * 100.0)))
            except Exception:
                pass
            return 0

        def _hook(d: dict[str, Any]) -> None:
            if cancel_check and cancel_check():
                raise OperationCancelled()

            status = str(d.get("status") or "").strip().lower()
            if status == "downloading":
                _emit_progress(_download_pct(d), "downloading")
                return
            if status == "finished":
                _emit_progress(100, "downloaded")

        def _post_hook(d: dict[str, Any]) -> None:
            if cancel_check and cancel_check():
                raise OperationCancelled()

            status = str(d.get("status") or "").strip().lower()
            if status == "started":
                _emit_progress(100, "postprocessing")
                return
            if status == "finished":
                _emit_progress(100, "postprocessed")

        stem = sanitize_filename(file_stem or "%(title)s") or Config.DOWNLOAD_DEFAULT_STEM
        stage_dir = self._create_download_stage(stem=stem)
        outtmpl = self._build_stage_outtmpl(stage_dir=stage_dir, stem=stem)

        ydl_opts: dict[str, Any] = self._base_ydl_opts(url=url, quiet=not _LOG.isEnabledFor(logging.DEBUG), skip_download=False)
        ydl_opts.update({
            "format": plan.get("format") or (Config.DOWNLOAD_FALLBACK_AUDIO_SELECTOR if kind == "audio" else Config.DOWNLOAD_FALLBACK_VIDEO_SELECTOR),
            "outtmpl": outtmpl,
            "progress_hooks": [_hook],
            "postprocessor_hooks": [_post_hook],
            "postprocessors": list(plan.get("postprocessors") or []),
        })
        format_sort = list(plan.get("format_sort") or [])
        if format_sort:
            ydl_opts["format_sort"] = format_sort
        merge_output_format = str(plan.get("merge_output_format") or "").strip().lower()
        if merge_output_format:
            ydl_opts["merge_output_format"] = merge_output_format

        _LOG.debug(
            "Download started. url=%s kind=%s quality=%s ext=%s audio_lang=%s purpose=%s keep_output=%s final_out_dir=%s stage_dir=%s stem=%s plan=%s",
            sanitize_url_for_log(url),
            kind,
            quality,
            ext_l,
            audio_lang_norm or "",
            purpose_l,
            bool(keep_output),
            out_dir,
            stage_dir,
            stem,
            {
                "format": ydl_opts.get("format"),
                "format_sort": format_sort,
                "merge_output_format": merge_output_format,
                "postprocessors": ydl_opts.get("postprocessors"),
                "plan_ext": plan_ext,
                "final_ext": final_ext,
                "artifact_policy": artifact_policy,
            },
        )

        info: dict[str, Any] | None = None
        try:
            info, download_runtime = self._extract_info_with_fallback(url=url, ydl_opts=ydl_opts, download=True)
            if download_runtime.get("js_runtime_fallback"):
                _LOG.info(
                    "Download continued after JS runtime fallback. url=%s detail=%s",
                    sanitize_url_for_log(url),
                    str(download_runtime.get("js_runtime_error") or ""),
                )
            stage_files = self._stage_files(stage_dir)
            _LOG.debug(
                "Download postprocess state. url=%s requested_ext=%s info_ext=%s info_filepath=%s stage_dir=%s stage_files=%s",
                sanitize_url_for_log(url),
                ext_l,
                self._normalize_ext((info or {}).get("ext")),
                str((info or {}).get("filepath") or (info or {}).get("_filename") or ""),
                str(stage_dir),
                [p.name for p in stage_files],
            )

            artifact = self._resolve_stage_artifact(
                info=info,
                stage_dir=stage_dir,
                stem=stem,
                requested_ext=final_ext,
                artifact_policy=artifact_policy,
            )
            if artifact is None:
                _LOG.warning(
                    "Download finished without stage artifact. url=%s requested_ext=%s final_ext=%s artifact_policy=%s info_ext=%s stage_dir=%s stage_files=%s",
                    sanitize_url_for_log(url),
                    ext_l,
                    final_ext,
                    artifact_policy,
                    self._normalize_ext((info or {}).get("ext")),
                    str(stage_dir),
                    [p.name for p in stage_files],
                )
                self._cleanup_stage_dir(stage_dir)
                raise DownloadError(
                    "error.down.download_failed",
                    detail="download finished without a final stage artifact",
                )

            should_promote = purpose_l == Config.DOWNLOAD_PURPOSE_DOWNLOAD or bool(keep_output)
            if should_promote:
                promoted = self._promote_stage_artifact(
                    artifact=artifact,
                    final_dir=out_dir,
                    stem=stem,
                    requested_ext=final_ext,
                )
                self._cleanup_stage_dir(stage_dir)
                _LOG.info(
                    "Download finished. url=%s requested_ext=%s final_ext=%s artifact_policy=%s resolved_artifact=%s promoted=%s",
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
            stage_files = self._stage_files(stage_dir)
            self._cleanup_stage_dir(stage_dir)
            _LOG.debug(
                "Download cancelled. url=%s stage_dir=%s stage_files=%s",
                sanitize_url_for_log(url),
                str(stage_dir),
                [p.name for p in stage_files],
            )
            raise
        except Exception as ex:
            stage_files = self._stage_files(stage_dir)
            self._cleanup_stage_dir(stage_dir)
            network_key = self._classify_network_error(ex)
            if network_key:
                self._log_network_error(action="download", url=url, ex=ex, error_key=network_key)
                raise DownloadError(network_key)
            _LOG.debug(
                "Download failed. url=%s requested_ext=%s final_ext=%s artifact_policy=%s info_ext=%s info_filepath=%s stage_dir=%s stage_files=%s detail=%s",
                sanitize_url_for_log(url),
                ext_l,
                final_ext,
                artifact_policy,
                self._normalize_ext((info or {}).get("ext")),
                str((info or {}).get("filepath") or (info or {}).get("_filename") or ""),
                str(stage_dir),
                [p.name for p in stage_files],
                str(ex),
            )
            raise DownloadError("error.down.download_failed", detail=str(ex))
