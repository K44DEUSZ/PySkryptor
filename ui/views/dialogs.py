# pyskryptor/ui/views/dialogs.py
from __future__ import annotations

from PyQt5 import QtWidgets
from core.config import Config
from core.files.file_manager import FileManager


def ask_cancel(parent: QtWidgets.QWidget) -> bool:
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Przerwać transkrypcję?")
    box.setText("Czy na pewno chcesz natychmiast przerwać bieżącą transkrypcję?\n\nTo przerwie aktualnie przetwarzany plik i pominie pozostałe.")
    yes_btn = box.addButton("Tak, przerwij teraz", QtWidgets.QMessageBox.DestructiveRole)
    no_btn = box.addButton("Nie", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(no_btn)
    box.exec_()
    return box.clickedButton() is yes_btn


def ask_conflict(parent: QtWidgets.QWidget, stem: str) -> tuple[str, str, bool]:
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Istniejący wynik")
    box.setText(f"Istnieje już folder wynikowy dla „{stem}”.\n\n{Config.OUTPUT_DIR / stem}\n\nJak chcesz postąpić?")
    skip_btn = box.addButton("Pomiń", QtWidgets.QMessageBox.RejectRole)
    new_btn = box.addButton("Utwórz wersję (1)", QtWidgets.QMessageBox.ActionRole)
    overwrite_btn = box.addButton("Nadpisz", QtWidgets.QMessageBox.DestructiveRole)
    box.setDefaultButton(new_btn)
    apply_all_cb = QtWidgets.QCheckBox("Zastosuj dla pozostałych")
    box.setCheckBox(apply_all_cb)
    box.exec_()

    if box.clickedButton() is skip_btn:
        return "skip", "", apply_all_cb.isChecked()
    elif box.clickedButton() is overwrite_btn:
        return "overwrite", "", apply_all_cb.isChecked()
    else:
        i = 1
        candidate = f"{stem} ({i})"
        while FileManager.output_dir_for(candidate).exists():
            i += 1
            candidate = f"{stem} ({i})"
        return "new", candidate, apply_all_cb.isChecked()
