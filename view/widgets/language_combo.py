# view/widgets/language_combo.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PyQt5 import QtCore, QtWidgets

from model.constants.whisper_languages import whisper_language_codes
from view.utils.translating import Translator


@dataclass(frozen=True)
class LanguageItem:
    code: str
    label: str


def _normalize_code(code: str) -> str:
    return str(code or "").strip().lower().replace("_", "-")


def _language_label(code: str, *, ui_lang: str) -> str:
    code = _normalize_code(code)
    ui = _normalize_code(ui_lang).split("-", 1)[0] or "en"

    try:
        from babel import Locale  # type: ignore

        loc_ui = Locale.parse(ui, sep="-")
        loc_en = Locale.parse("en", sep="-")

        localized = (loc_ui.languages.get(code) or "").strip()
        english = (loc_en.languages.get(code) or "").strip()
        native = ""
        try:
            loc_native = Locale.parse(code, sep="-")
            native = str(loc_native.get_display_name(loc_native)).strip()
        except Exception:
            native = ""

        best = (localized or native or english or code).strip()
        if best and best.lower() != code.lower():
            return f"{best} ({code})"
        return code
    except Exception:
        # If babel is unavailable or mapping is missing: show code only (stable, no crash)
        return code


class LanguageCombo(QtWidgets.QComboBox):
    """Searchable language picker for Whisper ISO codes."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self.setEditable(True)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)

        editor = self.lineEdit()
        if editor is not None:
            editor.setClearButtonEnabled(True)

        self._items: List[LanguageItem] = []
        self.rebuild()

        completer = QtWidgets.QCompleter([it.label for it in self._items], self)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self.setCompleter(completer)

    def rebuild(self) -> None:
        ui_lang = Translator.current_language()
        codes = whisper_language_codes()
        self._items = [LanguageItem(code=c, label=_language_label(c, ui_lang=ui_lang)) for c in codes]
        self._items.sort(key=lambda x: x.label.lower())

        self.blockSignals(True)
        try:
            self.clear()
            for it in self._items:
                self.addItem(it.label, it.code)
        finally:
            self.blockSignals(False)

    def set_code(self, code: str) -> None:
        code = _normalize_code(code)
        idx = self.findData(code)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(code)

    def code(self) -> str:
        data = self.currentData()
        if isinstance(data, str) and data.strip():
            return _normalize_code(data)
        return _normalize_code(self.currentText())
