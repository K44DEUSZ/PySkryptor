# app/view/support/source_notice.py
from __future__ import annotations

import logging

from PyQt5 import QtWidgets

from app.model.core.config.config import AppConfig
from app.model.settings.resolution import build_source_rights_notice_payload
from app.model.settings.service import SettingsService
from app.model.settings.validation import SettingsError
from app.view import dialogs


def confirm_source_rights_notice(
    parent: QtWidgets.QWidget,
    *,
    logger: logging.Logger | None = None,
) -> bool:
    """Confirm that the user wants to add a network source under the notice rules."""
    if not AppConfig.ui_source_rights_notice_enabled():
        return True

    decision = dialogs.ask_source_rights_notice(parent)
    if not decision.accepted:
        return False
    if not decision.dont_show_again:
        return True

    try:
        snap = SettingsService().save(build_source_rights_notice_payload(show_on_add=False))
        AppConfig.initialize_from_snapshot(snap)
    except (OSError, RuntimeError, TypeError, ValueError, SettingsError) as ex:
        if logger is not None:
            logger.debug("Source-rights notice preference save skipped. detail=%s", ex)
    return True
