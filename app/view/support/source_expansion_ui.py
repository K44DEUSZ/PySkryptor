# app/view/support/source_expansion_ui.py
from __future__ import annotations

from collections.abc import Callable

from PyQt5 import QtWidgets

from app.model.config.app_config import AppConfig as Config
from app.model.domain.results import ExpandedSourceItem, SourceExpansionResult
from app.model.services.localization_service import tr
from app.view import dialogs


def ensure_progress_dialog(
    parent: QtWidgets.QWidget,
    dialog: dialogs.ExpansionProgressDialog | None,
    on_cancel: Callable[[], None],
) -> dialogs.ExpansionProgressDialog:
    if dialog is not None:
        return dialog
    dlg = dialogs.ExpansionProgressDialog(parent)
    dlg.cancel_requested.connect(on_cancel)
    return dlg


def show_progress_dialog(dialog: dialogs.ExpansionProgressDialog) -> None:
    dialog.set_message(tr("dialog.expansion_progress.generic"))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def hide_progress_dialog(dialog: dialogs.ExpansionProgressDialog | None) -> None:
    if dialog is None:
        return
    dialog.hide()


def update_progress_dialog_message(
    dialog: dialogs.ExpansionProgressDialog,
    key: str,
    params: dict[str, object] | None = None,
) -> None:
    if not key:
        dialog.set_message(tr("dialog.expansion_progress.generic"))
        return
    dialog.set_message(tr(str(key), **(params or {})))


def sample_titles(result: SourceExpansionResult) -> list[str]:
    out: list[str] = []
    for item in result.items:
        title = str(getattr(item, "title", "") or "").strip()
        if title:
            out.append(title)
    return out


def should_confirm_bulk_add(count: int) -> bool:
    if count <= 0:
        return False
    if not Config.ui_bulk_add_confirmation_enabled():
        return False
    return int(count) >= int(Config.ui_bulk_add_confirmation_threshold())


def limit_items(result: SourceExpansionResult, limit: int) -> tuple[ExpandedSourceItem, ...]:
    lim = int(max(0, int(limit or 0)))
    if lim <= 0:
        return tuple(result.items)
    return tuple(result.items[:lim])
