# core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yt_dlp

from core.config.app_config import AppConfig as Config
from core.utils.logging import YtdlpQtLogger


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

    # ---------- Probe ----------

    def probe(self, url: str, *, log=lambda msg: None) -> Dict[str, Any]:
        """
        Lightweight metadata fetch without downloading the media.
        Returns a small dict with the most useful fields for the UI.
        """
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "nocheckcertificate": True,
            # Helps avoid SABR-only formats on YouTube; reduces noisy warnings.
            "extractor_args": {"youtube": {"player_client": ["default"]}},
            # Route yt_dlp logs to GUI with filtering.
            "logger": YtdlpQtLogger(log),
        }
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

    # ---------- Download ----------

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
        """
        Download media and return the final output path.
        Uses postprocessors to ensure the requested container/codec where possible.
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Format selection ----
        postprocessors: list[Dict[str, Any]] = []
        ytdlp_format: str

        if kind == "audio":
            # Choose best audio, optionally constrained by extension/bitrate.
            fmt_parts = ["bestaudio"]
            if ext:
                fmt_parts[0] += f"[ext={ext}]"
            # Fallback to any bestaudio if ext-specific match is not found.
            ytdlp_format = "/".join(fmt_parts + ["bestaudio"])

            # FFmpegExtractAudio will set final codec/container.
            pp_audio: Dict[str, Any] = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext or "m4a",
            }
            # Map "320k" etc. to preferredquality for audio when possible.
            if quality.endswith("k"):
                try:
                    q = int(quality[:-1])
                    pp_audio["preferredquality"] = str(q)
                except Exception:
                    pass
            postprocessors.append(pp_audio)

        else:
            # Video: prefer a height cap + container, with sane fallbacks.
            if quality.endswith("p"):
                try:
                    h = int(quality[:-1])
                    ytdlp_format = (
                        f"bestvideo[height<={h}][ext={ext}]+bestaudio/"
                        f"bestvideo[height<={h}]+bestaudio/"
                        f"best[ext={ext}]/best"
                    )
                except Exception:
                    ytdlp_format = f"bestvideo[ext={ext}]+bestaudio/best[ext={ext}]/best"
            else:
                ytdlp_format = f"bestvideo[ext={ext}]+bestaudio/best[ext={ext}]/best"

            # Convert/merge into the requested container (yt-dlp uses 'preferedformat').
            postprocessors.append({
                "key": "FFmpegVideoConvertor",
                "preferedformat": ext,
            })

        # ---- Output template; avoid leaving .part files behind ----
        outtmpl = str(out_dir / "%(title)s.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "format": ytdlp_format,
            "outtmpl": outtmpl,
            "quiet": True,
            "nocheckcertificate": True,
            "noprogress": False,
            "retries": 3,
            "concurrent_fragment_downloads": 4,
            "nopart": True,               # write directly to final file when possible
            "continuedl": False,          # do not attempt partial resume (less .part clutter)
            "postprocessors": postprocessors,
            "merge_output_format": ext if kind == "video" else None,
            "progress_hooks": [lambda d: self._on_progress(d, progress_cb)],
            # Reduce SABR/EJS noise and keep formats widely supported:
            "extractor_args": {"youtube": {"player_client": ["default"]}},
            # Route logs to GUI with filtering:
            "logger": YtdlpQtLogger(log),
        }

        # Remove None-valued options (yt-dlp may be picky about None).
        ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}

        # Point yt-dlp/ffmpeg to our bundled ffmpeg if available.
        if Config.FFMPEG_BIN_DIR.exists():
            ydl_opts["ffmpeg_location"] = str(Config.FFMPEG_BIN_DIR)

        # ---- Actual download ----
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                # Prefer a path reported by requested_downloads (most reliable).
                out_path: Optional[Path] = None
                if isinstance(info, dict):
                    req = info.get("requested_downloads")
                    if isinstance(req, list) and req:
                        fp = req[0].get("filepath")
                        if fp and not str(fp).endswith(".part"):
                            out_path = Path(fp)

                    # Fallback: _filename (may be missing after postproc).
                    if out_path is None:
                        fn = info.get("_filename")
                        if fn and not str(fn).endswith(".part"):
                            out_path = Path(fn)

                # Last resort: find the newest non-.part file in out_dir.
                if out_path is None:
                    candidates = [p for p in out_dir.glob("*.*") if not p.name.endswith(".part")]
                    if not candidates:
                        raise DownloadError("error.down.download_failed", detail="no output file")
                    out_path = max(candidates, key=lambda p: p.stat().st_mtime)

                return out_path

        except DownloadError:
            # Already wrapped with an i18n key; re-raise.
            raise
        except Exception as ex:
            # Wrap any unexpected exception as a localized error.
            raise DownloadError("error.down.download_failed", detail=str(ex))

    # ---------- Internal ----------

    @staticmethod
    def _on_progress(d: Dict[str, Any], cb) -> None:
        """Translate yt_dlp progress dict into a simple (percent, stage) callback."""
        if not cb:
            return
        try:
            status = d.get("status")
            if status == "downloading":
                pct_str = (d.get("_percent_str") or "").strip().rstrip("%")
                if pct_str:
                    cb(int(float(pct_str)), "downloading")
            elif status == "finished":
                cb(100, "finished")
        except Exception:
            # Swallow any progress parsing errors to avoid breaking the download.
            pass
