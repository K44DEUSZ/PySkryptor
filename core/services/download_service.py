# core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any, List

import yt_dlp

from core.config.app_config import AppConfig as Config
from core.io.text import sanitize_filename
from ui.utils.logging import YtdlpQtLogger


class DownloadError(RuntimeError):
    """Error with i18n key and params to be localized by UI."""

    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


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
            vcodec = f.get("vcodec")
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
            "logger": YtdlpQtLogger(log),
            "retries": Config.net_retries(),
            "socket_timeout": Config.net_timeout_s(),
        }
        if Config.net_proxy():
            ydl_opts["proxy"] = Config.net_proxy()
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
        file_stem: Optional[str] = None,   # optional base name override
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
            # audio-only download
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
            # video + audio merged
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
                format_sort = [f"res:{upper}", "vcodec:avc", "acodec:m4a", "fps", "size"]

            filt_main = f"{v_main}+{a_main}"
            alt_same = f"bestvideo[height>={min_h}][height<={upper}]+bestaudio"
            fallback = f"bestvideo[height>={min_h}]+bestaudio"
            ytdlp_format = "/".join([filt_main, alt_same, fallback])

        # Output template – optionally honour requested stem (e.g. rename-on-duplicate).
        if file_stem:
            safe = sanitize_filename(file_stem)
            outtmpl = str(out_dir / f"{safe}.%(ext)s")
        else:
            outtmpl = str(out_dir / "%(title)s.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "format": ytdlp_format,
            "format_sort": format_sort,
            "format_sort_force": True,
            "outtmpl": outtmpl,
            "quiet": True,
            "nocheckcertificate": True,
            "noprogress": False,
            "retries": Config.net_retries(),
            "concurrent_fragment_downloads": Config.net_concurrent_fragments(),
            "socket_timeout": Config.net_timeout_s(),
            "nopart": True,
            "continuedl": False,
            "postprocessors": postprocessors,
            "progress_hooks": [lambda d: self._on_progress(d, progress_cb)],
            "extractor_args": {"youtube": {"player_client": ["default"]}},
            "logger": YtdlpQtLogger(log),
        }
        if Config.net_proxy():
            ydl_opts["proxy"] = Config.net_proxy()
        if Config.net_max_kbps():
            ydl_opts["ratelimit"] = int(Config.net_max_kbps()) * 1024

        # drop None values
        ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}

        if Config.FFMPEG_BIN_DIR.exists():
            ydl_opts["ffmpeg_location"] = str(Config.FFMPEG_BIN_DIR)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                out_path: Optional[Path] = None

                if isinstance(info, dict):
                    req = info.get("requested_downloads")
                    if isinstance(req, list) and req:
                        fp = req[-1].get("filepath")
                        if fp and not str(fp).endswith(".part"):
                            out_path = Path(fp)
                    if out_path is None:
                        fn = info.get("_filename")
                        if fn and not str(fn).endswith(".part"):
                            out_path = Path(fn)

                if out_path is None:
                    candidates = [p for p in out_dir.glob("*.*") if not p.name.endswith(".part")]
                    if not candidates:
                        raise DownloadError("error.down.no_output_file")
                    out_path = max(candidates, key=lambda p: p.stat().st_mtime)

                return out_path

        except DownloadError:
            raise

        except Exception as ex:
            raise DownloadError("error.down.download_failed", detail=str(ex))

    # ----- Internal -----

    @staticmethod
    def _on_progress(d: Dict[str, Any], cb) -> None:
        if not cb:
            return
        try:
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = int(done * 100 / total) if total else 0
                cb(pct, "downloading")
            elif status == "finished":
                cb(100, "finished")
        except Exception:
            pass
