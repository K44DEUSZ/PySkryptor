# app/entrypoint.py
from __future__ import annotations

import sys
from PyQt5 import QtWidgets

from core.config.app_config import AppConfig as Config, ConfigError
from ui.i18n.translator import Translator
from ui.views.dialogs import ask_restore_defaults
from ui.views.main_window import MainWindow


def run() -> int:
    """
    Application entrypoint:
    - Create QApplication
    - Bootstrap translations (system language → fallback to EN)
    - Initialize configuration (settings-first)
    - On config error: offer to restore from defaults.json
    - Reload translations using user's language and show the main window
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    # Early i18n so error dialogs are localized
    Translator.load_best(Config.ROOT_DIR / "resources" / "locales")

    try:
        Config.initialize()
    except ConfigError as e:
        wants_restore = ask_restore_defaults(str(Config._DEFAULTS_PATH), e.params.get("detail", ""))
        if wants_restore:
            try:
                Config.restore_settings_from_defaults()
                Config.initialize()
            except Exception as ex2:
                QtWidgets.QMessageBox.critical(
                    None,
                    Translator.tr("app.title"),
                    Translator.tr("error.config.generic", detail=str(ex2)),
                )
                return 1
        else:
            return 1

    # Reload translations using the user's language after successful init
    Translator.load_best(Config.RESOURCES_DIR / "locales", system_first=False, fallback=Config.language())

    win = MainWindow()
    win.show()
    return app.exec_()
