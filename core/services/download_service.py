# pyskryptor/core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, List

from core.contracts.downloader import Downloader as DownloaderProtocol
from core.io.ytdlp_downloader import YtDlpDownloader


class DownloadService:
    """Facade for downloads; uses YtDlpDownloader."""

    def __init__(self, backend: Optional[DownloaderProtocol] = None) -> None:
        self._backend = backend or YtDlpDownloader()

    def peek_output_stem(self, url: str, log: Callable[[str], None]) -> Optional[str]:
        return self._backend.peek_output_stem(url, log)

    def download(self, urls: List[str], on_file_ready: Optional[Callable[[Path], None]], log: Callable[[str], None]) -> List[Path]:
        return self._backend.download(urls, on_file_ready, log)
