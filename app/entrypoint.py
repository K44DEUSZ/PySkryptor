# app/entrypoint.py
from __future__ import annotations

import sys
from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config
from ui.i18n.translator import Translator
from ui.views.main_window import MainWindow


def run() -> int:
    """
    Creates QApplication, initializes config and i18n, shows MainWindow, runs event loop.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # Global configuration and resources + user settings
    Config.initialize()

    # i18n uses language from settings.json
    try:
        Translator.load(Config.RESOURCES_DIR / "locales", lang=Config.language())
    except Exception:
        pass

    # Program main window
    win = MainWindow()
    win.show()

    return app.exec_()
