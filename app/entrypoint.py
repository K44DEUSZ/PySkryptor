# app/entrypoint.py
from __future__ import annotations

import locale
from pathlib import Path
from typing import Optional

from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config
from ui.i18n.translator import Translator
from ui.views.main_window import MainWindow


def _pick_language(locales_dir: Path, preferred: Optional[str]) -> str:
    """
    Choose UI language in order:
    1) preferred from settings.json (e.g. "pl", "en"),
    2) system language (first two letters),
    3) fallback: "en".
    """
    available = set(Translator.available_languages(locales_dir))
    # settings.json preference
    if preferred and preferred in available:
        return preferred

    # system language (e.g. "pl_PL" -> "pl")
    try:
        sys_lang, _ = locale.getdefaultlocale()
    except Exception:
        sys_lang = None
    if sys_lang:
        lang2 = sys_lang.split("_")[0].lower()
        if lang2 in available:
            return lang2

    return "en"


def run() -> int:
    app = QtWidgets.QApplication([])

    # Initialize runtime (paths, ffmpeg, device/dtype) via settings.json
    Config.initialize()

    # Load translations from JSON according to settings/fallbacks
    locales_dir = Config.ROOT_DIR / "resources" / "locales"
    lang = _pick_language(locales_dir, Config.language())
    try:
        Translator.load(locales_dir, lang=lang)
    except Exception:
        try:
            Translator.load(locales_dir, lang="en")
        except Exception:
            pass

    # Main window
    win = MainWindow()
    win.show()
    return app.exec_()
