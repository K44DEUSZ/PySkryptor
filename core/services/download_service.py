# core/services/download_service.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Dict, Any, List, Iterable, Union, overload

from core.io.ytdlp_downloader import YtDlpDownloader
from core.utils.text import is_url, sanitize_filename
from core.config.app_config import AppConfig as Config


UrlLike = Union[str, Path]


def _coerce_urls(maybe_urls: Optional[Union[UrlLike, Iterable[UrlLike]]]) -> List[str]:
    """Accept a single url/path or an iterable of them and return a clean list[str]."""
    if maybe_urls is None:
        return []
    if isinstance(maybe_urls, (str, Path)):
        candidates = [str(maybe_urls)]
    elif isinstance(maybe_urls, Iterable):
        candidates = [str(x) for x in maybe_urls]
    else:
        candidates = [str(maybe_urls)]
    return [u for u in candidates if is_url(u)]


class DownloadService:
    """Facade for downloader with validation and skip-if-exists + legacy compatibility."""

    def __init__(self) -> None:
        self._backend = YtDlpDownloader()

    def probe(self, url: str, log: Callable[[str], None]) -> Dict[str, Any]:
        if not is_url(url):
            raise ValueError("Nieprawidłowy URL.")
        return self._backend.probe(url, log)

    @overload
    def download(
        self,
        url: str,
        format_expr: str = ...,
        desired_ext: str = ...,
        kind: str = ...,
        output_dir: Optional[Path] = ...,
        progress_cb: Optional[Callable[[int, str], None]] = ...,
        log: Optional[Callable[[str], None]] = ...,
        *,
        urls: None = ...,
        on_file_ready: Optional[Callable[[Path], None]] = ...,
    ) -> Path: ...

    @overload
    def download(
        self,
        url: Optional[str] = ...,
        format_expr: str = ...,
        desired_ext: str = ...,
        kind: str = ...,
        output_dir: Optional[Path] = ...,
        progress_cb: Optional[Callable[[int, str], None]] = ...,
        log: Optional[Callable[[str], None]] = ...,
        *,
        urls: Union[UrlLike, Iterable[UrlLike]],
        on_file_ready: Optional[Callable[[Path], None]] = ...,
    ) -> List[Path]: ...

    def download(
        self,
        url: Optional[str] = None,
        format_expr: str = "bestaudio/best",
        desired_ext: str = "",
        kind: str = "video",
        output_dir: Optional[Path] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        log: Optional[Callable[[str], None]] = None,
        *,
        urls: Optional[Union[UrlLike, Iterable[UrlLike]]] = None,
        on_file_ready: Optional[Callable[[Path], None]] = None,
    ):
        """
        Single URL mode → returns Path.
        Legacy/batch mode (urls=...) → returns List[Path] (so callers can iterate).
        """
        _log = log or (lambda m: None)
        _progress = progress_cb or (lambda p, s: None)
        out_dir = output_dir or Config.DOWNLOADS_DIR

        # Batch / legacy mode → return list[Path]
        urls_list = _coerce_urls(urls)
        if urls_list:
            results: List[Path] = []
            for u in urls_list:
                try:
                    target = self._backend.download(
                        u,
                        "bestaudio/best",
                        "mp3",
                        "audio",
                        Config.INPUT_TMP_DIR,
                        _progress,
                        _log,
                    )
                    if on_file_ready:
                        try:
                            on_file_ready(target)
                        except Exception:
                            pass
                    results.append(target)
                except Exception as e:
                    _log(f"❌ Błąd pobierania {u}: {e}")
            if not results:
                raise RuntimeError("Brak poprawnych adresów URL do pobrania.")
            return results

        # Single URL mode → return Path
        if not url or not is_url(url):
            raise ValueError("Nieprawidłowy URL.")

        meta = self._backend.probe(url, _log)
        title = meta.get("title") or "plik"
        safe = sanitize_filename(title)

        # Skip if the same base already exists with desired ext (if provided)
        candidates = list(out_dir.glob(f"{safe}.*"))
        if candidates and desired_ext:
            for p in candidates:
                if p.suffix.lower() == f".{desired_ext.lower()}":
                    _log(f"ℹ️ Plik „{p.name}” już istnieje — pomijam pobieranie.")
                    _progress(100, "gotowe")
                    return p

        return self._backend.download(url, format_expr, desired_ext, kind, out_dir, _progress, _log)
