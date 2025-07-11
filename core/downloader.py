from pathlib import Path
from typing import Callable
import unicodedata
import uuid
import yt_dlp
import re

from core.config import Config
from core.ytdlp_logger import YtdlpLogger

class Downloader:
    @staticmethod
    def download(urls: list[str], on_file_ready: Callable[[Path], None] = None, log: Callable[[str], None] = print) -> list[Path]:
        results = []
        for url in urls:
            info = Downloader._get_info(url, log)
            if not info:
                log(f"⚠️ Serwis nieobsługiwany lub nie udało się pobrać metadanych: {url}")
                continue
            if Downloader._should_skip_url(info, log):
                continue
            file = Downloader._download(info, log)
            if file:
                if on_file_ready:
                    on_file_ready(file)
                results.append(file)
        return results

    @staticmethod
    def _get_info(url: str, log: Callable[[str], None]) -> dict | None:
        try:
            with yt_dlp.YoutubeDL({
                'quiet': True,
                'logger': YtdlpLogger(log),
                'allow_generic_extractor': False
            }) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception:
            return None

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        name = unicodedata.normalize("NFKD", name)
        cleaned = "".join(c for c in name if c.isalnum() or c in " -_")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:80] or f"file_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _should_skip_url(info: dict, log=print) -> bool:
        title = info.get("title", "")
        safe_title = Downloader._sanitize_filename(title)
        output_dir = Config.OUTPUT_DIR / safe_title
        if output_dir.exists():
            log(f"⏭️ Transkrypcja dla '{title}' już istnieje — pomijam.")
            return True
        return False

    @staticmethod
    def _download(info: dict, log: Callable[[str], None]) -> Path | None:
        title = info.get("title", f"yt_{uuid.uuid4().hex[:8]}")
        safe_title = Downloader._sanitize_filename(title)
        output_path = Config.INPUT_DIR / f"{safe_title}.mp3"
        options = {
            "format": "bestaudio/best",
            "outtmpl": str(Config.INPUT_DIR / f"{safe_title}.%(ext)s"),
            "quiet": True,
            "logger": YtdlpLogger(log),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192"
                }
            ],
            "postprocessor_args": ["-ar", "16000", "-ac", "1"]
        }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([info["webpage_url"]])
            if not output_path.exists():
                log(f"❌ Nie udało się utworzyć pliku MP3 dla: {info.get('webpage_url')}")
                return None
            return output_path
        except Exception as e:
            log(f"❌ Błąd podczas pobierania: {e}")
            return None
