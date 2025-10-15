# gui/file_drop_list.py
# Jedna lista dla plików lokalnych i URL-i. Wspiera drag&drop plików, folderów (rekurencyjnie) oraz drop tekstu/URL.
# API:
#  - add_entry(str_path_or_url) -> (bool, message)
#  - add_files(iterable[Path | str])  # może zawierać pliki i foldery
#  - remove_selected(), clear()
#  - get_entries() -> list[{'type':'file'|'url', 'value': str}]
#  - files_changed (signal)

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple, List

from PyQt5.QtCore import Qt, pyqtSignal, QMimeData
from PyQt5.QtWidgets import QListWidget, QListWidgetItem

SUPPORTED_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".webm", ".aac", ".ogg"}


def _is_url(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("http://") or t.startswith("https://")


def _is_supported_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXTS


class FileDropList(QListWidget):
    files_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self._items: dict[str, QListWidgetItem] = {}

    # ---- Public API ----

    def add_entry(self, text: str) -> Tuple[bool, str]:
        """
        Dodaje pojedynczy wpis:
        - URL -> element [URL] ...
        - plik -> element [LOCAL] ...
        - folder -> rekurencyjnie dodaje wspierane pliki z folderu (z informacją zwrotną)
        """
        text = (text or "").strip()
        if not text:
            return False, "Pusty wpis."

        if _is_url(text):
            key = f"url::{text}"
            if key in self._items:
                return False, "URL już na liście."
            item = QListWidgetItem(f"[URL] {text}")
            item.setData(Qt.UserRole, {"type": "url", "value": text})
            self._items[key] = item
            self.addItem(item)
            self.files_changed.emit()
            return True, text

        # Lokalna ścieżka
        p = Path(text)
        if p.is_dir():
            added = self._add_dir_recursive(p)
            if added > 0:
                self.files_changed.emit()
                return True, f"Dodano {added} plików z folderu."
            else:
                return False, "Brak wspieranych plików w folderze."
        elif p.is_file():
            if not _is_supported_file(p):
                return False, f"Nieobsługiwane rozszerzenie: {p.suffix}"
            key = f"file::{str(p.resolve())}"
            if key in self._items:
                return False, "Plik już na liście."
            item = QListWidgetItem(f"[LOCAL] {p}")
            item.setData(Qt.UserRole, {"type": "file", "value": str(p)})
            self._items[key] = item
            self.addItem(item)
            self.files_changed.emit()
            return True, str(p)
        else:
            return False, "Plik/folder nie istnieje."

    def add_files(self, files: Iterable[Path | str]) -> None:
        """
        Przyjmuje listę plików i/lub folderów (Path lub str).
        """
        changed = False
        for f in files:
            ok, _ = self.add_entry(str(f))
            changed = changed or ok
        if changed:
            self.files_changed.emit()

    def remove_selected(self) -> None:
        changed = False
        for item in self.selectedItems():
            data = item.data(Qt.UserRole) or {}
            typ = data.get("type")
            val = data.get("value")
            if typ and val:
                key = f"{typ}::{val}"
                self._items.pop(key, None)
            self.takeItem(self.row(item))
            changed = True
        if changed:
            self.files_changed.emit()

    def clear(self) -> None:  # noqa: A003
        super().clear()
        self._items.clear()
        self.files_changed.emit()

    def get_entries(self) -> list:
        out = []
        for _, item in self._items.items():
            data = item.data(Qt.UserRole) or {}
            typ = data.get("type")
            val = data.get("value")
            if typ and val:
                out.append({"type": typ, "value": val})
        return out

    # ---- Drag & Drop ----

    def dragEnterEvent(self, event) -> None:
        if self._has_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._has_payload(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        changed = False

        # Pliki/foldery
        if mime.hasUrls():
            for url in mime.urls():
                local = url.toLocalFile()
                if local:
                    ok, _ = self.add_entry(local)
                    changed = changed or ok

        # Tekst/URL (wielolinijkowy)
        if mime.hasText():
            text = (mime.text() or "").strip()
            if text:
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    ok, _ = self.add_entry(line)
                    changed = changed or ok

        if changed:
            self.files_changed.emit()

        event.acceptProposedAction()

    @staticmethod
    def _has_payload(mime: QMimeData) -> bool:
        try:
            return mime.hasUrls() or (mime.hasText() and bool((mime.text() or "").strip()))
        except Exception:
            return False

    # ---- Helpers ----

    def _add_dir_recursive(self, folder: Path) -> int:
        count = 0
        for f in folder.rglob("*"):
            if _is_supported_file(f):
                key = f"file::{str(f.resolve())}"
                if key in self._items:
                    continue
                item = QListWidgetItem(f"[LOCAL] {f}")
                item.setData(Qt.UserRole, {"type": "file", "value": str(f)})
                self._items[key] = item
                self.addItem(item)
                count += 1
        return count

    # ---- Klawiatura ----

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.remove_selected()
        else:
            super().keyPressEvent(event)
