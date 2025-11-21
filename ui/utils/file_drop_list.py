# ui/utils/file_drop_list.py
from __future__ import annotations

from pathlib import Path
from typing import List, Iterable

from PyQt5 import QtCore, QtWidgets

from core.config.app_config import AppConfig as Config


def _supported_suffixes() -> set[str]:
    """
    Collect supported suffixes (lowercased, with leading dot) from settings-driven config.
    """
    audio = set(Config.audio_extensions())
    video = set(Config.video_extensions())
    return {s.lower() if s.startswith(".") else f".{s.lower()}" for s in (audio | video)}


def _is_supported(path: Path, *, allowed: set[str]) -> bool:
    """
    Return True if path is a file and its suffix is in allowed set.
    """
    return path.is_file() and path.suffix.lower() in allowed


def _flatten_supported_from_dir(dir_path: Path, *, allowed: set[str]) -> Iterable[Path]:
    """
    Yield all supported files from a directory (recursive).
    """
    for p in dir_path.rglob("*"):
        if _is_supported(p, allowed=allowed):
            yield p


class FileDropList(QtWidgets.QListWidget):
    """
    Lightweight drag&drop emitter.

    - Accepts files/folders/URL-list drops from the OS.
    - Filters files by extensions defined in settings (Config.audio_extensions/video_extensions).
    - Emits `pathsDropped: list[str]` with de-duplicated absolute paths.
    - Does NOT add items to itself (keeps UI responsibility in parent panel).
    """
    pathsDropped = QtCore.pyqtSignal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)

    # ----- Qt DnD -----

    def dragEnterEvent(self, e):  # type: ignore[override]
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):  # type: ignore[override]
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):  # type: ignore[override]
        allowed = _supported_suffixes()
        paths: List[Path] = []

        # File/dir URLs
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                p = Path(url.toLocalFile())
                if not p.exists():
                    continue
                if p.is_dir():
                    paths.extend(_flatten_supported_from_dir(p, allowed=allowed))
                elif _is_supported(p, allowed=allowed):
                    paths.append(p)

        # Text payload (some explorers drop plain text paths)
        if e.mimeData().hasText():
            for line in e.mimeData().text().splitlines():
                p = Path(line.strip())
                if p.exists():
                    if p.is_dir():
                        paths.extend(_flatten_supported_from_dir(p, allowed=allowed))
                    elif _is_supported(p, allowed=allowed):
                        paths.append(p)

        # De-duplicate (preserve order)
        out = [str(p) for p in dict.fromkeys(paths)]
        if out:
            self.pathsDropped.emit(out)

        e.acceptProposedAction()
