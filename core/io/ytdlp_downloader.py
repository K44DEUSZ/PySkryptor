from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Any, Optional, List

from yt_dlp import YoutubeDL

from core.config.app_config import AppConfig as Config


class YtdlpProxyLogger:
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


class YtDlpDownloader:
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

    def download(
        self,
        url: str,
        *,
        out_dir: Optional[Path] = None,
        format_id: Optional[str] = None,
        ext: Optional[str] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        postprocess_audio: bool = False,
    ) -> Path:
        out_dir = out_dir or Config.DOWNLOADS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        def _hook(d: Dict[str, Any]) -> None:
            if not progress_cb:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = int(done * 100 / total) if total else 0
                progress_cb(pct, "pobieranie")
            elif status == "finished":
                progress_cb(100, "postprocess")

        outtmpl = str(out_dir / "%(title)s.%(ext)s")

        opts: Dict[str, Any] = self._base_opts(log)
        opts.update({"outtmpl": outtmpl, "progress_hooks": [_hook]})

        if format_id:
            opts["format"] = format_id
        else:
            opts["format"] = "bestaudio/best" if postprocess_audio else "bestvideo*+bestaudio/best"

        postprocessors: List[Dict[str, Any]] = []
        if postprocess_audio:
            postprocessors.append(
                {"key": "FFmpegExtractAudio", "preferredcodec": (ext or "m4a"), "preferredquality": "0"}
            )
        elif ext and ext.lower() in {"mp4", "mkv"}:
            postprocessors.append({"key": "FFmpegVideoConvertor", "preferedformat": ext.lower()})
        if postprocessors:
            opts["postprocessors"] = postprocessors

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

            if postprocess_audio and ext:
                final_path = final_path.with_suffix(f".{ext}")

        return final_path
