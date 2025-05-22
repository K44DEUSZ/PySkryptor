from PyQt5.QtWidgets import QListWidget, QListWidgetItem
from PyQt5.QtCore import Qt
from pathlib import Path

from core.config import Config

class FileDropList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self._items: dict[str, QListWidgetItem] = {}

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            return event.ignore()
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in Config.AUDIO_EXT + Config.VIDEO_EXT:
                self.add_file(str(path))
        event.acceptProposedAction()

    def add_file(self, path: str):
        if path not in self._items:
            item = QListWidgetItem(path)
            self._items[path] = item
            self.addItem(item)

    def get_file_paths(self) -> list[str]:
        return list(self._items.keys())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            for item in self.selectedItems():
                path = item.text().strip()
                self.takeItem(self.row(item))
                self._items.pop(path, None)
        else:
            super().keyPressEvent(event)
