# core/downloader.py
# Stabilny interfejs pobierania przez yt_dlp.
# API:
#   Downloader.download(urls=[...], on_file_ready: Optional[Callable[[Path], None]] = None, log=print) -> list[Path]

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, List, Dict, Any
import unicodedata
import uuid
import re

import yt_dlp

from core.config import Config
from core.ytdlp_logger import YtdlpLogger


def _slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


class Downloader:
    @staticmethod
    def download(
        urls: List[str],
        on_file_ready: Optional[Callable[[Path], None]] = None,
        log: Callable[[str], None] = print,
    ) -> List[Path]:
        results: List[Path] = []
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
                    try:
                        on_file_ready(file)
                    except Exception:
                        pass
                results.append(file)
        return results

    # ---- prywatne ----

    @staticmethod
    def _get_info(url: str, log: Callable[[str], None]) -> Optional[Dict[str, Any]]:
        ydl_opts = {
            "logger": YtdlpLogger(log),
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            log(f"❌ Nie udało się pobrać metadanych: {e}")
            return None

    @staticmethod
    def _should_skip_url(info: Dict[str, Any], log: Callable[[str], None]) -> bool:
        return False

    @staticmethod
    def _download(info: Dict[str, Any], log: Callable[[str], None]) -> Optional[Path]:
        title = info.get("title") or "plik"
        ext = "mp3"
        guid = uuid.uuid4().hex[:8]
        slug = _slugify(title) or "plik"
        out_base = f"{slug}-{guid}"

        out_dir = (Config.INPUT_DIR if hasattr(Config, "INPUT_DIR") else Path.cwd())
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tmpl = str(out_dir / f"{out_base}.%(ext)s")

        # Minimalny i stabilny zestaw postprocessorów:
        postprocessors = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext,
                "preferredquality": "0",
            }
        ]

        ydl_opts = {
            "logger": YtdlpLogger(log),
            "quiet": True,
            "outtmpl": out_tmpl,
            "noplaylist": True,
            "postprocessors": postprocessors,
            "format": "bestaudio/best",
            # Dodatkowe argumenty do konwersji: 16 kHz / mono
            "postprocessor_args": [
                "-ar", "16000",
                "-ac", "1",
            ],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                log("⬇️ Pobieranie i konwersja…")
                ydl.download([info.get("webpage_url") or info.get("url")])
                # yt_dlp nie zwraca ścieżki; wyszukujemy powstały plik
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
