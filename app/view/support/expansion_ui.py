# app/view/support/expansion_ui.py
from __future__ import annotations

from collections.abc import Callable

from PyQt5 import QtWidgets

from app.model.core.config.config import AppConfig
from app.model.core.domain.results import ExpandedSourceItem, SourceExpansionResult
from app.model.core.runtime.localization import tr
from app.view import dialogs


def ensure_progress_dialog(
    parent: QtWidgets.QWidget,
    dialog: dialogs.ExpansionProgressDialog | None,
    on_cancel: Callable[[], None],
) -> dialogs.ExpansionProgressDialog:
    """Return the existing expansion dialog or create a new one."""
    if dialog is not None:
        return dialog
    dlg = dialogs.ExpansionProgressDialog(parent)
    dlg.cancel_requested.connect(on_cancel)
    return dlg


def show_progress_dialog(dialog: dialogs.ExpansionProgressDialog) -> None:
    """Show the expansion dialog with the default localized message."""
    dialog.set_message(tr("dialog.expansion_progress.generic"))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def hide_progress_dialog(dialog: dialogs.ExpansionProgressDialog | None) -> None:
    """Hide the expansion dialog when it is available."""
    if dialog is None:
        return
    dialog.hide()


def update_progress_dialog_message(
    dialog: dialogs.ExpansionProgressDialog,
    key: str,
    params: dict[str, object] | None = None,
) -> None:
    """Update the expansion dialog message from a localization key."""
    if not key:
        dialog.set_message(tr("dialog.expansion_progress.generic"))
        return
    dialog.set_message(tr(str(key), **(params or {})))


def sample_expansion_titles(result: SourceExpansionResult) -> list[str]:
    """Collect non-empty item titles from an expansion result."""
    out: list[str] = []
    for item in result.items:
        title = str(getattr(item, "title", "") or "").strip()
        if title:
            out.append(title)
    return out


def should_confirm_bulk_add(count: int) -> bool:
    """Return whether bulk-add confirmation should be shown."""
    if count <= 0:
        return False
    if not AppConfig.ui_bulk_add_confirmation_enabled():
        return False
    return int(count) >= int(AppConfig.ui_bulk_add_confirmation_threshold())


def limit_expansion_items(result: SourceExpansionResult, limit: int) -> tuple[ExpandedSourceItem, ...]:
    """Return the expansion items truncated to the requested limit."""
    lim = int(max(0, int(limit or 0)))
    if lim <= 0:
        return tuple(result.items)
    return tuple(result.items[:lim])
