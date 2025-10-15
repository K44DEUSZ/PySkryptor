# pyskryptor/core/contracts/downloader.py
from __future__ import annotations
from pathlib import Path
from typing import Callable, List, Optional, Protocol


class Downloader(Protocol):
    def peek_output_stem(self, url: str, log: Callable[[str], None]) -> Optional[str]: ...
    def download(self, urls: List[str], on_file_ready: Optional[Callable[[Path], None]], log: Callable[[str], None]) -> List[Path]: ...
