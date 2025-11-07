from __future__ import annotations

from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config
from ui.i18n.translator import Translator
from ui.views.main_window import MainWindow


def create_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication([])
    Config.initialize()
    Translator.load(Config.RESOURCES_DIR / "locales", lang="pl")
    return app


def create_main_window() -> MainWindow:
    return MainWindow()
