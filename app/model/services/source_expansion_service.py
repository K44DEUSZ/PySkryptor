# app/model/services/source_expansion_service.py
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.model.config.app_config import AppConfig as Config
from app.model.domain.errors import AppError
from app.model.domain.results import ExpandedSourceItem, SourceExpansionResult
from app.model.io.file_manager import FileManager
from app.model.io.media_probe import is_url_source


class SourceExpansionService:
    """Resolve one user add action into normalized queue items."""

    def __init__(
        self,
        *,
        cancel_check: Callable[[], bool] | None = None,
        status_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._cancel_check = cancel_check
        self._status_callback = status_callback

    @staticmethod
    def _supported_exts() -> list[str]:
        return list(Config.files_media_input_file_exts())

    def _emit_status(self, key: str, **params: Any) -> None:
        cb = self._status_callback
        if cb is None:
            return
        cb(str(key or ""), dict(params or {}))

    def expand_manual_input(self, raw: str) -> SourceExpansionResult:
        self._emit_status("dialog.expansion_progress.manual_input")
        parsed = FileManager.parse_source_input(raw, supported_exts=self._supported_exts())
        if not parsed.get("ok", False):
            err = str(parsed.get("error") or "invalid")
            raise AppError("error.files.source_expand_invalid", {"reason": err})

        source_type = str(parsed.get("type") or "").strip().lower()
        key = str(parsed.get("key") or "").strip()
        if not key:
            raise AppError("error.files.source_expand_invalid", {"reason": "empty"})

        if source_type == "url":
            from app.model.services.download_service import DownloadError, DownloadService

            self._emit_status("dialog.expansion_progress.playlist")
            try:
                playlist = DownloadService().resolve_playlist(
                    key,
                    cancel_check=self._cancel_check,
                )
            except DownloadError as ex:
                if str(getattr(ex, "key", "")) != "error.playlist.not_playlist":
                    raise
            else:
                return SourceExpansionResult(
                    origin_kind="playlist",
                    origin_label=str(playlist.playlist_title or playlist.playlist_url or key),
                    discovered_count=int(playlist.total_count),
                    items=tuple(
                        ExpandedSourceItem(
                            key=str(entry.entry_url),
                            source_kind="url",
                            title=str(entry.title or ""),
                            duration_s=entry.duration_s,
                        )
                        for entry in playlist.entries
                        if str(entry.entry_url or "").strip()
                    ),
                )

        source_kind = "url" if source_type == "url" or is_url_source(key) else "file"
        origin_label = key if source_kind == "url" else str(Path(key).name or key)
        return SourceExpansionResult(
            origin_kind="manual_input",
            origin_label=origin_label,
            discovered_count=1,
            items=(ExpandedSourceItem(key=key, source_kind=source_kind),),
        )

    def expand_local_paths(self, paths: list[str], *, origin_kind: str) -> SourceExpansionResult:
        normalized_paths = [str(p or "").strip() for p in list(paths or []) if str(p or "").strip()]
        kind = str(origin_kind or "local_paths")
        status_key = {
            "folder": "dialog.expansion_progress.folder",
            "file_selection": "dialog.expansion_progress.selection",
            "drop": "dialog.expansion_progress.drop",
        }.get(kind, "dialog.expansion_progress.local_paths")
        self._emit_status(status_key)
        keys = FileManager.collect_media_files(
            normalized_paths,
            supported_exts=self._supported_exts(),
            cancel_check=self._cancel_check,
        )
        items = tuple(ExpandedSourceItem(key=str(key), source_kind="file") for key in keys)

        origin_label = normalized_paths[0] if len(normalized_paths) == 1 else ""

        return SourceExpansionResult(
            origin_kind=kind,
            origin_label=origin_label,
            discovered_count=len(items),
            items=items,
        )
