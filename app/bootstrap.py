# pyskryptor/app/bootstrap.py
from __future__ import annotations

from PyQt5 import QtWidgets
from ui.views.main_window import MainWindow


def create_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication([])
    return app


def create_main_window() -> MainWindow:
    return MainWindow()
