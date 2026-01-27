# core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any, List

import yt_dlp

from core.config.app_config import AppConfig as Config
from core.io.text import sanitize_filename


# ----- yt-dlp logger adapter -----

_NOISE_PATTERNS: tuple[str, ...] = (
    "UNPLAYABLE formats",
    "developer option intended for debugging",
    "impersonation",
    "SABR streaming",
    "SABR-only",
    "[debug]",
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
    """Minimal logger adapter for yt_dlp.

    yt_dlp expects methods: debug/info/warning/error.
    We forward messages into a provided callable with simple noise filtering.
    """

    def __init__(self, log_fn, *, extra_noise: tuple[str, ...] = ()) -> None:
        self._log = log_fn
        self._extra_noise = tuple(extra_noise or ())

    def debug(self, msg) -> None:
        # yt_dlp debug is extremely verbose; ignore.
        return

    def info(self, msg) -> None:
        if not _is_noisy(str(msg), self._extra_noise):
            self._log(str(msg))

    def warning(self, msg) -> None:
        if not _is_noisy(str(msg), self._extra_noise):
            self._log(str(msg))

    def error(self, msg) -> None:
        if not _is_noisy(str(msg), self._extra_noise):
            self._log(str(msg))


class DownloadError(RuntimeError):
    """Error with i18n key and params to be localized by UI."""

    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


class DownloadCancelled(RuntimeError):
    """Raised to cooperatively stop an in-progress download."""
    pass


class DownloadService:
    """Thin wrapper over yt_dlp with simple probe + download API."""

    def __init__(self) -> None:
        pass

    # ----- Helpers -----

    @staticmethod
    def _normalize_lang_code(code: str | None) -> str | None:
        """
        Normalize language codes into a simple BCP-47-like form:
        - replace '_' with '-'
        - language part lower-case
        - 2-letter region upper-case
        Works for 'en', 'en-us', 'EN_us', 'pl-PL', etc.
        """
        if not code:
            return None
        code = str(code).strip()
        if not code:
            return None

        code = code.replace("_", "-")
        parts = [p for p in code.split("-") if p]
        if not parts:
            return None

        parts[0] = parts[0].lower()
        for i in range(1, len(parts)):
            if len(parts[i]) == 2:
                parts[i] = parts[i].upper()
            else:
                parts[i] = parts[i].lower()
        return "-".join(parts)

    @classmethod
    def _collect_audio_tracks(cls, info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Best-effort audio track summary per language code.
        We do NOT try to model every single yt_dlp field – just enough
        to let the UI offer language selection.
        """
        formats = info.get("formats") or []
        by_lang: Dict[str, Dict[str, Any]] = {}

        for f in formats:
            acodec = f.get("acodec")
            if acodec in (None, "none"):
                continue  # not an audio stream

            raw_lang = (
                f.get("language")
                or f.get("lang")
                or f.get("audio_lang")
                or f.get("language_preference")
            )
            lang = cls._normalize_lang_code(raw_lang)
            if not lang:
                continue

            bitrate = f.get("abr") or f.get("tbr") or 0
            cur = by_lang.get(lang)
            if not cur or bitrate > cur.get("bitrate", 0):
                by_lang[lang] = {
                    "lang_code": lang,
                    "bitrate": bitrate,
                }

        return list(by_lang.values())

    # ----- Probe -----

    def probe(self, url: str, *, log=lambda msg: None) -> Dict[str, Any]:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "nocheckcertificate": True,
            "extractor_args": {"youtube": {"player_client": ["default"]}},
            "logger": YtdlpLogger(log),
            "retries": Config.net_retries(),
            "socket_timeout": Config.net_timeout_s(),
        }
        if Config.net_max_kbps():
            ydl_opts["ratelimit"] = int(Config.net_max_kbps()) * 1024

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            audio_tracks = self._collect_audio_tracks(info)

            return {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "filesize": info.get("filesize") or info.get("filesize_approx"),
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "formats": info.get("formats") or [],
                "audio_tracks": audio_tracks,
            }
        except Exception as ex:
            raise DownloadError("error.down.probe_failed", detail=str(ex))

    # ----- Download -----

    def download(
        self,
        *,
        url: str,
        kind: str = "video",     # "video" | "audio"
        quality: str = "auto",   # "auto" | "1080p" | "720p" | "320k" etc.
        ext: str = "mp4",
        out_dir: Path,
        progress_cb=None,
        log=lambda msg: None,
        audio_lang: Optional[str] = None,  # normalized language code or None
        file_stem: Optional[str] = None,
        cancel_check=None,   # optional callable -> bool
    ) -> Optional[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)

        postprocessors: list[Dict[str, Any]] = []
        format_sort: list[str] = []
        ytdlp_format: str

        min_h = Config.min_video_height()
        max_h = Config.max_video_height()
        ext_l = (ext or "mp4").lower()
        audio_lang = self._normalize_lang_code(audio_lang)

        if kind == "audio":
            base = "bestaudio"
            if audio_lang:
                base = (
                    f"bestaudio[language={audio_lang}]"
                    f"/bestaudio[lang={audio_lang}]"
                    f"/bestaudio"
                )
            if ext_l:
                base = f"{base}[ext={ext_l}]"
            ytdlp_format = "/".join([base, "bestaudio"])

            pp_audio: Dict[str, Any] = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext_l or "m4a",
            }
            if quality.endswith("k"):
                try:
                    q = int(quality[:-1])
                    pp_audio["preferredquality"] = str(q)
                except Exception:
                    pass
            postprocessors.append(pp_audio)

        else:
            if quality.endswith("p"):
                try:
                    req_h = int(quality[:-1])
                except Exception:
                    req_h = max_h
            else:
                req_h = max_h
            upper = min(req_h, max_h)

            if ext_l == "webm":
                v_main = (
                    f"bestvideo[height>={min_h}][height<={upper}][ext=webm]"
                )
                if audio_lang:
                    a_main = (
                        f"bestaudio[language={audio_lang}][ext=webm]"
                        f"/bestaudio[lang={audio_lang}][ext=webm]"
                        f"/bestaudio[language={audio_lang}]"
                        f"/bestaudio[lang={audio_lang}]"
                        f"/bestaudio[ext=webm]"
                        f"/bestaudio"
                    )
                else:
                    a_main = "bestaudio[ext=webm]/bestaudio"
                format_sort = [f"res:{upper}", "vcodec:vp9", "acodec:opus", "fps", "size"]
            else:
                v_main = (
                    f"bestvideo[height>={min_h}][height<={upper}][ext=mp4]"
                    f"/bestvideo[height>={min_h}][height<={upper}]"
                )
                if audio_lang:
                    a_main = (
                        f"bestaudio[language={audio_lang}][ext=m4a]"
                        f"/bestaudio[lang={audio_lang}][ext=m4a]"
                        f"/bestaudio[language={audio_lang}]"
                        f"/bestaudio[lang={audio_lang}]"
                        f"/bestaudio[ext=m4a]"
                        f"/bestaudio"
                    )
                else:
                    a_main = "bestaudio[ext=m4a]/bestaudio"
                format_sort = [f"res:{upper}", "vcodec:h264", "acodec:aac", "fps", "size"]

            ytdlp_format = f"{v_main}+{a_main}/{v_main}/{a_main}/best"

        safe_title = sanitize_filename(file_stem or "download")
        out_tpl = str(out_dir / f"{safe_title}.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "outtmpl": out_tpl,
            "noplaylist": True,
            "quiet": True,
            "nocheckcertificate": True,
            "retries": Config.net_retries(),
            "socket_timeout": Config.net_timeout_s(),
            "concurrent_fragment_downloads": Config.net_concurrent_fragments(),
            "logger": YtdlpLogger(log),
            "progress_hooks": [progress_cb] if progress_cb else [],
            "format": ytdlp_format,
        }

        if format_sort:
            ydl_opts["format_sort"] = format_sort

        if Config.net_max_kbps():
            ydl_opts["ratelimit"] = int(Config.net_max_kbps()) * 1024

        if postprocessors:
            ydl_opts["postprocessors"] = postprocessors

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if cancel_check and bool(cancel_check()):
                    raise DownloadCancelled()

                info = ydl.extract_info(url, download=True)
                if cancel_check and bool(cancel_check()):
                    raise DownloadCancelled()

                if not info:
                    return None

                # Determine the final output file path
                # Prefer _filename but fall back to requested stem.
                out = info.get("_filename") or info.get("requested_downloads", [{}])[0].get("_filename")
                if out:
                    p = Path(out)
                    return p if p.exists() else None

                # Try to find a file that matches the stem in out_dir
                for p in out_dir.glob(f"{safe_title}.*"):
                    if p.is_file():
                        return p

                return None

        except DownloadCancelled:
            raise
        except Exception as ex:
            raise DownloadError("error.down.download_failed", detail=str(ex))