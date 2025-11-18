# app/entrypoint.py
from __future__ import annotations

import sys
from pathlib import Path
from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config
from core.services.settings_service import SettingsService
from ui.i18n.translator import Translator
from ui.views.main_window import MainWindow


def run() -> int:
    project_root = Path(__file__).resolve().parents[1]
    Config.ROOT_DIR = project_root

    ss = SettingsService(project_root)
    Config.initialize(ss)

    locales_dir = Config.ROOT_DIR / "resources" / "locales"
    try:
        Translator.load_best(locales_dir, system_first=True, fallback=Config.language())
    except Exception:
        Translator.load_best(locales_dir, system_first=False, fallback="en")

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(run())
