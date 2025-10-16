# core/io/ytdlp_downloader.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Any

import yt_dlp

from core.utils.logging import YtdlpProxyLogger
from core.utils.text import sanitize_filename
from core.config.app_config import AppConfig as Config


def _format_entry(fmt: Dict[str, Any]) -> Dict[str, Any]:
    vcodec = fmt.get("vcodec")
    acodec = fmt.get("acodec")
    kind = "audio" if (vcodec in (None, "none")) else ("video" if (acodec not in (None, "none")) else "unknown")
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    height = fmt.get("height") or 0
    abr = fmt.get("abr") or 0
    return {
        "id": str(fmt.get("format_id")),
        "ext": fmt.get("ext"),
        "acodec": acodec,
        "vcodec": vcodec,
        "height": int(height) if height else 0,
        "abr": int(abr) if abr else 0,
        "filesize": int(size) if size else None,
        "format_note": fmt.get("format_note") or "",
        "kind": kind,
        "desc": fmt.get("format") or "",
    }


class YtDlpDownloader:
    """yt_dlp probe and download with progress reporting and optional audio conversion."""

    def probe(self, url: str, log: Callable[[str], None]) -> Dict[str, Any]:
        ydl_opts = {
            "logger": YtdlpProxyLogger(log),
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "prefer_ffmpeg": True,
            "ffmpeg_location": str(Config.FFMPEG_BIN_DIR),
            "allow_generic_extractor": True,
            "listformats": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get("title") or "plik"
        duration = info.get("duration") or 0
        extractor = info.get("extractor") or info.get("extractor_key") or "unknown"
        fmts = info.get("formats") or []
        filtered = []
        for f in fmts:
            if f.get("format_id") and (f.get("acodec") not in (None, "none") or f.get("vcodec") not in (None, "none")):
                filtered.append(_format_entry(f))
        safe_name = sanitize_filename(title)
        return {
            "service": extractor,
            "title": title,
            "duration": int(duration),
            "formats": filtered,
            "suggested_name": safe_name,
            "thumbnail": info.get("thumbnail"),
        }

    def download(
        self,
        url: str,
        format_expr: str,
        desired_ext: str,
        kind: str,
        output_dir: Path,
        progress_cb: Callable[[int, str], None],
        log: Callable[[str], None],
    ) -> Path:
        """
        kind: "video" | "audio"
        desired_ext: target container/codec preference; for audio can be mp3/m4a/webm.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        def _hook(d: Dict[str, Any]) -> None:
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                pct = int(done * 100 / total) if total else 0
                progress_cb(max(0, min(100, pct)), "pobieranie")
            elif status == "finished":
                progress_cb(100, "postprocess")

        outtmpl = str(output_dir / "%(title).200B.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "logger": YtdlpProxyLogger(log),
            "quiet": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "prefer_ffmpeg": True,
            "ffmpeg_location": str(Config.FFMPEG_BIN_DIR),
            "progress_hooks": [_hook],
            "format": str(format_expr),
            "postprocessor_args": [],
        }

        # enforce audio extension via postprocessor if needed
        if kind == "audio" and desired_ext in {"mp3", "m4a"}:
            ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": desired_ext, "preferredquality": "0"}]

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            progress_cb(0, "analiza")
            info = ydl.extract_info(url, download=True)
            res = ydl.prepare_filename(info)

        result = Path(res)
        if not result.exists():
            cand = list(output_dir.glob(f"{result.stem}.*"))
            if cand:
                result = cand[0]
        if not result.exists():
            raise RuntimeError("Pobieranie zakończone, ale nie odnaleziono pliku wynikowego.")
        return result
