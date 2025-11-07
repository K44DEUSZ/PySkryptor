from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Dict, Any

from core.config.app_config import AppConfig as Config
from core.io.ytdlp_downloader import YtDlpDownloader
from core.utils.text import is_url, sanitize_filename


class DownloadService:
    """
    Facade above YtDlpDownloader. Validates URL, performs probe and download.
    """

    def __init__(self) -> None:
        self._io = YtDlpDownloader()

    def probe(self, url: str, log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        if not is_url(url):
            raise ValueError("Niepoprawny URL.")
        return self._io.probe(url, log=log)

    def download(
        self,
        *,
        url: str,
        kind: str = "video",
        quality: str = "auto",
        ext: str = "mp4",
        out_dir: Optional[Path] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> Path:
        if not is_url(url):
            raise ValueError("Niepoprawny URL.")

        out_dir = out_dir or Config.DOWNLOADS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        postprocess_audio = kind.lower().startswith("audio")
        path = self._io.download(
            url=url,
            out_dir=out_dir,
            format_id=None,           # mapowanie quality->format_id możesz dodać później
            ext=ext,
            progress_cb=progress_cb,
            log=log,
            postprocess_audio=postprocess_audio,
        )
        # Bezpieczna nazwa w systemie plików (zachowujemy rozszerzenie)
        safe = sanitize_filename(path.stem) + path.suffix
        final_path = path.with_name(safe)
        if final_path != path:
            try:
                path.rename(final_path)
            except Exception:
                final_path = path  # jeśli nie uda się zmienić nazwy, zostaw oryginalną

        return final_path
