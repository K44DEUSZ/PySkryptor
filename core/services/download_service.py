# core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any

import yt_dlp

from core.config.app_config import AppConfig as Config
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
            return {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "filesize": info.get("filesize") or info.get("filesize_approx"),
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "formats": info.get("formats") or [],
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
    ) -> Optional[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)

        postprocessors: list[Dict[str, Any]] = []
        format_sort: list[str] = []
        ytdlp_format: str

        min_h = Config.min_video_height()
        max_h = Config.max_video_height()

        if kind == "audio":
            fmt = "bestaudio"
            if ext:
                fmt += f"[ext={ext}]"
            ytdlp_format = "/".join([fmt, "bestaudio"])
            pp_audio: Dict[str, Any] = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext or "m4a",
            }
            if quality.endswith("k"):
                try:
                    q = int(quality[:-1])
                    pp_audio["preferredquality"] = str(q)
                except Exception:
                    pass
            postprocessors.append(pp_audio)
        else:
            # video
            if quality.endswith("p"):
                try:
                    req_h = int(quality[:-1])
                except Exception:
                    req_h = max_h
            else:
                req_h = max_h
            upper = min(req_h, max_h)

            filt_main = f"bestvideo[height>={min_h}][height<={upper}][ext={ext}]+bestaudio"
            alt_same = f"bestvideo[height>={min_h}][height<={upper}]+bestaudio"
            fallback = f"bestvideo[height>={min_h}]+bestaudio"
            ytdlp_format = "/".join([filt_main, alt_same, fallback])
            format_sort = [f"res:{upper}", "vcodec:avc", "fps", "size"]

            postprocessors.append({
                "key": "FFmpegVideoConvertor",
                "preferedformat": ext,
            })

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
            "merge_output_format": ext if kind == "video" else None,
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
