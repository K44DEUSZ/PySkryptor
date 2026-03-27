# app/view/panels/tab_specs.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PyQt5 import QtWidgets

from app.model.services.localization_service import tr
from app.view.panels.about_panel import AboutPanel
from app.view.panels.downloader_panel import DownloaderPanel
from app.view.panels.files_panel import FilesPanel
from app.view.panels.live_panel import LivePanel
from app.view.panels.settings_panel import SettingsPanel


class PanelFactory(Protocol):
    def __call__(self, parent: QtWidgets.QWidget | None) -> QtWidgets.QWidget: ...


@dataclass(frozen=True)
class PanelTabSpec:
    """Immutable tab descriptor used to build the main window panels."""

    key: str
    title_key: str
    factory: PanelFactory

    def title(self) -> str:
        return tr(self.title_key)


def build_main_tab_specs() -> tuple[PanelTabSpec, ...]:
    """Return the ordered tab specifications for the main window."""
    return (
        PanelTabSpec(key='files', title_key='tabs.files', factory=lambda parent: FilesPanel(parent)),
        PanelTabSpec(key='live', title_key='tabs.live', factory=lambda parent: LivePanel(parent)),
        PanelTabSpec(key='downloader', title_key='tabs.downloader', factory=lambda parent: DownloaderPanel(parent)),
        PanelTabSpec(key='settings', title_key='tabs.settings', factory=lambda parent: SettingsPanel(parent)),
        PanelTabSpec(key='about', title_key='tabs.about', factory=lambda parent: AboutPanel(parent)),
    )
