# pyskryptor/core/io/ytdlp_downloader.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, List, Dict, Any
import unicodedata
import re
import uuid

import yt_dlp

from core.utils.logging import YtdlpProxyLogger
from core.config import Config


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


class YtDlpDownloader:
    """yt_dlp utility with metadata peek and audio extraction to 16kHz mono MP3."""

    def peek_output_stem(self, url: str, log: Callable[[str], None]) -> Optional[str]:
        info = self._get_info(url, log)
        if not info:
            return None
        title = info.get("title") or "plik"
        return slugify(title) or "plik"

    def download(self, urls: List[str], on_file_ready: Optional[Callable[[Path], None]], log: Callable[[str], None]) -> List[Path]:
        results: List[Path] = []
        for url in urls:
            info = self._get_info(url, log)
            if not info:
                log(f"⚠️ Serwis nieobsługiwany lub brak metadanych: {url}")
                continue
            file = self._download(info, log)
            if file:
                if on_file_ready:
                    try:
                        on_file_ready(file)
                    except Exception:
                        pass
                results.append(file)
        return results

    def _get_info(self, url: str, log: Callable[[str], None]) -> Optional[Dict[str, Any]]:
        ydl_opts = {
            "logger": YtdlpProxyLogger(log),
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "prefer_ffmpeg": True,
            "ffmpeg_location": str(Config.FFMPEG_BIN_DIR),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            log(f"❌ Nie udało się pobrać metadanych: {e}")
            return None

    def _download(self, info: Dict[str, Any], log: Callable[[str], None]) -> Optional[Path]:
        title = info.get("title") or "plik"
        ext = "mp3"
        guid = uuid.uuid4().hex[:8]
        slug = slugify(title) or "plik"
        out_base = f"{slug}-{guid}"

        out_dir = Config.INPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tmpl = str(out_dir / f"{out_base}.%(ext)s")

        postprocessors = [
            {"key": "FFmpegExtractAudio", "preferredcodec": ext, "preferredquality": "0"}
        ]

        ydl_opts = {
            "logger": YtdlpProxyLogger(log),
            "quiet": True,
            "outtmpl": out_tmpl,
            "noplaylist": True,
            "postprocessors": postprocessors,
            "format": "bestaudio/best",
            "postprocessor_args": ["-ar", "16000", "-ac", "1"],  # fixed stray space
            "prefer_ffmpeg": True,
            "ffmpeg_location": str(Config.FFMPEG_BIN_DIR),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                log("⬇️ Pobieranie i konwersja…")
                ydl.download([info.get("webpage_url") or info.get("url")])
                produced = sorted(out_dir.glob(f"{out_base}.*"))
                for p in produced:
                    if p.suffix.lower() == f".{ext}":
                        log(f"✅ Zapisano: {p}")
                        return p
                if produced:
                    log(f"✅ Zapisano: {produced[0]}")
                    return produced[0]
                log("⚠️ Nie odnaleziono wyjściowego pliku.")
                return None
        except Exception as e:
            log(f"❌ Błąd pobierania: {e}")
            return None
