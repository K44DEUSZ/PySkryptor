# app/ui/panels/about_panel.py
from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6 import QtCore, QtWidgets

from app.core.config.config import AppConfig
from app.core.config.meta import AppMeta
from app.core.runtime.localization import tr
from app.ui import dialogs
from app.ui.components.action_button import ActionButton
from app.ui.components.icon_badge import IconBadge
from app.ui.components.license_dialog import LicenseDialog
from app.ui.components.section_group import SectionGroup
from app.ui.support.action_icons import action_icon, refresh_action_icons
from app.ui.support.host_runtime import open_external_url
from app.ui.support.responsive_layout import responsive_box
from app.ui.support.theme_runtime import LogoSvgLabel, logo_svg_path
from app.ui.support.widget_effects import enable_styled_background
from app.ui.support.widget_setup import set_passive_cursor, setup_button, setup_layout
from app.ui.ui_config import ui


class AboutPanel(QtWidgets.QWidget):
  """Application identity, author information and the built-in license reader."""

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self.setObjectName('AboutPanel')
    self.setProperty('uiRole', 'page')
    enable_styled_background(self)
    self._ui = ui(self)
    self._logo: LogoSvgLabel | None = None
    self._license_dialog: LicenseDialog | None = None
    set_passive_cursor(self)
    self._build_ui()

  def _build_ui(self) -> None:
    cfg = self._ui
    outer = QtWidgets.QVBoxLayout(self)
    outer.setContentsMargins(0, 0, 0, 0)
    scroll = QtWidgets.QScrollArea(self)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QtWidgets.QWidget(scroll)
    body.setProperty('uiRole', 'page')
    enable_styled_background(body)
    root = QtWidgets.QVBoxLayout(body)
    setup_layout(root, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_l)
    root.addWidget(self._build_app_group())
    details = QtWidgets.QHBoxLayout()
    details.setContentsMargins(0, 0, 0, 0)
    details.setSpacing(cfg.space_l)
    self.author_group = self._build_author_group()
    self.license_group = self._build_license_group()
    details.addWidget(self.author_group, 1)
    details.addWidget(self.license_group, 1)
    root.addLayout(details)
    root.addStretch(1)
    scroll.setWidget(body)
    body.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    outer.addWidget(scroll)
    self._responsive_details = responsive_box(body, details, 880)

  def _build_app_group(self) -> SectionGroup:
    group = SectionGroup(self, object_name='AboutAppGroup', layout='hbox')
    layout = cast(QtWidgets.QHBoxLayout, group.root)
    setup_layout(layout, cfg=self._ui, margins=(24, 20, 24, 20), spacing=32)
    path = logo_svg_path()
    if path is not None:
      self._logo = LogoSvgLabel(path, object_name='AboutLogo')
      self._logo.update_for_bounds(280, 96)
      layout.addWidget(self._logo)
    label = QtWidgets.QLabel(group)
    label.setWordWrap(True)
    label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
    label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setText(tr('about.app.description').format(
      name=AppMeta.NAME, version=AppMeta.VERSION, author=AppMeta.AUTHOR, years=AppMeta.DEVELOPMENT_YEARS,
    ))
    layout.addStretch(1)
    layout.addWidget(label)
    return group

  def _build_author_group(self) -> SectionGroup:
    group = SectionGroup(self, object_name='AboutAuthorGroup')
    layout = cast(QtWidgets.QVBoxLayout, group.root)
    setup_layout(layout, cfg=self._ui, margins=(20, 20, 20, 20), spacing=16)
    header = QtWidgets.QHBoxLayout()
    caption = QtWidgets.QLabel(tr('about.author.label'))
    caption.setProperty('role', 'overline')
    header.addWidget(caption, 1)
    header.addWidget(IconBadge('kaadeusz', image_path=AppConfig.PATHS.IMAGES_DIR / 'kaadeusz_mark.svg'))
    layout.addLayout(header)
    name = QtWidgets.QLabel(AppMeta.AUTHOR)
    name.setProperty('role', 'authorName')
    layout.addWidget(name)
    description = QtWidgets.QLabel(tr('about.author.description'))
    description.setProperty('role', 'secondaryText')
    description.setWordWrap(True)
    layout.addWidget(description)
    layout.addStretch(1)
    for text, url in (('kaadeusz.pl', AppMeta.AUTHOR_URL), (tr('about.source_code'), AppMeta.REPO_URL)):
      button = ActionButton(action_icon('link'), text)
      setup_button(button, min_w=0)
      button.setProperty('actionRole', 'quiet')
      button.setProperty('contentAlignment', 'leading')
      button.clicked.connect(lambda _checked=False, target=url: open_external_url(target))
      layout.addWidget(button)
    return group

  def _build_license_group(self) -> SectionGroup:
    group = SectionGroup(self, object_name='AboutLicenseGroup')
    layout = cast(QtWidgets.QVBoxLayout, group.root)
    setup_layout(layout, cfg=self._ui, margins=(20, 20, 20, 20), spacing=16)
    header = QtWidgets.QHBoxLayout()
    caption = QtWidgets.QLabel(tr('about.section.license'))
    caption.setProperty('role', 'sectionTitle')
    header.addWidget(caption, 1)
    layout.addLayout(header)
    label = QtWidgets.QLabel('GPL-3.0')
    label.setProperty('role', 'authorName')
    layout.addWidget(label)
    summary = QtWidgets.QLabel(tr('about.license.summary'))
    summary.setProperty('role', 'secondaryText')
    summary.setWordWrap(True)
    summary.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(summary)
    layout.addStretch(1)
    self.btn_license = ActionButton(action_icon('file'), tr('about.license.read_full'))
    setup_button(self.btn_license, min_w=0)
    self.btn_license.setProperty('actionRole', 'primary')
    self.btn_license.clicked.connect(self._open_license_file)
    layout.addWidget(self.btn_license)
    self.btn_notices = ActionButton(action_icon('info'), tr('about.notices.label'))
    setup_button(self.btn_notices, min_w=0)
    self.btn_notices.clicked.connect(self._open_notices_file)
    layout.addWidget(self.btn_notices)
    return group

  def _show_document(self, index: int) -> None:
    path = (Path(AppConfig.PATHS.LICENSE_FILE) if index == 0
            else AppConfig.PATHS.INSTALL_ROOT_DIR / 'THIRD_PARTY_NOTICES.txt')
    if not path.is_file():
      key = 'about.license.missing' if index == 0 else 'about.notices.open_failed'
      dialogs.show_error(self, title=tr('dialog.error.title'), message=tr(key))
      return
    if self._license_dialog is None:
      self._license_dialog = LicenseDialog(self.window())
      self._license_dialog.destroyed.connect(self._clear_license_dialog)
    self._license_dialog.set_document(index)
    self._license_dialog.show()
    self._license_dialog.raise_()
    self._license_dialog.activateWindow()
    refresh_action_icons(self._license_dialog)

  def _clear_license_dialog(self, *_args) -> None:
    self._license_dialog = None

  def _open_license_file(self) -> None:
    self._show_document(0)

  def _open_notices_file(self) -> None:
    self._show_document(1)

  def on_parent_close(self) -> None:
    if self._license_dialog is not None:
      self._license_dialog.close()
