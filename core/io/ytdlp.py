# core/io/ytdlp.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Any, Optional, List

from yt_dlp import YoutubeDL

from core.config.app_config import AppConfig as Config


class YtdlpProxyLogger:
    """Thin adapter to route yt_dlp logs into our GUI logger."""
    def __init__(self, log: Optional[Callable[[str], None]] = None) -> None:
        self._log = log or (lambda *_: None)

    def debug(self, msg: str) -> None:
        if msg.startswith("ERROR:"):
            self._log(f"❌ {msg}")

    def info(self, msg: str) -> None:
        if msg and not msg.startswith("[debug]"):
            self._log(msg)

    def warning(self, msg: str) -> None:
        if "UNPLAYABLE" in msg:
            return
        self._log(f"⚠️ {msg}")

    def error(self, msg: str) -> None:
        self._log(f"❌ {msg}")


class YtDlpHandler:
    """yt_dlp-based probe/download helper compatible with the refactored contracts."""

    # ----- Options -----

    def _base_opts(self, log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            "logger": YtdlpProxyLogger(log),
            "noplaylist": True,
            "prefer_ffmpeg": True,
            "ffmpeg_location": str(Config.FFMPEG_BIN_DIR),
            "allow_generic_extractor": True,
            "listformats": False,
            "ignoreerrors": False,
            "extract_flat": False,
            "list_unplayable_formats": False,
            "allow_unplayable_formats": False,
            "extractor_args": {
                "youtube": {"player_client": ["android", "web"]},
            },
        }

    # ----- Probe -----

    def probe(self, url: str, log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        opts = self._base_opts(log)
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title") or "file"
        duration = info.get("duration")
        extractor = info.get("extractor_key") or info.get("extractor")
        filesize = info.get("filesize") or info.get("filesize_approx")

        formats: List[Dict[str, Any]] = []
        for f in info.get("formats", []) or []:
            formats.append(
                {
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "height": f.get("height"),
                    "width": f.get("width"),
                    "abr": f.get("abr"),
                    "tbr": f.get("tbr"),
                    "filesize": f.get("filesize"),
                    "filesize_approx": f.get("filesize_approx"),
                    "format_note": f.get("format_note"),
                }
            )

        return {
            "extractor": extractor,
            "service": extractor,
            "title": title,
            "duration": duration,
            "filesize": filesize,
            "formats": formats,
            "suggested_filename": title,
        }

    # ----- Download -----

    def _format_selector(self, *, kind: Optional[str], quality: Optional[str]) -> str:
        k = (kind or "").lower()
        q = (quality or "").lower()

        if k == "audio":
            if q.endswith("k"):
                try:
                    abr = int(q[:-1])
                    return f"bestaudio[abr<={abr}]/bestaudio/best"
                except Exception:
                    return "bestaudio/best"
            return "bestaudio/best"

        # video or auto
        if q.endswith("p"):
            try:
                h = int(q[:-1])
                return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
            except Exception:
                return "bestvideo*+bestaudio/best"
        return "bestvideo*+bestaudio/best"

    def _postprocessors(self, *, kind: Optional[str], ext: Optional[str]) -> List[Dict[str, Any]]:
        pp: List[Dict[str, Any]] = []
        if not ext:
            return pp
        e = ext.lower()
        if (kind or "").lower() == "audio":
            if e in {"mp3", "m4a", "aac", "wav", "flac", "opus"}:
                pp.append({"key": "FFmpegExtractAudio", "preferredcodec": e, "preferredquality": "0"})
        else:
            if e in {"mp4", "mkv"}:
                pp.append({"key": "FFmpegVideoConvertor", "preferedformat": e})
        return pp

    def download(
        self,
        urls: List[str],
        on_file_ready: Optional[Callable[[Path], None]],
        log: Callable[[str], None],
        *,
        kind: Optional[str] = None,
        quality: Optional[str] = None,
        ext: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,  # percent, stage
    ) -> List[Path]:
        results: List[Path] = []
        out_dir = Config.DOWNLOADS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        def _hook(d: Dict[str, Any]) -> None:
            if not progress_cb:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = int(done * 100 / total) if total else 0
                progress_cb(pct, "download")
            elif status == "finished":
                progress_cb(100, "postprocess")

        outtmpl = str(out_dir / "%(title)s.%(ext)s")
        fmt = self._format_selector(kind=kind, quality=quality)
        postprocessors = self._postprocessors(kind=kind, ext=ext)

        for url in urls:
            if progress_cb:
                progress_cb(0, "analyze")

            opts: Dict[str, Any] = self._base_opts(log)
            opts.update(
                {
                    "outtmpl": outtmpl,
                    "format": fmt,
                    "progress_hooks": [_hook],
                }
            )
            if postprocessors:
                opts["postprocessors"] = postprocessors

            # Prefer container if user requested specific ext and no audio-extract
            if ext and (kind or "").lower() != "audio" and not postprocessors:
                pass

            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                final_path = Path(ydl.prepare_filename(info))

                try:
                    rd = info.get("requested_downloads")
                    if rd and isinstance(rd, list):
                        cand = rd[-1].get("filepath")
                        if cand:
                            final_path = Path(cand)
                except Exception:
                    pass

                # If we extracted audio and specified ext, enforce suffix
                if (kind or "").lower() == "audio" and ext:
                    final_path = final_path.with_suffix(f".{ext.lower()}")

                if on_file_ready:
                    try:
                        on_file_ready(final_path)
                    except Exception:
                        pass

                results.append(final_path)

        return results
