# core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Dict, Any

from core.config.app_config import AppConfig as Config
from core.io.ytdlp import YtDlpHandler
from core.utils.text import is_url, sanitize_filename


class DownloadError(RuntimeError):
    """Download error carrying a translation key + params (UI will localize)."""
    def __init__(self, key: str, **params: Any) -> None:
        self.key = key
        self.params = params
        super().__init__(key)


class DownloadService:
    """
    Facade above YtDlpHandler with URL validation and i18n-aware errors.
    """

    def __init__(self) -> None:
        self._io = YtDlpHandler()

    def probe(self, url: str, log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        if not is_url(url):
            raise DownloadError("error.down.invalid_url", url=url)
        try:
            return self._io.probe(url, log=log)
        except Exception as ex:
            raise DownloadError("error.down.probe_failed", detail=str(ex))

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
            raise DownloadError("error.down.invalid_url", url=url)

        out_dir = out_dir or Config.DOWNLOADS_DIR
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as ex:
            raise DownloadError("error.down.output_dir", path=str(out_dir), detail=str(ex))

        postprocess_audio = kind.lower().startswith("audio")

        try:
            path = self._io.download(
                url=url,
                out_dir=out_dir,
                format_id=None,  # optional mapping quality->format_id can be added later
                ext=ext,
                progress_cb=progress_cb,
                log=log,
                postprocess_audio=postprocess_audio,
            )
        except Exception as ex:
            raise DownloadError("error.down.download_failed", detail=str(ex))

        # Sanitize final filename (preserve extension)
        try:
            safe = sanitize_filename(path.stem) + path.suffix
            final_path = path.with_name(safe)
            if final_path != path:
                try:
                    path.rename(final_path)
                except Exception:
                    final_path = path
        except Exception as ex:
            # Non-fatal; return original path
            final_path = path

        return final_path
