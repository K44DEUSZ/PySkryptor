# gui/file_drop_list.py
# Stabilny widżet listy plików z obsługą drag&drop i podstawowymi operacjami.

from pathlib import Path
from typing import Iterable

from PyQt5.QtCore import Qt, pyqtSignal, QMimeData
from PyQt5.QtWidgets import QListWidget, QListWidgetItem

# Jeśli chcesz korzystać z Config do rozszerzeń, możesz to rozszerzyć w przyszłości.
# Na razie utrzymujemy prostą listę obsługiwanych rozszerzeń:
SUPPORTED_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm", ".aac", ".ogg"}


class FileDropList(QListWidget):
    files_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self._items: dict[str, QListWidgetItem] = {}

    # ---- Public API ----

    def add_files(self, files: Iterable[Path]) -> None:
        added = False
        for p in files:
            try:
                path = Path(p)
                if not path.exists() or not path.is_file():
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTS:
                    continue
                key = str(path.resolve())
                if key in self._items:
                    continue
                item = QListWidgetItem(key)
                item.setData(Qt.UserRole, key)
                self._items[key] = item
                self.addItem(item)
                added = True
            except Exception:
                # cichutko pomijamy pojedyncze błędne wpisy
                continue
        if added:
            self.files_changed.emit()

    def remove_selected(self) -> None:
        removed = False
        for item in self.selectedItems():
            key = item.data(Qt.UserRole) or item.text().strip()
            self._items.pop(str(key), None)
            self.takeItem(self.row(item))
            removed = True
        if removed:
            self.files_changed.emit()

    def clear(self) -> None:  # noqa: A003 — zgodnie z API Qt
        super().clear()
        self._items.clear()
        self.files_changed.emit()

    def get_file_paths(self) -> list[str]:
        return list(self._items.keys())

    # ---- Drag & Drop ----

    def dragEnterEvent(self, event) -> None:
        if self._has_urls(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._has_urls(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        urls = []
        if self._has_urls(event.mimeData()):
            for url in event.mimeData().urls():
                try:
                    p = Path(url.toLocalFile())
                    urls.append(p)
                except Exception:
                    continue
        if urls:
            self.add_files(urls)
        event.acceptProposedAction()

    @staticmethod
    def _has_urls(mime: QMimeData) -> bool:
        try:
            return mime.hasUrls()
        except Exception:
            return False

    # ---- Klawiatura ----

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.remove_selected()
        else:
            super().keyPressEvent(event)
