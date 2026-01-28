# ui/utils/logging.py
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PyQt5 import QtCore, QtGui


# ----- Qt HTML appender -----

class _QtHtmlAppender(QtCore.QObject):
    """
    Thread-safe HTML appender for QTextEdit/QTextBrowser.

    Workers (in other threads) can call methods on QtHtmlLogSink; internally
    we marshal to the GUI thread via these signals.
    """
    append_html = QtCore.pyqtSignal(str)
    clear_all = QtCore.pyqtSignal()

    def __init__(self, doc_widget) -> None:
        super().__init__(doc_widget)
        self._w = doc_widget  # QTextEdit/QTextBrowser
        self.append_html.connect(self._on_append_html, QtCore.Qt.QueuedConnection)
        self.clear_all.connect(self._on_clear, QtCore.Qt.QueuedConnection)

        # Make each paragraph a *single visual line* with tiny vertical gap.
        try:
            self._w.document().setDefaultStyleSheet(
                "p.logline{margin:0 0 2px 0;}"
                "hr{margin:4px 0;}"
            )
        except Exception:
            pass

    def _on_append_html(self, html: str) -> None:
        cursor = self._w.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertHtml(html)
        cursor.insertBlock()
        self._w.setTextCursor(cursor)
        self._w.ensureCursorVisible()

    def _on_clear(self) -> None:
        self._w.clear()


def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _href_for_path(path: Path) -> str:
    return QtCore.QUrl.fromLocalFile(str(Path(path))).toString()


# ----- GUI sink -----

class QtHtmlLogSink:
    """
    Log builder for GUI.
    """

    def __init__(self, text_widget) -> None:
        # text_widget: QTextBrowser (preferred) or QTextEdit
        self._appender = _QtHtmlAppender(text_widget)

    # ----- public API -----

    def clear(self) -> None:
        self._appender.clear_all.emit()

    def plain(self, text: str) -> None:
        self._emit_line(_escape_html(text))

    def info(self, text: str) -> None:
        self._emit_line(_escape_html(text))

    def ok(self, text: str) -> None:
        self._emit_line(_escape_html(text))

    def warn(self, text: str) -> None:
        self._emit_line(_escape_html(text))

    def err(self, text: str) -> None:
        self._emit_line(_escape_html(text))

    def line_with_link(self, prefix: str, path: Path, *, title: Optional[str] = None, icon: str = "") -> None:
        """
        Print one logical line in the form:
        "<icon><prefix> <a href='file://...'>title</a>"
        """
        t = _escape_html(title or Path(path).stem)
        href = _href_for_path(path)
        pref = _escape_html(prefix)
        if icon:
            pref = icon + " " + pref
        self._emit_line(f"{pref} <a href=\"{href}\">{t}</a>")

    # Backwards compatibility: a standalone link line.
    def link(self, title: str, path: Path, *, prefix: Optional[str] = None) -> None:
        txt = ""
        if prefix:
            txt = _escape_html(prefix) + " "
        href = _href_for_path(path)
        self._emit_line(f"{txt}<a href=\"{href}\">{_escape_html(title or Path(path).stem)}</a>")

    def hr(self) -> None:
        # Horizontal rule is its own block.
        self._appender.append_html.emit("<hr/>")

    # ----- internal helpers -----

    def _emit_line(self, inner_html: str) -> None:
        # Wrap inner HTML in a paragraph so CSS can control spacing.
        self._appender.append_html.emit(f"<p class='logline'>{inner_html}</p>")


# ----- Convenience factories -----

def gui_logger(text_sink: QtHtmlLogSink) -> Callable[[str], None]:
    """
    Return a simple callable that appends plain messages to the GUI sink.
    Good for piping into DownloadService/YTDLP when you don't need formatting.
    """
    def _log(msg: str) -> None:
        text_sink.plain(str(msg))
    return _log
