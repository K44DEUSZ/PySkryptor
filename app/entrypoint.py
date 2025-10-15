# pyskryptor/app/entrypoint.py
from __future__ import annotations

from PyQt5 import QtWidgets
from app.bootstrap import create_app, create_main_window


def run() -> int:
    app: QtWidgets.QApplication = create_app()
    win = create_main_window()
    win.show()
    return app.exec_()
