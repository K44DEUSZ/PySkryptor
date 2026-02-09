# view/widgets/language_combo.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PyQt5 import QtCore, QtWidgets

from model.services.settings_service import SettingsCatalog
from view.utils.translating import Translator


@dataclass(frozen=True)
class LanguageItem:
    code: str
    label: str


def _normalize_code(code: str) -> str:
    return str(code or "").strip().lower().replace("_", "-")


def _tr_key_exists(key: str) -> bool:
    # Translator.tr() returns the key itself when missing.
    return Translator.tr(key) != key


def _language_label(code: str, *, locale_prefix: str) -> str:
    code = _normalize_code(code)
    if not code:
        return ""

    key = f"{locale_prefix}.{code}"
    if _tr_key_exists(key):
        name = Translator.tr(key).strip()
        if name and name.lower() != code:
            return f"{name} ({code})"
        return code

    # Fallback: try Babel (optional dependency).
    try:
        from babel import Locale  # type: ignore

        ui = _normalize_code(Translator.current_language()).split("-", 1)[0] or "en"
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
        return code


class LanguageCombo(QtWidgets.QComboBox):
    """Searchable language picker.

    Labels are primarily sourced from locales via keys:
      <locale_prefix>.<code>

    Pass a codes_provider to reuse this widget for different language sets.
    """

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        *,
        special_first: Optional[Tuple[str, str]] = None,
        codes_provider: Callable[[], List[str]] = lambda: sorted(SettingsCatalog.translation_language_codes()),
        locale_prefix: str = "lang.names",
    ) -> None:
        super().__init__(parent)

        self.setEditable(True)
        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)

        editor = self.lineEdit()
        if editor is not None:
            editor.setClearButtonEnabled(True)

        self._special_first = special_first
        self._codes_provider = codes_provider
        self._locale_prefix = str(locale_prefix or "lang.names")
        self._items: List[LanguageItem] = []
        self.rebuild()

        completer = QtWidgets.QCompleter([it.label for it in self._items], self)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        self.setCompleter(completer)

    def rebuild(self) -> None:
        codes = list(self._codes_provider() or [])
        items = [LanguageItem(code=c, label=_language_label(c, locale_prefix=self._locale_prefix)) for c in codes]
        items = [it for it in items if it.label]
        items.sort(key=lambda x: x.label.lower())

        self._items = []

        if self._special_first is not None:
            label_key, code = self._special_first
            label = Translator.tr(label_key).strip()
            if not label:
                label = str(code)
            self._items.append(LanguageItem(code=str(code), label=label))

        self._items.extend(items)

        self.blockSignals(True)
        try:
            self.clear()
            for it in self._items:
                self.addItem(it.label, _normalize_code(it.code))
        finally:
            self.blockSignals(False)

    def set_code(self, code: str) -> None:
        code_norm = _normalize_code(code)
        idx = self.findData(code_norm)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(code_norm)

    def code(self) -> str:
        data = self.currentData()
        if isinstance(data, str):
            return _normalize_code(data)
        return _normalize_code(self.currentText())
