# ui/widgets/file_drop_list.py
from __future__ import annotations

from pathlib import Path
from typing import List

from PyQt5 import QtCore, QtWidgets

from core.config.app_config import AppConfig as Config


def _is_supported(path: Path) -> bool:
    if not path.is_file():
        return False
    return path.suffix.lower() in (Config.AUDIO_EXT | Config.VIDEO_EXT)


def _flatten_supported_from_dir(dir_path: Path) -> List[Path]:
    files: List[Path] = []
    for p in dir_path.rglob("*"):
        if p.is_file() and _is_supported(p):
            files.append(p)
    return files


class FileDropList(QtWidgets.QListWidget):
    """
    Prosty widżet obsługujący drag&drop plików/folderów.
    Sygnał `pathsDropped` emituje listę ścieżek (str) po odfiltrowaniu
    do wspieranych rozszerzeń z Config.AUDIO_EXT | Config.VIDEO_EXT.
    """
    pathsDropped = QtCore.pyqtSignal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)

    # --- Qt DnD ---

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:  # type: ignore[name-defined]
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent) -> None:  # type: ignore[name-defined]
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QtGui.QDropEvent) -> None:  # type: ignore[name-defined]
        paths: List[Path] = []

        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                p = Path(url.toLocalFile())
                if not p.exists():
                    continue
                if p.is_dir():
                    paths.extend(_flatten_supported_from_dir(p))
                elif _is_supported(p):
                    paths.append(p)

        # (opcjonalnie) tekstowe ścieżki przeciągnięte z eksploratora
        if e.mimeData().hasText():
            for line in e.mimeData().text().splitlines():
                try:
                    p = Path(line.strip())
                except Exception:
                    continue
                if p.exists():
                    if p.is_dir():
                        paths.extend(_flatten_supported_from_dir(p))
                    elif _is_supported(p):
                        paths.append(p)

        # deduplikacja i emisja
        out = [str(p) for p in dict.fromkeys(paths)]
        if out:
            self.pathsDropped.emit(out)
            # opcjonalnie pokaż na liście (widżet bywa ukryty w UI)
            for s in out:
                self.addItem(s)

        e.acceptProposedAction()
