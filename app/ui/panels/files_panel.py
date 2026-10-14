# app/ui/panels/files_panel.py
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, cast

from PyQt6 import QtCore, QtGui, QtWidgets

from app.application.panel_protocols import FilesCoordinatorProtocol
from app.core.config.config import AppConfig
from app.core.config.policy import LanguagePolicy
from app.core.domain.entities import TranscriptionSessionRequest
from app.core.domain.results import ExpandedSourceItem, SourceExpansionResult
from app.core.domain.state import AppRuntimeState
from app.core.runtime.localization import current_language, tr
from app.core.utils.string_utils import format_hms
from app.features.download.domain import SourceAccessInterventionResolution
from app.features.download.policy import DownloadPolicy
from app.features.settings.resolution import (
  build_files_quick_options_payload,
  build_transcription_session_request,
  transcription_output_modes,
  translation_runtime_available,
)
from app.features.sources.duplicates import (
  SourceDuplicateRecord,
  evaluate_source_duplicate,
  is_duplicate_terminal_status,
)
from app.features.sources.parser import build_entries, parse_source_input
from app.features.sources.probe import is_url_source
from app.runtime.resolution import EngineCatalog
from app.runtime.types import EngineRuntimeState
from app.ui import dialogs
from app.ui.components.action_button import ActionButton
from app.ui.components.app_dialog import AppDialog
from app.ui.components.choice_toggle import ChoiceToggle
from app.ui.components.collapsible_section import CollapsibleSection
from app.ui.components.file_dialog import AppFileDialog
from app.ui.components.popup_combo import (
  LanguageCombo,
  PopupComboBox,
  combo_current_code,
  rebuild_code_combo,
  set_combo_data,
)
from app.ui.components.progress_action_bar import ProgressActionBar
from app.ui.components.runtime_badge import RuntimeBadgeWidget
from app.ui.components.section_group import SectionGroup
from app.ui.components.source_table import SourceTable
from app.ui.support.action_icons import action_icon
from app.ui.support.dialog_positioning import center_dialog, dialog_screen
from app.ui.support.expansion_ui import (
  ensure_progress_dialog,
  hide_progress_dialog,
  limit_expansion_items,
  sample_expansion_titles,
  should_confirm_bulk_add,
  show_progress_dialog,
  update_progress_dialog_message,
)
from app.ui.support.host_runtime import (
  connect_network_status_changed,
  normalize_network_status,
  open_external_url,
  open_local_path,
  read_network_status,
)
from app.ui.support.language_options import (
  build_source_language_items,
  build_target_language_items,
  effective_source_language_code,
  effective_target_language_code,
  resolve_source_language_selection,
  resolve_target_language_selection,
  supported_source_language_codes,
  supported_target_language_codes,
)
from app.ui.support.options_autosave import OptionsAutosave
from app.ui.support.responsive_layout import ResponsiveLayout, responsive_input_row
from app.ui.support.source_notice import confirm_source_rights_notice
from app.ui.support.source_probe_presenter import (
  build_probe_error_presentation,
  build_probe_success_presentation,
)
from app.ui.support.status_presenter import (
  RuntimePresentation,
  build_runtime_presentation,
  build_static_runtime_presentation,
  compose_status_text,
  display_texts_for_statuses,
  is_active_work_status,
  is_terminal_status,
  normalize_status_base_key,
  status_display_text,
)
from app.ui.support.theme_runtime import active_theme_key, status_icon
from app.ui.support.widget_effects import enable_styled_background
from app.ui.support.widget_setup import (
  build_field_stack,
  build_layout_host,
  make_grid,
  set_passive_cursor,
  setup_button,
  setup_combo,
  setup_icon_action,
  setup_input,
  setup_layout,
  setup_option_checkbox,
)
from app.ui.ui_config import ui

_LOG = logging.getLogger(__name__)


class FilesPanel(QtWidgets.QWidget):
  """Files tab: manage sources and batch transcription/translation."""

  _cancel_notice_pending: bool
  _was_cancelled: bool
  _conflict_apply_all_action: str | None
  _conflict_apply_all_new_base: str | None
  _transcription_ready: bool
  _transcription_error_key: str | None
  _transcription_error_params: dict[str, Any]
  _translation_ready: bool
  _translation_error_key: str | None
  _translation_error_params: dict[str, Any]

  COL_CHECK = 0
  COL_NO = 1
  COL_TITLE = 2
  COL_DUR = 3
  COL_SRC = 4
  COL_LANG = 5
  COL_PATH = 6
  COL_STATUS = 7
  COL_PREVIEW = 8

  @staticmethod
  def _status_icon(key: str, fallback: QtGui.QIcon) -> QtGui.QIcon:
    app = QtWidgets.QApplication.instance()
    qt_app = app if isinstance(app, QtWidgets.QApplication) else None
    try:
      icon = status_icon(key, theme=active_theme_key(app=qt_app))
      if not icon.isNull():
        return icon
    except (AttributeError, RuntimeError, TypeError, ValueError):
      return fallback
    return fallback

  def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
    super().__init__(parent)
    self.setObjectName("FilesPanel")
    self.setProperty("uiRole", "page")
    enable_styled_background(self)
    self._ui = ui(self)
    set_passive_cursor(self)
    self._panel_coordinator: FilesCoordinatorProtocol | None = None

    self._init_state()
    self._build_ui()
    self._wire_signals()
    self._restore_initial_state()

  def bind_coordinator(self, coordinator: FilesCoordinatorProtocol) -> None:
    self._panel_coordinator = coordinator

  def coordinator(self) -> FilesCoordinatorProtocol | None:
    return self._panel_coordinator

  def _coordinator_is_expanding(self) -> bool:
    coord = self.coordinator()
    return bool(coord is not None and coord.is_expanding())

  def _coordinator_is_transcribing(self) -> bool:
    coord = self.coordinator()
    return bool(coord is not None and coord.is_transcribing())

  def _coordinator_is_options_save_running(self) -> bool:
    coord = self.coordinator()
    return bool(coord is not None and coord.is_options_save_running())

  def _coordinator_is_probe_running(self) -> bool:
    coord = self.coordinator()
    return bool(coord is not None and coord.is_probe_running())

  def _quick_options_save_is_busy(self) -> bool:
    return self._coordinator_is_transcribing() or self._coordinator_is_options_save_running()

  def _new_row_id(self) -> str:
    self._row_seq += 1
    return f"files-row-{self._row_seq}"

  def _row_id_at(self, row: int) -> str:
    return self.tbl_sources.internal_key_at(row, self.COL_PATH)

  def _row_for_row_id(self, row_id: str) -> int:
    return self.tbl_sources.row_for_internal_key(self.COL_PATH, str(row_id or "").strip())

  def _source_key_for_row_id(self, row_id: str) -> str:
    return str(self._source_key_by_row_id.get(str(row_id), "") or "").strip()

  def _runtime_key_for_row_id(self, row_id: str) -> str:
    row_key = str(row_id or "").strip()
    runtime_key = str(self._runtime_key_by_row_id.get(row_key, "") or "").strip()
    if runtime_key:
      return runtime_key
    return self._source_key_for_row_id(row_key)

  def _runtime_row_ids(self, runtime_key: str) -> list[str]:
    return list(self._row_ids_by_runtime_key.get(str(runtime_key or "").strip(), ()))

  def _row_id_for_runtime_key(self, runtime_key: str) -> str:
    candidates = self._runtime_row_ids(runtime_key)
    for row_id in reversed(candidates):
      if not self._is_row_completed(row_id):
        return row_id
    return candidates[-1] if candidates else ""

  def _bind_runtime_key(self, row_id: str, runtime_key: str) -> None:
    row_key = str(row_id or "").strip()
    bound_key = str(runtime_key or "").strip()
    if not row_key or not bound_key:
      return
    bucket = self._row_ids_by_runtime_key.setdefault(bound_key, [])
    if row_key not in bucket:
      bucket.append(row_key)
    self._runtime_key_by_row_id[row_key] = bound_key

  def _unbind_runtime_key(self, row_id: str, runtime_key: str) -> None:
    row_key = str(row_id or "").strip()
    bound_key = str(runtime_key or "").strip()
    if not row_key or not bound_key:
      return
    bucket = self._row_ids_by_runtime_key.get(bound_key)
    if not bucket:
      return
    self._row_ids_by_runtime_key[bound_key] = [candidate for candidate in bucket if candidate != row_key]
    if not self._row_ids_by_runtime_key[bound_key]:
      self._row_ids_by_runtime_key.pop(bound_key, None)

  def _replace_runtime_key(self, row_id: str, new_runtime_key: str) -> None:
    row_key = str(row_id or "").strip()
    if not row_key:
      return
    old_runtime_key = self._runtime_key_for_row_id(row_key)
    self._unbind_runtime_key(row_key, old_runtime_key)
    self._bind_runtime_key(row_key, new_runtime_key)

  def _row_duplicate_records(self) -> list[SourceDuplicateRecord]:
    records: list[SourceDuplicateRecord] = []
    for row_id, source_key in self._source_key_by_row_id.items():
      key = str(source_key or "").strip()
      if not key:
        continue
      records.append(
        SourceDuplicateRecord(
          source_key=key,
          is_terminal=self._is_row_duplicate_terminal(row_id),
        )
      )
    return records

  def _can_add_source_key(self, source_key: str) -> tuple[bool, bool]:
    decision = evaluate_source_duplicate(
      self._row_duplicate_records(),
      source_key,
    )
    return bool(decision.allow), bool(decision.duplicate)

  def _is_row_duplicate_terminal(self, row_id: str) -> bool:
    status_key = str(self._status_base_by_row_id.get(str(row_id), "") or "").strip()
    return bool(status_key and is_duplicate_terminal_status(status_key))

  def _is_row_completed(self, row_id: str) -> bool:
    status_key = str(self._status_base_by_row_id.get(str(row_id), "") or "").strip()
    return status_key in {"status.done", "status.saved"}

  def _transcription_row_ids(self) -> list[str]:
    row_ids: list[str] = []
    for row in range(self.tbl_sources.rowCount()):
      row_id = self._row_id_at(row)
      if not row_id or self._is_row_completed(row_id):
        continue
      row_ids.append(row_id)
    return row_ids

  def _init_state(self) -> None:
    self._was_cancelled: bool = False
    self._cancel_notice_pending: bool = False
    self._conflict_apply_all_action: str | None = None
    self._conflict_apply_all_new_base: str | None = None

    self._transcription_ready: bool = False
    self._translation_ready: bool = False
    self._transcription_error_key: str | None = None
    self._transcription_error_params: dict[str, Any] = {}
    self._translation_error_key: str | None = None
    self._translation_error_params: dict[str, Any] = {}

    self._row_seq = 0
    self._source_key_by_row_id: dict[str, str] = {}
    self._runtime_key_by_row_id: dict[str, str] = {}
    self._row_ids_by_runtime_key: dict[str, list[str]] = {}
    self._source_kind_by_row_id: dict[str, str] = {}
    self._display_path_by_row_id: dict[str, str] = {}
    self._audio_track_by_row_id: dict[str, str | None] = {}
    self._transcript_by_row_id: dict[str, str] = {}
    self._status_base_by_row_id: dict[str, str] = {}
    self._pct_by_row_id: dict[str, int] = {}
    self._error_by_row_id: dict[str, tuple[str, dict[str, Any]]] = {}
    self._output_dir_by_row_id: dict[str, str] = {}

    self._network_status = read_network_status(self.parentWidget())
    self._session_target_language = LanguagePolicy.PREFERRED
    self._expansion_progress_dialog: dialogs.ExpansionProgressDialog | None = None
    self._session_source_language = LanguagePolicy.PREFERRED
    self._options_dialog: AppDialog | None = None

  def _build_ui(self) -> None:
    cfg = self._ui
    root = QtWidgets.QVBoxLayout(self)
    setup_layout(root, cfg=cfg, margins=(0, cfg.spacing, 0, 0), spacing=cfg.spacing)
    base_h = cfg.control_min_h

    self.model_info = RuntimeBadgeWidget(self)
    loading_presentation = build_static_runtime_presentation(
      text=tr("files.runtime.status_loading"),
      state="loading",
      icon_name="status_loading",
    )
    self.model_info.set_summary_presentation(loading_presentation)
    self.model_info.set_summary_icon(self._icon_for_runtime_presentation(loading_presentation))
    self.model_info.set_device_value(tr("common.na"))
    neutral_presentation = build_static_runtime_presentation(text=tr("common.na"), state="neutral")
    self.model_info.set_asr_presentation(neutral_presentation)
    self.model_info.set_translation_presentation(neutral_presentation)

    self._build_top_section(root, base_h)
    self._main_row_host = QtWidgets.QWidget(self)
    work_layout = QtWidgets.QHBoxLayout(self._main_row_host)
    self._work_layout = work_layout
    setup_layout(work_layout, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_l)
    queue_host = QtWidgets.QWidget()
    queue_layout = QtWidgets.QVBoxLayout(queue_host)
    setup_layout(queue_layout, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)
    self._build_details_section(queue_layout)
    self._build_action_bar(queue_layout, base_h)
    work_layout.addWidget(queue_host, 1)
    self._build_main_row(work_layout, base_h)
    root.addWidget(self._main_row_host, 1)
    self._responsive_work = ResponsiveLayout(self._main_row_host, self._reflow_work_area)

  def _build_top_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
    cfg = self._ui
    self._top_section_host = QtWidgets.QWidget(self)
    top_grid = make_grid(4, cfg)
    top_grid.setVerticalSpacing(cfg.space_l)
    self._top_section_host.setLayout(top_grid)

    self.ed_source_input = QtWidgets.QLineEdit()
    self.ed_source_input.setObjectName("FilesSourceInput")
    setup_input(self.ed_source_input, placeholder=tr("files.placeholder"), min_h=base_h)

    self.btn_src_add = ActionButton(action_icon("controls.add"), tr("controls.add"))
    self.btn_src_add.setObjectName("FilesAddSource")
    setup_button(self.btn_src_add, min_h=base_h, min_w=cfg.control_min_w)

    self.btn_open_output = ActionButton(action_icon("files.open_output"), tr("files.open_output"))
    self.btn_open_output.setObjectName("FilesOpenOutput")
    setup_button(self.btn_open_output, min_h=base_h, min_w=cfg.control_min_w)
    self.btn_open_output.setProperty('actionRole', 'quiet')
    self.btn_open_output.setMinimumWidth(self.btn_open_output.sizeHint().width() + cfg.space_s)

    top_btn_host, top_btn_box = build_layout_host(
      parent=self._top_section_host,
      layout="hbox",
      margins=(0, 0, 0, 0),
      spacing=cfg.space_l,
    )
    self.btn_src_add.setFixedWidth(110)
    top_btn_box.addWidget(self.btn_src_add)
    top_btn_box.addStretch(1)
    top_btn_box.addWidget(self.btn_open_output)

    top_grid.addWidget(self.ed_source_input, 0, 0, 1, 3)
    top_grid.addWidget(top_btn_host, 0, 3)
    self._responsive_top = responsive_input_row(
      self._top_section_host, top_grid, self.ed_source_input, top_btn_host,
    )

    self.btn_add_files = ActionButton(action_icon("files.add_files"), tr("files.add_files"))
    self.btn_add_folder = ActionButton(action_icon("files.add_folder"), tr("files.add_folder"))
    self.btn_open_source = ActionButton(action_icon("controls.open_source"), tr("controls.open_source"))
    self.btn_refresh_status = ActionButton(
      action_icon("controls.refresh_status"),
      tr("controls.refresh_status"),
    )
    self.btn_remove_selected = ActionButton(
      action_icon("files.remove_selected"),
      tr("files.remove_selected"),
    )
    self.btn_clear_list = ActionButton(action_icon("files.clear"), tr("files.clear"))
    for button in (self.btn_open_source, self.btn_refresh_status, self.btn_remove_selected, self.btn_clear_list):
      button.setProperty('actionRole', 'quiet')

    for button in (
      self.btn_add_files,
      self.btn_add_folder,
      self.btn_open_source,
      self.btn_refresh_status,
      self.btn_remove_selected,
      self.btn_clear_list,
    ):
      setup_button(button, min_h=base_h, min_w=cfg.control_min_w)

    actions_host, actions_box = build_layout_host(
      parent=self._top_section_host,
      layout="hbox",
      margins=(12, 8, 12, 8),
      spacing=8,
    )
    actions_host.setObjectName('QueueToolbar')
    self._queue_actions_host = actions_host
    self.btn_add_files.setProperty('actionRole', 'quiet')
    self.btn_add_folder.setProperty('actionRole', 'quiet')
    actions_box.addWidget(self.btn_add_files)
    actions_box.addWidget(self.btn_add_folder)
    actions_box.addStretch(1)
    for button in (self.btn_open_source, self.btn_refresh_status, self.btn_remove_selected, self.btn_clear_list):
      setup_icon_action(button)
      actions_box.addWidget(button)

    root.addWidget(self._top_section_host)

  def _build_details_section(self, root: QtWidgets.QVBoxLayout) -> None:
    details_group = SectionGroup(self, object_name="FilesDetailsGroup")
    details_layout = cast(QtWidgets.QVBoxLayout, details_group.root)
    details_layout.setContentsMargins(0, 0, 0, 0)
    details_layout.setSpacing(0)
    details_layout.addWidget(self._queue_actions_host)

    self.tbl_sources = SourceTable()
    self.tbl_sources.set_empty_text(tr("files.empty"))
    self.tbl_sources.set_empty_title(tr('desktop.queue_empty'))
    self.tbl_sources.setObjectName("SourcesTable")
    self.tbl_sources.setColumnCount(9)
    self.tbl_sources.setHorizontalHeaderLabels(
      [
        "",
        "#",
        tr("files.details.col.name"),
        tr("files.details.col.duration"),
        tr("files.details.col.source"),
        tr("download.queue.audio_track"),
        tr("files.details.col.path"),
        tr("files.details.col.status"),
        "",
      ]
    )
    self.tbl_sources.horizontalHeader().moveSection(self.COL_STATUS, 3)
    for column in (self.COL_NO, self.COL_SRC, self.COL_PATH):
      self.tbl_sources.setColumnHidden(column, True)
    self.tbl_sources.set_identity_column(self.COL_TITLE, detail_column=self.COL_PATH, icon_name='file')
    self.tbl_sources.verticalHeader().setVisible(False)
    self.tbl_sources.setCornerButtonEnabled(False)
    self.tbl_sources.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    self.tbl_sources.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    self.tbl_sources.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)

    self._apply_empty_header_mode()

    details_layout.addWidget(self.tbl_sources, 2)
    self._details_group = details_group
    root.addWidget(details_group, 2)

  def _build_action_bar(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
    self.action_bar = ProgressActionBar(
      primary_text=tr("controls.start"),
      secondary_text=tr("controls.cancel"),
      height=base_h,
    )
    self.action_bar.setObjectName("FilesActionBar")
    root.addWidget(self.action_bar)

  def _build_main_row(self, root: QtWidgets.QHBoxLayout, base_h: int) -> None:
    cfg = self._ui
    self.grp_options = SectionGroup(
      self,
      object_name="QuickOptions",
      role="panelGroup",
      layout="vbox",
    )
    ql = cast(QtWidgets.QVBoxLayout, self.grp_options.root)
    setup_layout(
      ql,
      cfg=cfg,
      margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin),
      spacing=cfg.spacing,
    )

    mode_host, out_host, source_host, target_host, url_download_host = self._build_quick_options_controls(base_h)
    header = QtWidgets.QLabel(tr('files.options.title'))
    header.setProperty('role', 'sectionTitle')
    ql.addWidget(header)
    for host in (mode_host, source_host, target_host, out_host):
      ql.addWidget(host)
    self.url_options_disclosure = CollapsibleSection(tr('files.options.url_download.title'))
    self.url_options_disclosure.body_layout.addWidget(url_download_host)
    ql.addWidget(self.url_options_disclosure)
    ql.addStretch(1)
    scroll = QtWidgets.QScrollArea(self)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setMinimumWidth(0)
    scroll.setWidget(self.grp_options)
    self._options_scroll = scroll
    self._options_header = header
    self.options_disclosure = CollapsibleSection(tr('files.options.title'))
    self.options_disclosure.body_layout.setContentsMargins(0, 0, 0, 0)
    self.options_disclosure.body_layout.addWidget(scroll)
    self.options_disclosure.btn_toggle.hide()
    self.options_disclosure.set_expanded(True, animated=False)
    self._compact_options = False
    self.options_disclosure.expanded_changed.connect(self._on_options_expanded)
    root.addWidget(self.options_disclosure)

  def _reflow_work_area(self, width: int) -> None:
    compact = width < 900
    self._work_layout.setDirection(QtWidgets.QBoxLayout.Direction.TopToBottom if compact
                                   else QtWidgets.QBoxLayout.Direction.LeftToRight)
    self.options_disclosure.setMinimumWidth(0 if compact else 288)
    self.options_disclosure.setMaximumWidth(16777215 if compact else 312)
    if self._options_dialog is not None and self._options_dialog.isVisible() and not compact:
      self._options_dialog.close()
    if self._options_dialog is None or not self._options_dialog.isVisible():
      self._options_scroll.setMaximumHeight(220 if compact else 16777215)
    self.options_disclosure.btn_toggle.setVisible(compact)
    self._options_header.setVisible(not compact)
    if compact != self._compact_options:
      self._compact_options = compact
      self.options_disclosure.set_expanded(not compact, animated=False)
    self.tbl_sources.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded if compact
                                                 else QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

  def _on_options_expanded(self, expanded: bool) -> None:
    if not expanded or not self._compact_options or self.height() >= 580:
      return
    self.options_disclosure.set_expanded(False, animated=False)
    if self._options_dialog is None:
      self._options_dialog = AppDialog(self)
      self._options_dialog.setWindowTitle(tr('files.options.title'))
      screen = dialog_screen(self._options_dialog)
      available = screen.availableGeometry().size() if screen is not None else QtCore.QSize(868, 644)
      self._options_dialog.resize(min(540, available.width() - 48), min(660, available.height() - 64))
      layout = QtWidgets.QVBoxLayout(self._options_dialog)
      buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close, self._options_dialog)
      buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Close).setText(tr('about.viewer.close'))
      setup_button(buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Close), min_w=0)
      buttons.rejected.connect(self._options_dialog.close)
      layout.addWidget(buttons)
      self._options_dialog.finished.connect(self._restore_options_scroll)
    self._options_scroll.setMaximumHeight(16777215)
    self._options_dialog.layout().insertWidget(0, self._options_scroll, 1)
    self._options_scroll.show()
    self._options_dialog.show()
    center_dialog(self._options_dialog)
    self._options_dialog.raise_()
    self._options_dialog.activateWindow()

  def _restore_options_scroll(self) -> None:
    self.options_disclosure.body_layout.addWidget(self._options_scroll)
    self._options_scroll.setMaximumHeight(220 if self._compact_options else 16777215)

  def hideEvent(self, event: QtGui.QHideEvent) -> None:
    if self._options_dialog is not None:
      self._options_dialog.close()
    super().hideEvent(event)

  def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
    super().resizeEvent(event)
    if getattr(self, '_compact_options', False) and self.options_disclosure.is_expanded():
      self._on_options_expanded(True)

  def _build_mode_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    self.tg_mode = ChoiceToggle(
      first_text=tr("files.options.mode.transcribe"),
      second_text=tr("files.options.mode.transcribe_translate"),
      height=base_h,
      vertical=True,
    )
    self.tg_mode.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
    return build_field_stack(
      self,
      tr("common.field.mode"),
      self.tg_mode,
      buddy=self.tg_mode,
    )

  def _build_target_language_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    self.cmb_target_language = LanguageCombo(codes_provider=supported_target_language_codes)
    self.cmb_target_language.setMinimumHeight(base_h)
    return build_field_stack(
      self,
      tr("common.field.target_language"),
      self.cmb_target_language,
      buddy=self.cmb_target_language,
      filled=True,
    )

  def _build_source_language_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    self.cmb_source_language = LanguageCombo(
      special_first=("language.special.auto_detect", LanguagePolicy.AUTO),
      codes_provider=supported_source_language_codes,
    )
    self.cmb_source_language.setMinimumHeight(base_h)
    return build_field_stack(
      self,
      tr("common.field.source_language"),
      self.cmb_source_language,
      buddy=self.cmb_source_language,
      filled=True,
    )

  def _build_output_formats_field(self, _base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    cfg = self._ui
    self._out_checks = {}
    self.out_checks_host = QtWidgets.QWidget()
    out_checks_lay = QtWidgets.QVBoxLayout(self.out_checks_host)
    setup_layout(
      out_checks_lay,
      cfg=cfg,
      margins=(0, 0, 0, 0),
      spacing=cfg.space_s,
    )

    self.formats_disclosure = CollapsibleSection(tr('files.options.more_formats'))
    for mode in transcription_output_modes():
      mid = str(mode.get("id", "")).strip().lower()
      if not mid:
        continue
      cb = QtWidgets.QCheckBox(tr(str(mode.get("tr_key", ""))))
      setup_option_checkbox(cb, min_h=cfg.option_row_min_h)
      self._out_checks[mid] = cb
      if mid in {'txt', 'srt', 'vtt'}:
        out_checks_lay.addWidget(cb)
      else:
        self.formats_disclosure.body_layout.addWidget(cb)
        cb.toggled.connect(
          lambda checked: self.formats_disclosure.set_expanded(True, animated=False) if checked else None,
        )
    out_checks_lay.addWidget(self.formats_disclosure)
    self.formats_disclosure.set_badge(str(len(self._out_checks) - 3))

    return build_field_stack(
      self,
      tr("files.options.output_format.label"),
      self.out_checks_host,
      buddy=self.out_checks_host,
    )

  def _create_quick_options_autosave_controller(self) -> None:
    self._opt_autosave = OptionsAutosave(
      self,
      build_payload=self._build_quick_options_payload,
      commit=self._commit_quick_options_payload,
      is_busy=self._quick_options_save_is_busy,
      interval_ms=1200,
      pending_delay_ms=300,
    )

  def _build_url_download_options_field(self, base_h: int) -> QtWidgets.QWidget:
    cfg = self._ui
    self.chk_download_audio_only = QtWidgets.QCheckBox(tr("files.options.url_download.download_audio_only"))
    self.chk_download_audio_only.setToolTip(tr("files.options.help.download_audio_only"))
    setup_option_checkbox(self.chk_download_audio_only, min_h=base_h)

    self.chk_keep_url_audio = QtWidgets.QCheckBox(tr("files.options.url_download.keep_audio"))
    self.chk_keep_url_audio.setToolTip(tr("files.options.help.keep_audio"))
    setup_option_checkbox(self.chk_keep_url_audio, min_h=base_h)

    self.cmb_audio_ext = PopupComboBox()
    setup_combo(self.cmb_audio_ext, min_h=base_h)
    self.cmb_audio_ext.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    self.chk_keep_url_video = QtWidgets.QCheckBox(tr("files.options.url_download.keep_video"))
    self.chk_keep_url_video.setToolTip(tr("files.options.help.keep_video"))
    setup_option_checkbox(self.chk_keep_url_video, min_h=base_h)

    self.cmb_video_ext = PopupComboBox()
    setup_combo(self.cmb_video_ext, min_h=base_h)
    self.cmb_video_ext.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    self._fill_audio_ext_combo()
    self._fill_video_ext_combo()
    self._create_quick_options_autosave_controller()

    url_download_host = QtWidgets.QWidget()
    url_download_grid = QtWidgets.QGridLayout(url_download_host)
    setup_layout(
      url_download_grid,
      cfg=cfg,
      margins=(0, 0, 0, 0),
      spacing=cfg.spacing,
      hspacing=cfg.space_l,
      vspacing=cfg.space_s,
      column_stretches={0: 1, 1: 1},
    )
    url_download_grid.addWidget(self.chk_download_audio_only, 2, 0, 1, 2)

    aud_row = QtWidgets.QWidget()
    aud_lay = QtWidgets.QHBoxLayout(aud_row)
    setup_layout(aud_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_s)
    aud_lay.addWidget(self.chk_keep_url_audio, 0)
    aud_lay.addWidget(self.cmb_audio_ext, 1)
    url_download_grid.addWidget(aud_row, 3, 0, 1, 2)

    vid_row = QtWidgets.QWidget()
    vid_lay = QtWidgets.QHBoxLayout(vid_row)
    setup_layout(vid_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_s)
    vid_lay.addWidget(self.chk_keep_url_video, 0)
    vid_lay.addWidget(self.cmb_video_ext, 1)
    url_download_grid.addWidget(vid_row, 4, 0, 1, 2)
    return url_download_host

  def _build_quick_options_controls(
    self,
    base_h: int,
  ) -> tuple[QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget]:
    mode_host, self.lbl_mode = self._build_mode_field(base_h)
    target_host, self.lbl_target_lang = self._build_target_language_field(base_h)
    source_host, self.lbl_source_lang = self._build_source_language_field(base_h)
    out_host, self.lbl_output = self._build_output_formats_field(base_h)
    url_download_host = self._build_url_download_options_field(base_h)
    return mode_host, out_host, source_host, target_host, url_download_host

  def _wire_signals(self) -> None:
    self.btn_src_add.clicked.connect(self._on_add_clicked)
    self.ed_source_input.returnPressed.connect(self._on_add_clicked)
    self.ed_source_input.textChanged.connect(self._update_open_source_state)

    self.btn_add_files.clicked.connect(self._on_add_files_clicked)
    self.btn_add_folder.clicked.connect(self._on_add_folder_clicked)
    self.btn_open_source.clicked.connect(self._on_open_source_clicked)
    self.btn_refresh_status.clicked.connect(self._on_refresh_status_clicked)
    self.btn_remove_selected.clicked.connect(self._on_remove_selected)
    self.btn_clear_list.clicked.connect(self._on_clear_clicked)
    self.btn_open_output.clicked.connect(self._open_output_folder)

    self.action_bar.primary_clicked.connect(self._on_start_clicked)
    self.action_bar.secondary_clicked.connect(self._on_cancel_clicked)

    self.tbl_sources.itemSelectionChanged.connect(self._update_buttons)
    self.tbl_sources.cellClicked.connect(self._on_table_cell_clicked)
    self.tbl_sources.viewport().installEventFilter(self)
    self.tbl_sources.paths_dropped.connect(self._on_paths_dropped)
    self.tbl_sources.delete_pressed.connect(self._on_remove_selected)
    self.tbl_sources.preview_requested.connect(self._on_preview_requested)
    self.tbl_sources.cellDoubleClicked.connect(lambda row, _col: self._open_transcript_for_row(row))

    self.tg_mode.changed.connect(self._on_quick_option_changed)
    self.chk_download_audio_only.toggled.connect(self._on_quick_option_changed)
    self.chk_keep_url_audio.toggled.connect(self._on_quick_option_changed)
    self.chk_keep_url_video.toggled.connect(self._on_quick_option_changed)

    for cb in self._out_checks.values():
      cb.toggled.connect(self._on_quick_option_changed)
    self.cmb_audio_ext.currentIndexChanged.connect(self._on_quick_option_changed)
    self.cmb_video_ext.currentIndexChanged.connect(self._on_quick_option_changed)
    self.cmb_target_language.currentTextChanged.connect(self._on_target_language_changed)
    self.cmb_source_language.currentTextChanged.connect(self._on_source_language_changed)
    if not connect_network_status_changed(self.parentWidget(), self._on_network_status_changed):
      _LOG.debug("Files network signal hookup skipped. host=%s", type(self.parentWidget()).__name__)

  def _restore_initial_state(self) -> None:
    self._apply_saved_quick_options()
    self._apply_runtime_model_state()
    self._sync_options_ui()
    self._refresh_runtime_ui()

  def _apply_saved_quick_options(self) -> None:
    self._opt_autosave.set_blocked(True)
    try:
      tcfg = AppConfig.transcription_cfg_dict()

      self.chk_download_audio_only.setChecked(bool(tcfg.get("download_audio_only", True)))
      self.chk_keep_url_audio.setChecked(bool(tcfg.get("url_keep_audio", False)))
      self.chk_keep_url_video.setChecked(bool(tcfg.get("url_keep_video", False)))
      self.url_options_disclosure.set_expanded(
        self.chk_keep_url_audio.isChecked() or self.chk_keep_url_video.isChecked(), animated=False,
      )

      audio_ext_raw = tcfg.get("url_audio_ext")
      if audio_ext_raw:
        audio_ext = str(audio_ext_raw).strip().lower().lstrip(".")
        if audio_ext in DownloadPolicy.DOWNLOAD_AUDIO_OUTPUT_EXTENSIONS:
          set_combo_data(self.cmb_audio_ext, audio_ext)

      vext_raw = tcfg.get("url_video_ext")
      if vext_raw:
        vext = str(vext_raw).strip().lower().lstrip(".")
        if vext in DownloadPolicy.DOWNLOAD_VIDEO_OUTPUT_EXTENSIONS:
          set_combo_data(self.cmb_video_ext, vext)

      translate_after = bool(
        tcfg.get("translate_after_transcription", AppConfig.transcription_translate_after_enabled())
      )
      self.tg_mode.set_first_checked(not translate_after)

      translation_option_enabled = not EngineCatalog.current_model_disabled("translation")
      self.tg_mode.set_second_enabled(translation_option_enabled)

      self._rebuild_target_language_combo(
        desired=LanguagePolicy.PREFERRED,
      )
      self._rebuild_source_language_combo(
        desired=LanguagePolicy.PREFERRED,
      )

      output_formats = tcfg.get("output_formats")
      if isinstance(output_formats, str):
        selected = {output_formats.strip().lower()}
      elif isinstance(output_formats, (list, tuple)):
        selected = {str(x or "").strip().lower() for x in output_formats}
      else:
        selected = {"txt"}

      for mid, cb in self._out_checks.items():
        cb.setChecked(mid in selected)
    finally:
      self._session_target_language = combo_current_code(
        self.cmb_target_language,
        default=LanguagePolicy.PREFERRED,
      )
      self._session_source_language = combo_current_code(
        self.cmb_source_language,
        default=LanguagePolicy.PREFERRED,
      )
      self._opt_autosave.set_blocked(False)

  def showEvent(self, e: QtGui.QShowEvent) -> None:
    super().showEvent(e)
    self._refresh_runtime_ui()

  def on_parent_close(self) -> None:
    coord = self.coordinator()
    if self._is_transcription_running() and coord is not None:
      coord.cancel_transcription()

    if self._coordinator_is_probe_running() and coord is not None:
      coord.cancel_probe()

  def _refresh_runtime_ui(self) -> None:
    self._refresh_runtime_badge()
    self._refresh_pending_row_statuses()
    self._update_buttons()

  def _is_transcription_running(self) -> bool:
    return self._coordinator_is_transcribing()

  def _sync_options_and_autosave(self, *, refresh_targets: bool = False) -> None:
    if refresh_targets:
      self._refresh_target_languages_if_ready()
    self._sync_options_ui()
    self._opt_autosave.trigger()

  def _network_available(self) -> bool:
    return self._network_status != 'offline'

  def _pending_status_for_row_id(self, row_id: str) -> str:
    source_kind = str(self._source_kind_by_row_id.get(str(row_id), '') or '').strip().lower()
    if source_kind == 'url' and not self._network_available():
      return 'status.offline'
    return 'status.queued'

  def _refresh_pending_row_statuses(self) -> None:
    for row in range(self.tbl_sources.rowCount()):
      row_id = self._row_id_at(row)
      if not row_id or row_id in self._transcript_by_row_id:
        continue
      active_base = str(self._status_base_by_row_id.get(str(row_id), '') or '').strip()
      if active_base and is_active_work_status(active_base):
        continue
      self._set_pending_row_status(row, self._pending_status_for_row_id(row_id))

  def _set_pending_row_status(self, row: int, status_key: str) -> None:
    item = self.tbl_sources.item(row, self.COL_STATUS)
    if item is None:
      return
    text = status_display_text(status_key, status_key)
    item.setText(text)
    self.tbl_sources.refresh_probe_presentation(
      row=row,
      status_col=self.COL_STATUS,
      status_key=str(status_key or '').strip(),
    )

  def _set_probe_row_status(self, row_id: str) -> None:
    target_row_id = str(row_id or '').strip()
    if not target_row_id:
      return
    row = self._row_for_row_id(target_row_id)
    if row < 0:
      return
    self._status_base_by_row_id[target_row_id] = 'status.probing'
    self._pct_by_row_id.pop(target_row_id, None)
    item = self.tbl_sources.item(row, self.COL_STATUS)
    if item is None:
      return
    item.setText(status_display_text('status.probing', 'status.probing'))
    self.tbl_sources.refresh_probe_presentation(
      row=row,
      status_col=self.COL_STATUS,
      status_key='status.probing',
    )

  def _finish_probe_row_status(self, row_id: str) -> None:
    target_row_id = str(row_id or '').strip()
    if not target_row_id:
      return
    row = self._row_for_row_id(target_row_id)
    if row < 0:
      return
    self._status_base_by_row_id.pop(target_row_id, None)
    self._set_pending_row_status(row, self._pending_status_for_row_id(target_row_id))

  def _refresh_runtime_badge(self) -> None:
    self._refresh_mode_badge()
    if self._network_status == 'offline':
      network_key = 'files.runtime.network_offline'
      network_state = 'offline'
    elif self._network_status == 'online':
      network_key = 'files.runtime.network_online'
      network_state = 'ready'
    else:
      network_key = 'files.runtime.network_checking'
      network_state = 'loading'
    try:
      self.model_info.set_network_presentation(
        build_static_runtime_presentation(text=tr(network_key), state=network_state)
      )
    except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
      _LOG.debug("Files runtime network badge update skipped. detail=%s", ex)

  @QtCore.pyqtSlot(str)
  def _on_network_status_changed(self, status: str) -> None:
    previous = self._network_status
    self._network_status = normalize_network_status(status)
    if previous != self._network_status:
      _LOG.debug('Files network state updated. previous=%s current=%s', previous, self._network_status)
    self._refresh_runtime_ui()

  @staticmethod
  def _disabled_value() -> str:
    return str(tr("files.runtime.value_disabled") or "").strip() or "disabled"

  def _build_runtime_engine_presentation(
    self,
    *,
    model_cfg: dict[str, Any],
    ready: bool,
    error_key: str | None,
    error_params: dict[str, Any],
  ) -> RuntimePresentation:
    disabled = EngineCatalog.model_cfg_disabled(model_cfg)
    engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()
    engine_name = EngineCatalog.model_label(engine_name)
    return build_runtime_presentation(
      ready=bool(ready and (not disabled)),
      disabled=disabled,
      ready_text=engine_name,
      disabled_text=self._disabled_value(),
      missing_text=tr("files.runtime.status_missing"),
      error_key=error_key,
      error_params=error_params,
    )

  def _build_runtime_summary_presentation(self) -> RuntimePresentation:
    return build_runtime_presentation(
      ready=self._transcription_ready,
      disabled=EngineCatalog.current_model_disabled("transcription"),
      ready_text=tr("files.runtime.status_ready"),
      disabled_text=tr("files.runtime.status_disabled"),
      missing_text=tr("files.runtime.status_missing"),
      error_key=self._transcription_error_key,
      error_params=self._transcription_error_params,
      icon_names={
        "ready": "status_ready",
        "disabled": "status_info",
        "error": "status_error",
        "missing": "status_error",
      },
    )

  def _icon_for_runtime_presentation(self, presentation: RuntimePresentation) -> QtGui.QIcon:
    icon_name = str(presentation.icon_name or "").strip()
    if icon_name == "status_ready":
      fallback = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton)
    elif icon_name == "status_info":
      fallback = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
    elif icon_name == "status_loading":
      fallback = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
    else:
      fallback = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical)
    return self._status_icon(icon_name or "status_error", fallback)

  def _on_quick_option_changed(self, *_args) -> None:
    self._sync_options_and_autosave(refresh_targets=True)

  def _on_target_language_changed(self, *_args) -> None:
    current = combo_current_code(
      self.cmb_target_language,
      default=LanguagePolicy.PREFERRED,
    )
    self._session_target_language = self._resolve_target_language_selection(current)
    self._sync_options_and_autosave()

  def _on_source_language_changed(self, *_args) -> None:
    self._session_source_language = combo_current_code(
      self.cmb_source_language,
      default=LanguagePolicy.PREFERRED,
    )
    self._sync_options_and_autosave()

  def _current_transcription_options(self) -> dict[str, Any]:
    output_formats = [mid for mid, cb in self._out_checks.items() if cb.isChecked()]

    audio_only = bool(self.chk_download_audio_only.isChecked())
    keep_audio = bool(self.chk_keep_url_audio.isChecked())
    keep_video = bool(self.chk_keep_url_video.isChecked()) and (not audio_only)

    audio_ext = (
      str(self.cmb_audio_ext.currentData() or AppConfig.transcription_url_audio_ext()).strip().lower().lstrip(".")
    )
    video_ext = (
      str(self.cmb_video_ext.currentData() or AppConfig.transcription_url_video_ext()).strip().lower().lstrip(".")
    )

    if not output_formats:
      output_formats = list(AppConfig.transcription_output_mode_ids())

    return {
      "translate_after_transcription": bool(
        (not self.tg_mode.is_first_checked()) and self._translation_runtime_available()
      ),
      "output_formats": output_formats,
      "download_audio_only": audio_only,
      "url_keep_audio": keep_audio,
      "url_audio_ext": audio_ext or AppConfig.transcription_url_audio_ext(),
      "url_keep_video": keep_video,
      "url_video_ext": video_ext or AppConfig.transcription_url_video_ext(),
    }

  def _build_quick_options_payload(self) -> dict[str, Any]:
    return build_files_quick_options_payload(
      source_language_selection=self._session_source_language,
      target_language_selection=self._session_target_language,
      **self._current_transcription_options(),
    )

  def _commit_quick_options_payload(self, payload: dict[str, Any]) -> None:
    coord = self.coordinator()
    if coord is None:
      return
    coord.save_quick_options(payload)

  def on_quick_options_save_error(self, key: str, params: dict[str, Any]) -> None:
    dialogs.show_error(self, key=key, params=params or {})

  def _fill_audio_ext_combo(self) -> None:
    self.cmb_audio_ext.clear()
    for ext in DownloadPolicy.DOWNLOAD_AUDIO_OUTPUT_EXTENSIONS:
      self.cmb_audio_ext.addItem(str(ext), ext)
    self.cmb_audio_ext.setCurrentIndex(0)

  def _fill_video_ext_combo(self) -> None:
    self.cmb_video_ext.clear()
    for ext in DownloadPolicy.DOWNLOAD_VIDEO_OUTPUT_EXTENSIONS:
      self.cmb_video_ext.addItem(str(ext), ext)
    self.cmb_video_ext.setCurrentIndex(0)

  @staticmethod
  def _effective_source_language_code(selection: str | None, *, supported: list[str] | None = None) -> str:
    codes = supported if supported is not None else supported_source_language_codes()
    return effective_source_language_code("files", selection, supported=codes)

  @staticmethod
  def _resolve_source_language_selection(
    selection: str | None,
    *,
    supported: list[str] | None = None,
  ) -> str:
    codes = supported if supported is not None else supported_source_language_codes()
    return resolve_source_language_selection(selection, supported=codes)

  def _rebuild_source_language_combo(
    self,
    *,
    desired: str,
    supported: list[str] | None = None,
  ) -> None:
    codes = supported if supported is not None else supported_source_language_codes()
    items = build_source_language_items(
      "files",
      supported=codes,
      ui_language=current_language(),
    )
    wanted = self._resolve_source_language_selection(desired, supported=codes)
    rebuild_code_combo(
      self.cmb_source_language,
      items,
      desired_code=wanted,
      fallback_code=LanguagePolicy.PREFERRED,
    )

  def _refresh_source_languages_if_ready(self) -> None:
    desired = (
      self._session_source_language
      or combo_current_code(self.cmb_source_language, default=LanguagePolicy.PREFERRED)
      or LanguagePolicy.PREFERRED
    )
    supported = supported_source_language_codes()
    resolved_selection = self._resolve_source_language_selection(desired, supported=supported)
    self._rebuild_source_language_combo(desired=resolved_selection, supported=supported)
    self._session_source_language = combo_current_code(
      self.cmb_source_language,
      default=LanguagePolicy.PREFERRED,
    )

  @staticmethod
  def _resolve_target_language_selection(
    selection: str | None,
    *,
    supported: list[str] | None = None,
  ) -> str:
    codes = supported if supported is not None else supported_target_language_codes()
    return resolve_target_language_selection(selection, supported=codes)

  @staticmethod
  def _effective_target_language_code(
    selection: str | None,
    *,
    supported: list[str] | None = None,
  ) -> str:
    codes = supported if supported is not None else supported_target_language_codes()
    return effective_target_language_code(
      "files",
      selection,
      ui_language=current_language(),
      supported=codes,
    )

  def _refresh_target_languages_if_ready(self) -> None:
    desired = (
      self._session_target_language
      or combo_current_code(self.cmb_target_language, default=LanguagePolicy.PREFERRED)
      or LanguagePolicy.PREFERRED
    )
    supported = supported_target_language_codes()
    resolved_selection = self._resolve_target_language_selection(desired, supported=supported)
    self._rebuild_target_language_combo(desired=resolved_selection, supported=supported)
    self._session_target_language = combo_current_code(
      self.cmb_target_language,
      default=LanguagePolicy.PREFERRED,
    )

  def refresh_defaults_from_settings(self) -> None:
    self._opt_autosave.set_blocked(True)
    try:
      self._refresh_target_languages_if_ready()
      self._refresh_source_languages_if_ready()
      self._sync_options_ui()
    finally:
      self._opt_autosave.set_blocked(False)

  def _rebuild_target_language_combo(
    self,
    *,
    desired: str,
    supported: list[str] | None = None,
  ) -> None:
    codes = supported if supported is not None else supported_target_language_codes()
    items = build_target_language_items(
      "files",
      supported=codes,
      ui_language=current_language(),
    )
    wanted = self._resolve_target_language_selection(desired, supported=codes)
    rebuild_code_combo(
      self.cmb_target_language,
      items,
      desired_code=wanted,
      fallback_code=LanguagePolicy.PREFERRED,
    )

  def _set_preview_enabled(self, row_id: str, enabled: bool) -> None:
    row = self._row_for_row_id(row_id)
    if row < 0:
      return
    w = self.tbl_sources.cellWidget(row, self.COL_PREVIEW)
    if not w:
      return
    btn = w.findChild(QtWidgets.QAbstractButton)
    if btn:
      btn.setEnabled(bool(enabled))

  def _reset_previews(self, row_ids: list[str] | None = None) -> None:
    targets = list(row_ids or [])
    if not targets:
      return
    for row_id in targets:
      self._output_dir_by_row_id.pop(row_id, None)
      self._transcript_by_row_id.pop(row_id, None)
      self._set_preview_enabled(row_id, False)

  def _translation_runtime_available(self) -> bool:
    return bool(self._translation_ready) and translation_runtime_available(
      translation_state=EngineRuntimeState(
        ready=self._translation_ready,
        error_key=self._translation_error_key,
        error_params=self._translation_error_params,
      ),
      model_cfg=EngineCatalog.current_model_cfg("translation"),
    )

  def _refresh_mode_badge(self) -> None:
    asr_presentation = self._build_runtime_engine_presentation(
      model_cfg=EngineCatalog.current_model_cfg("transcription"),
      ready=self._transcription_ready,
      error_key=self._transcription_error_key,
      error_params=self._transcription_error_params,
    )
    translation_presentation = self._build_runtime_engine_presentation(
      model_cfg=EngineCatalog.current_model_cfg("translation"),
      ready=self._translation_ready,
      error_key=self._translation_error_key,
      error_params=self._translation_error_params,
    )
    self.model_info.set_asr_presentation(asr_presentation)
    self.model_info.set_translation_presentation(translation_presentation)
    devices = [getattr(self, '_transcription_device', '')] if self._transcription_ready else []
    if self._translation_ready and not self.tg_mode.is_first_checked():
      devices.append(getattr(self, '_translation_device', ''))
    self.model_info.set_device_value(' / '.join(dict.fromkeys(filter(None, devices))) or tr('common.na'))

  def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
    if obj is self.tbl_sources.viewport() and event.type() == QtCore.QEvent.Type.Resize:
      if getattr(self, "_header_mode", "") == "empty":
        self._apply_empty_column_widths()
    return super().eventFilter(obj, event)

  def _sync_options_ui(self) -> None:
    running = self._is_transcription_running()
    model_ready = self._transcription_ready

    translation_available = self._translation_runtime_available()

    if not model_ready:
      self.lbl_mode.setEnabled(False)
      self.tg_mode.setEnabled(False)
      self.tg_mode.set_second_enabled(False)

      self.lbl_output.setEnabled(False)
      self.out_checks_host.setEnabled(False)

      self.lbl_source_lang.setEnabled(False)
      self.cmb_source_language.setEnabled(False)
      self.lbl_target_lang.setEnabled(False)
      self.cmb_target_language.setEnabled(False)

      self.chk_download_audio_only.setEnabled(False)
      self.chk_keep_url_audio.setEnabled(False)
      self.cmb_audio_ext.setEnabled(False)
      self.chk_keep_url_video.setEnabled(False)
      self.cmb_video_ext.setEnabled(False)
      return

    self.tg_mode.setEnabled(not running)
    self.tg_mode.set_second_enabled(bool(translation_available and (not running)))
    if not translation_available:
      if not self.tg_mode.is_first_checked():
        self.tg_mode.set_first_checked(True)

    translate_mode = bool(not self.tg_mode.is_first_checked()) and translation_available
    self.lbl_target_lang.setEnabled((not running) and translate_mode)
    self.cmb_target_language.setEnabled((not running) and translate_mode)

    self.lbl_source_lang.setEnabled(not running)
    self.cmb_source_language.setEnabled(not running)

    audio_only = bool(self.chk_download_audio_only.isChecked())

    self.chk_keep_url_video.setEnabled((not running) and (not audio_only))
    self.cmb_video_ext.setEnabled((not running) and (not audio_only) and self.chk_keep_url_video.isChecked())

    self.chk_keep_url_audio.setEnabled(not running)
    self.cmb_audio_ext.setEnabled((not running) and self.chk_keep_url_audio.isChecked())

    self.chk_download_audio_only.setEnabled(not running)
    self.lbl_mode.setEnabled(not running)
    self.lbl_output.setEnabled(not running)
    self.out_checks_host.setEnabled(not running)

  def _status_width(self) -> int:
    cfg = self._ui
    labels = [str(tr('files.details.col.status') or '').strip()]
    labels.extend(
      display_texts_for_statuses(
        (
          'status.queued',
          'status.offline',
          'status.processing',
          'status.probing',
          'status.downloading',
          'status.transcribing',
          'status.translating',
          'status.saving',
          'status.saved',
          'status.done',
          'status.skipped',
          'status.error',
        )
      )
    )
    metrics = QtGui.QFontMetrics(self.tbl_sources.font())
    text_width = max((metrics.horizontalAdvance(label) for label in labels if label), default=0)
    min_w = int(cfg.control_min_w + cfg.pad_x_l)
    pad_w = int(cfg.pad_x_l + cfg.pad_y_l + cfg.space_l - 1)
    max_w = int(cfg.control_min_w + cfg.control_min_h * 3 + max(0, cfg.space_s - 1))
    return max(
      min_w,
      min(text_width + pad_w + metrics.horizontalAdvance(' (100%)'), max_w),
    )

  def _preview_width(self) -> int:
    return 52

  def _apply_empty_header_mode(self) -> None:
    self._header_mode = 'empty'
    self.tbl_sources.reset_header_user_widths()
    self._apply_source_header_layout()

  def _apply_populated_header_mode(self) -> None:
    self._header_mode = 'populated'
    self._apply_source_header_layout()

  def _apply_source_header_layout(self) -> None:
    language_width = self.tbl_sources.column_widget_width_hint(self.COL_LANG, fallback=148, pad=6, cap=184)
    self.tbl_sources.apply_content_header_layout(
      check_col=self.COL_CHECK,
      number_col=self.COL_NO,
      fill_column=self.COL_TITLE,
      stretch_weights={self.COL_TITLE: 1},
      preferred_widths={
        self.COL_DUR: 96,
        self.COL_LANG: language_width,
        self.COL_STATUS: min(148, self._status_width()),
      },
      min_widths={self.COL_TITLE: 200, self.COL_DUR: 90, self.COL_LANG: 140, self.COL_STATUS: 112},
      max_widths={self.COL_DUR: 106, self.COL_LANG: 184, self.COL_STATUS: 168},
      fixed_widths={self.COL_PREVIEW: self._preview_width()},
    )

  def _apply_empty_column_widths(self) -> None:
    if getattr(self, '_header_mode', '') == 'empty':
      self.tbl_sources.reapply_header_layout()

  def _on_preview_requested(self, key: str) -> None:
    key = str(key or "").strip()
    if not key:
      return
    out_dir = self._output_dir_by_row_id.get(key)
    if not out_dir:
      return
    try:
      p = Path(out_dir)
      p.mkdir(parents=True, exist_ok=True)
      open_local_path(p)
    except (OSError, RuntimeError, TypeError, ValueError) as e:
      _LOG.error("Opening the preview output folder failed. key=%s path=%s", key, out_dir, exc_info=True)
      dialogs.show_error(self, key="dialog.error.unexpected", params={"msg": str(e)})

  @QtCore.pyqtSlot(int)
  def _on_lang_combo_changed(self, idx: int) -> None:
    w = self.sender()
    if not isinstance(w, QtWidgets.QComboBox):
      return

    key = str(w.property("internal_key") or "").strip()
    if not key:
      return

    track_ids = w.property("audio_track_ids") or [None]
    try:
      track_id = track_ids[idx] if 0 <= idx < len(track_ids) else None
    except (IndexError, TypeError):
      track_id = None

    self._audio_track_by_row_id[key] = str(track_id).strip() or None if track_id else None

  def _update_audio_tracks(self, row: int, meta: dict[str, Any]) -> None:
    default_text = tr("download.select.audio_track.default")
    row_id = self._row_id_at(row)
    if row_id:
      self._audio_track_by_row_id.setdefault(row_id, None)
    self.tbl_sources.update_audio_tracks(
      row=row,
      col=self.COL_LANG,
      meta=meta,
      default_text=default_text,
      preferred_audio_track_id=self._audio_track_by_row_id.get(row_id) if row_id else None,
      internal_key=row_id or None,
    )
    if row_id:
      self._audio_track_by_row_id[row_id] = self.tbl_sources.audio_track_id_at(row, self.COL_LANG)

    self._update_buttons()

  @QtCore.pyqtSlot(int, int)
  def _on_table_cell_clicked(self, row: int, col: int) -> None:
    if row < 0:
      return

    mods = QtWidgets.QApplication.keyboardModifiers()
    if not (mods & QtCore.Qt.KeyboardModifier.ControlModifier):
      return

    if col not in (self.COL_SRC, self.COL_PATH):
      return

    target = self.tbl_sources.text_at(row, self.COL_PATH)
    if not target:
      return

    try:
      if is_url_source(target):
        if "://" not in target:
          target = "https://" + target
        open_external_url(target)
        return

      p = Path(target)
      if not p.exists():
        return

      if p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
      else:
        open_local_path(p)
    except (OSError, RuntimeError, ValueError) as ex:
      _LOG.debug("Files source reveal skipped. target=%s detail=%s", target, ex)

  def _insert_placeholder_row(
    self,
    source_key: str,
    *,
    source_kind: str,
    title: str = "",
    duration_s: int | None = None,
  ) -> None:
    if self.tbl_sources.rowCount() == 0:
      self._apply_populated_header_mode()

    row_id = self._new_row_id()
    row = self.tbl_sources.rowCount()
    self.tbl_sources.insertRow(row)

    self._source_key_by_row_id[row_id] = source_key
    self._source_kind_by_row_id[row_id] = str(source_kind or "file").strip().lower() or "file"
    self._display_path_by_row_id[row_id] = source_key
    self._audio_track_by_row_id.setdefault(row_id, None)
    self._bind_runtime_key(row_id, source_key)

    self.tbl_sources.setCellWidget(
      row,
      self.COL_CHECK,
      self.tbl_sources.make_checkbox_cell(on_changed=self._update_buttons),
    )

    it_no = QtWidgets.QTableWidgetItem(str(row + 1))
    it_no.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
    self.tbl_sources.setItem(row, self.COL_NO, it_no)

    initial_title = str(title or "").strip() or tr("common.loading")
    it_title = QtWidgets.QTableWidgetItem(initial_title)
    self.tbl_sources.setItem(row, self.COL_TITLE, it_title)

    initial_dur = format_hms(duration_s, blank_for_none=True)
    it_dur = QtWidgets.QTableWidgetItem(initial_dur or tr("common.na"))
    it_dur.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
    self.tbl_sources.setItem(row, self.COL_DUR, it_dur)

    src_label = self._source_label_for_kind(source_kind)
    it_src = QtWidgets.QTableWidgetItem(src_label)
    it_src.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
    self.tbl_sources.setItem(row, self.COL_SRC, it_src)
    it_src.setToolTip(src_label)

    track_cb = self.tbl_sources.make_audio_track_combo(
      internal_key=row_id,
      default_text=tr("download.select.audio_track.default"),
      on_changed=self._on_lang_combo_changed,
      enabled=False,
    )
    self.tbl_sources.setCellWidget(row, self.COL_LANG, track_cb)

    it_path = QtWidgets.QTableWidgetItem(source_key)
    it_path.setToolTip(source_key)
    it_path.setData(QtCore.Qt.ItemDataRole.UserRole, row_id)
    self.tbl_sources.setItem(row, self.COL_PATH, it_path)

    pending_status = self._pending_status_for_row_id(row_id)
    it_status = QtWidgets.QTableWidgetItem(tr(pending_status))
    it_status.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
    self.tbl_sources.setItem(row, self.COL_STATUS, it_status)

    self.tbl_sources.setCellWidget(
      row,
      self.COL_PREVIEW,
      self.tbl_sources.make_preview_cell(
        internal_key=row_id,
        tooltip=tr("files.preview.open_folder"),
        enabled=False,
      ),
    )

  def _update_row_from_meta(self, row: int, meta: dict[str, Any]) -> None:
    if row < 0 or row >= self.tbl_sources.rowCount():
      return

    title = str(meta.get("name") or meta.get("title") or tr("common.na"))
    duration = meta.get("duration")
    row_id = self._row_id_at(row)

    self.tbl_sources.item(row, self.COL_TITLE).setText(title)
    txt = format_hms(duration, blank_for_none=True)
    self.tbl_sources.item(row, self.COL_DUR).setText(txt or tr("common.na"))
    self._update_audio_tracks(row, meta)
    if row_id:
      self._error_by_row_id.pop(row_id, None)
    presentation = build_probe_success_presentation(
      meta,
      visible_status_keys=("status.queued", "status.offline"),
    )
    self.tbl_sources.set_probe_presentation(
      row=row,
      col=self.COL_LANG,
      status_col=self.COL_STATUS,
      presentation=presentation,
      status_key='status.probing',
    )

  def _start_metadata_for(self, keys: list[str]) -> None:
    if not keys:
      return

    entries = []
    for k in keys:
      entries.append({"type": ("url" if is_url_source(k) else "file"), "value": k})

    coord = self.coordinator()
    if coord is not None:
      for key in keys:
        row_id = self._row_id_for_runtime_key(str(key or '').strip())
        if row_id:
          self._set_probe_row_status(row_id)
      coord.start_probe(entries)

  def _update_buttons(self) -> None:
    has_items = self.tbl_sources.rowCount() > 0
    has_runnable_items = bool(self._transcription_row_ids())
    action_rows = self._action_rows()
    has_sel = bool(action_rows)
    model_ready = self._transcription_ready
    running = self._is_transcription_running()
    expanding = self._coordinator_is_expanding()
    busy = bool(running or expanding)

    self.ed_source_input.setEnabled((not busy) and model_ready)

    self.action_bar.set_primary_enabled(has_runnable_items and model_ready and not busy)
    self.action_bar.set_secondary_enabled(running)

    self.btn_clear_list.setEnabled(has_items and not busy and model_ready)
    self.btn_remove_selected.setEnabled(has_sel and not busy and model_ready)

    self.btn_src_add.setEnabled(not busy and model_ready)
    self.btn_open_output.setEnabled(True)
    self.btn_refresh_status.setEnabled(has_sel and not busy and model_ready)

    self.btn_add_files.setEnabled(not busy and model_ready)
    self.btn_add_folder.setEnabled(not busy and model_ready)

    self.tbl_sources.setEnabled(model_ready and not expanding)
    self.tbl_sources.setAcceptDrops(bool(model_ready and (not busy)))
    if model_ready and (not busy):
      self.tbl_sources.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DropOnly)
    else:
      self.tbl_sources.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.NoDragDrop)

    if busy:
      self.tbl_sources.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
    else:
      self.tbl_sources.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

    self.tbl_sources.set_header_checkbox_enabled(bool((not running) and model_ready and has_items))

    for r in range(self.tbl_sources.rowCount()):
      cb = self.tbl_sources.checkbox_at(r, self.COL_CHECK)
      if cb is not None:
        cb.setEnabled(bool((not running) and model_ready))

      w = self.tbl_sources.combo_at(r, self.COL_LANG)
      if isinstance(w, QtWidgets.QComboBox):
        can_choose = bool(w.property("has_choices"))
        w.setEnabled(bool(can_choose and (not running) and model_ready))

    self._update_open_source_state()
    self._sync_options_ui()

  def _discard_source_state(self, row_id: str) -> None:
    target_row_id = str(row_id or "").strip()
    if not target_row_id:
      return

    runtime_key = self._runtime_key_for_row_id(target_row_id)
    self._unbind_runtime_key(target_row_id, runtime_key)
    self._runtime_key_by_row_id.pop(target_row_id, None)
    self._source_key_by_row_id.pop(target_row_id, None)
    self._source_kind_by_row_id.pop(target_row_id, None)
    self._display_path_by_row_id.pop(target_row_id, None)
    self._audio_track_by_row_id.pop(target_row_id, None)
    self._transcript_by_row_id.pop(target_row_id, None)
    self._status_base_by_row_id.pop(target_row_id, None)
    self._pct_by_row_id.pop(target_row_id, None)
    self._error_by_row_id.pop(target_row_id, None)
    self._output_dir_by_row_id.pop(target_row_id, None)

  def _clear_source_collections(self) -> None:
    self._source_key_by_row_id.clear()
    self._runtime_key_by_row_id.clear()
    self._row_ids_by_runtime_key.clear()
    self._source_kind_by_row_id.clear()
    self._display_path_by_row_id.clear()
    self._audio_track_by_row_id.clear()
    self._transcript_by_row_id.clear()
    self._status_base_by_row_id.clear()
    self._pct_by_row_id.clear()
    self._error_by_row_id.clear()
    self._output_dir_by_row_id.clear()

  def _finalize_source_rows_changed(self) -> None:
    self.tbl_sources.renumber_rows(self.COL_NO)
    self._update_buttons()

  def _try_parse_and_validate_manual_source(self) -> dict[str, Any] | None:
    parsed = parse_source_input(self.ed_source_input.text())
    if not parsed.get("ok", False):
      err = str(parsed.get("error") or "")
      if err in ("not_found", "unsupported"):
        dialogs.show_info(
          self,
          title=tr("dialog.info.title"),
          message=tr("dialog.info.source_missing"),
        )
      return None

    key = str(parsed.get("key") or "").strip()
    if not key:
      return None

    parsed["key"] = key
    return parsed

  @staticmethod
  def _source_label_for_kind(source_kind: str) -> str:
    kind = str(source_kind or "").strip().lower()
    return tr("files.source.url") if kind == "url" else tr("files.source.local")

  def _ensure_expansion_progress_dialog(self) -> dialogs.ExpansionProgressDialog:
    dlg = ensure_progress_dialog(self, self._expansion_progress_dialog, self._cancel_expansion_request)
    self._expansion_progress_dialog = dlg
    return dlg

  def _cancel_expansion_request(self) -> None:
    coord = self.coordinator()
    if coord is None:
      return
    coord.cancel_expansion()

  def _apply_expansion_result(
    self,
    result: SourceExpansionResult,
    items: tuple[ExpandedSourceItem, ...] | None = None,
  ) -> tuple[int, int]:
    added_source_keys: list[str] = []
    duplicate_count = 0
    source_items = tuple(result.items) if items is None else tuple(items)
    for item in source_items:
      source_key = str(getattr(item, "key", "") or "").strip()
      if not source_key:
        continue
      allow, duplicate = self._can_add_source_key(source_key)
      if not allow:
        if duplicate:
          duplicate_count += 1
        continue
      source_kind = str(getattr(item, "source_kind", "file") or "file").strip().lower() or "file"
      self._insert_placeholder_row(
        source_key,
        source_kind=source_kind,
        title=str(getattr(item, "title", "") or ""),
        duration_s=getattr(item, "duration_s", None),
      )
      added_source_keys.append(source_key)

    if added_source_keys:
      self._start_metadata_for(added_source_keys[:30])
    self._finalize_source_rows_changed()

    if result.origin_kind in {"manual_input", "playlist"}:
      self.ed_source_input.clear()

    return len(added_source_keys), duplicate_count

  def _show_expansion_summary(
    self,
    result: SourceExpansionResult,
    *,
    added_count: int,
    duplicate_count: int,
    selected_count: int,
  ) -> None:
    message = ""
    total = int(max(0, int(result.discovered_count or 0)))
    selected = int(max(0, int(selected_count or 0)))
    limited = bool(total > 0 and 0 < selected < total)
    if total <= 1:
      if added_count == 0 and duplicate_count > 0:
        message = tr("files.messages.already_on_list")
    elif limited and added_count > 0 and duplicate_count > 0:
      message = tr(
        "files.messages.bulk_add_summary_limited_with_duplicates",
        added=added_count,
        selected=selected,
        total=total,
        skipped=duplicate_count,
      )
    elif limited and added_count > 0:
      message = tr("files.messages.bulk_add_summary_limited", added=added_count, selected=selected, total=total)
    elif added_count > 0 and duplicate_count > 0:
      message = tr("files.messages.bulk_add_summary_with_duplicates", added=added_count, skipped=duplicate_count)
    elif added_count > 0:
      message = tr("files.messages.bulk_add_summary_added", added=added_count)
    elif duplicate_count > 0:
      message = tr("files.messages.bulk_add_summary_duplicates_only", skipped=duplicate_count)

    if not message:
      return

    dialogs.show_info(
      self,
      title=tr("dialog.info.title"),
      message=message,
    )

  def _reset_sources_view_state(self) -> None:
    self.tbl_sources.setRowCount(0)
    self.action_bar.reset()
    self._apply_empty_header_mode()
    self._update_buttons()

  def _on_add_clicked(self) -> None:
    parsed = self._try_parse_and_validate_manual_source()
    if not parsed:
      return

    if str(parsed.get("type") or "").strip().lower() == "url":
      if not confirm_source_rights_notice(self, logger=_LOG):
        return

    coord = self.coordinator()
    if coord is None:
      return
    coord.expand_manual_input(self.ed_source_input.text())

  def _on_paths_dropped(self, paths: list[str]) -> None:
    coord = self.coordinator()
    if coord is None:
      return
    coord.expand_local_paths(list(paths or []), origin_kind="drop")

  def _remove_rows(self, rows: list[int]) -> None:
    if not rows:
      return
    for r in sorted(set(rows), reverse=True):
      row_id = self._row_id_at(r)
      if row_id:
        self._discard_source_state(row_id)
      self.tbl_sources.removeRow(r)

    if self.tbl_sources.rowCount() == 0:
      self._apply_empty_header_mode()

    self._finalize_source_rows_changed()

  def _open_transcript_for_row(self, row: int) -> None:
    row_id = self._row_id_at(row)
    if not row_id:
      return
    path = self._transcript_by_row_id.get(row_id)
    if not path:
      return
    try:
      open_local_path(Path(path))
    except (OSError, RuntimeError, ValueError) as ex:
      _LOG.debug("Transcript open skipped. path=%s detail=%s", path, ex)

  def _on_add_files_clicked(self) -> None:
    files, _ = AppFileDialog.getOpenFileNames(
      self,
      tr("files.add_files"),
      "",
      tr("files.details.filters.audio_video"),
    )
    if not files:
      return
    coord = self.coordinator()
    if coord is None:
      return
    coord.expand_local_paths(list(files), origin_kind="file_selection")

  def _on_add_folder_clicked(self) -> None:
    folder = AppFileDialog.getExistingDirectory(self, tr("files.add_folder"))
    if not folder:
      return
    p = Path(folder)
    if not p.exists() or not p.is_dir():
      return
    coord = self.coordinator()
    if coord is None:
      return
    coord.expand_local_paths([str(p)], origin_kind="folder")

  def _action_rows(self) -> list[int]:
    return self.tbl_sources.rows_for_removal(self.COL_CHECK)

  def _source_target_for_row(self, row: int) -> str:
    if row < 0 or row >= self.tbl_sources.rowCount():
      return ""
    row_id = self._row_id_at(row)
    if not row_id:
      return ""
    return self._source_key_for_row_id(row_id)

  def _source_target(self) -> str:
    rows = self._action_rows()
    if rows:
      return self._source_target_for_row(rows[0])
    return str(self.ed_source_input.text() or "").strip()

  def _update_open_source_state(self) -> None:
    self.btn_open_source.setEnabled(bool(self._source_target()))

  @staticmethod
  def _open_source_target(target: str) -> bool:
    source = str(target or "").strip()
    if not source:
      return False

    try:
      if is_url_source(source):
        if "://" not in source:
          source = "https://" + source
        return bool(open_external_url(source))

      path = Path(source)
      if not path.exists():
        return False

      if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
        return True

      return bool(open_local_path(path))
    except (OSError, RuntimeError, ValueError) as ex:
      _LOG.debug("Files source open skipped. target=%s detail=%s", source, ex)
      return False

  def _on_open_source_clicked(self) -> None:
    target = self._source_target()
    if not target:
      return
    self._open_source_target(target)

  def _reset_row_status(self, row_id: str) -> None:
    target_row_id = str(row_id or "").strip()
    if not target_row_id:
      return

    self._pct_by_row_id.pop(target_row_id, None)
    self._status_base_by_row_id.pop(target_row_id, None)
    self._error_by_row_id.pop(target_row_id, None)
    self._output_dir_by_row_id.pop(target_row_id, None)
    self._transcript_by_row_id.pop(target_row_id, None)
    self._set_preview_enabled(target_row_id, False)

    row = self._row_for_row_id(target_row_id)
    if row < 0:
      return
    item = self.tbl_sources.item(row, self.COL_STATUS)
    if item is not None:
      item.setToolTip("")
    self._set_pending_row_status(row, self._pending_status_for_row_id(target_row_id))

  def _on_refresh_status_clicked(self) -> None:
    if self._is_transcription_running() or self._coordinator_is_expanding():
      return

    rows = self._action_rows()
    if not rows:
      return

    for row in rows:
      row_id = self._row_id_at(row)
      if row_id:
        self._reset_row_status(row_id)

    self._update_buttons()

  def _on_remove_selected(self) -> None:
    rows = self.tbl_sources.rows_for_removal(self.COL_CHECK)
    self._remove_rows(rows)

  def _on_clear_clicked(self) -> None:
    self._clear_source_collections()
    self._reset_sources_view_state()

  def _open_output_folder(self) -> None:
    try:
      out_dir = AppConfig.PATHS.TRANSCRIPTIONS_DIR
      out_dir.mkdir(parents=True, exist_ok=True)
      open_local_path(out_dir)
    except (OSError, RuntimeError, TypeError, ValueError) as e:
      _LOG.error(
        "Opening the transcriptions output folder failed. path=%s",
        AppConfig.PATHS.TRANSCRIPTIONS_DIR,
        exc_info=True,
      )
      dialogs.show_error(self, key="dialog.error.unexpected", params={"msg": str(e)})

  def _can_start_transcription(self) -> bool:
    if self._is_transcription_running():
      return False
    if not self._transcription_ready:
      return False
    if AppConfig.SETTINGS is None:
      return False
    return True

  def _prepare_transcription_entries(self) -> tuple[list[dict[str, Any]], list[str]]:
    row_ids = self._transcription_row_ids()
    if not row_ids:
      return [], []

    self._refresh_target_languages_if_ready()
    self._reset_previews(row_ids)

    source_keys: list[str] = []
    audio_track_by_source_key: dict[str, str] = {}
    for row_id in row_ids:
      source_key = self._source_key_for_row_id(row_id)
      if not source_key:
        continue
      self._replace_runtime_key(row_id, source_key)
      source_keys.append(source_key)
      audio_track_id = str(self._audio_track_by_row_id.get(row_id) or '').strip()
      if audio_track_id:
        audio_track_by_source_key[source_key] = audio_track_id
    return build_entries(source_keys, audio_track_by_source_key), row_ids

  def _reset_transcription_run_state(self, run_row_ids: list[str]) -> None:
    for row_id in run_row_ids:
      self._pct_by_row_id.pop(row_id, None)
      self._status_base_by_row_id.pop(row_id, None)
      self._error_by_row_id.pop(row_id, None)
      self._output_dir_by_row_id.pop(row_id, None)
      self._transcript_by_row_id.pop(row_id, None)

      row = self._row_for_row_id(row_id)
      if row < 0:
        continue
      it = self.tbl_sources.item(row, self.COL_STATUS)
      if it:
        it.setText("-")
        it.setToolTip("")

    self.action_bar.reset()
    self._was_cancelled = False
    self._cancel_notice_pending = False
    self._conflict_apply_all_action = None
    self._conflict_apply_all_new_base = None

  def _build_transcription_session_request(self) -> TranscriptionSessionRequest:
    supported_source = supported_source_language_codes()
    supported_target = supported_target_language_codes()
    return build_transcription_session_request(
      source_language=self._effective_source_language_code(
        self._session_source_language,
        supported=supported_source,
      ),
      target_language=self._effective_target_language_code(
        self._session_target_language,
        supported=supported_target,
      ),
      **self._current_transcription_options(),
    )

  def _request_transcription_cancel(self) -> None:
    coord = self.coordinator()
    if coord is None:
      return
    coord.cancel_transcription()

  def _mark_non_finished_rows_cancelled(self) -> None:
    for row in range(self.tbl_sources.rowCount()):
      row_id = self._row_id_at(row)
      if not row_id:
        continue
      base_key = self._status_base_by_row_id.get(row_id)
      if is_terminal_status(base_key or ""):
        continue
      self._status_base_by_row_id[row_id] = "status.cancelled"
      self._apply_terminal_status_state(row_id, "status.cancelled")
      self._pct_by_row_id.pop(row_id, None)
      self._render_row_status_text(row_id, row, "status.cancelled", tr("status.cancelled"))
      self.tbl_sources.refresh_probe_presentation(
        row=row,
        status_col=self.COL_STATUS,
        status_key="status.cancelled",
      )

  def _on_start_clicked(self) -> None:
    if not self._can_start_transcription():
      return

    entries, run_row_ids = self._prepare_transcription_entries()
    if not entries:
      return

    self._reset_transcription_run_state(run_row_ids)

    session_request = self._build_transcription_session_request()
    coord = self.coordinator()
    if coord is None:
      return
    coord.start_transcription(entries=entries, session_request=session_request)
    self._update_buttons()

  def _on_cancel_clicked(self) -> None:
    if not self._is_transcription_running():
      return
    if not dialogs.ask_cancel(self):
      return

    self._cancel_notice_pending = True
    self._request_transcription_cancel()
    self._update_buttons()

  @QtCore.pyqtSlot(int)
  def on_global_progress(self, value: int) -> None:
    if self._was_cancelled:
      return
    self.action_bar.set_progress(int(value))

  @QtCore.pyqtSlot(list)
  def on_meta_rows_ready(self, batch: list[dict[str, Any]]) -> None:
    for meta in batch:
      runtime_key = str(meta.get("path") or "").strip()
      if not runtime_key:
        continue
      row_id = self._row_id_for_runtime_key(runtime_key)
      if not row_id:
        continue
      row = self._row_for_row_id(row_id)
      if row < 0:
        continue
      self._update_row_from_meta(row, meta)
      self._finish_probe_row_status(row_id)

  @QtCore.pyqtSlot(str, str, dict)
  def on_meta_item_error(self, key: str, err_key: str, params: dict[str, Any]) -> None:
    runtime_key = str(key or "").strip()
    row_id = self._row_id_for_runtime_key(runtime_key) if runtime_key else ""
    row = self._row_for_row_id(row_id) if row_id else -1
    if row < 0 or not row_id:
      return

    error_key = str(err_key or "error.generic").strip() or "error.generic"
    error_params = dict(params or {})
    self._error_by_row_id[row_id] = (error_key, error_params)
    self._pct_by_row_id.pop(row_id, None)
    self._status_base_by_row_id[row_id] = 'status.error'
    self._output_dir_by_row_id.pop(row_id, None)
    self._transcript_by_row_id.pop(row_id, None)
    self._set_preview_enabled(row_id, False)
    presentation = build_probe_error_presentation(error_key, error_params)
    self.tbl_sources.set_probe_presentation(
      row=row,
      col=self.COL_LANG,
      status_col=self.COL_STATUS,
      presentation=presentation,
      status_key='status.error',
    )
    item = self.tbl_sources.item(row, self.COL_STATUS)
    if item is not None:
      item.setText(tr('status.error'))

  def on_meta_finished(self) -> None:
    self._update_buttons()

  @QtCore.pyqtSlot(bool)
  def on_expansion_busy_changed(self, busy: bool) -> None:
    if busy:
      show_progress_dialog(self._ensure_expansion_progress_dialog())
    else:
      hide_progress_dialog(self._expansion_progress_dialog)
    self._update_buttons()

  @QtCore.pyqtSlot(str, dict)
  def on_expansion_status_changed(self, key: str, params: dict[str, Any]) -> None:
    update_progress_dialog_message(self._ensure_expansion_progress_dialog(), key, params or {})

  @QtCore.pyqtSlot(object)
  def on_expansion_ready(self, result: SourceExpansionResult) -> None:
    hide_progress_dialog(self._expansion_progress_dialog)
    if result.discovered_count <= 0 or not result.items:
      dialogs.show_info(
        self,
        title=tr("dialog.info.title"),
        message=tr("files.messages.no_media_found"),
      )
      return

    selected_items = tuple(result.items)
    threshold = int(AppConfig.ui_bulk_add_confirmation_threshold())
    if should_confirm_bulk_add(result.discovered_count):
      action, chosen_count = dialogs.ask_bulk_add_plan(
        self,
        origin_kind=result.origin_kind,
        count=result.discovered_count,
        origin_label=result.origin_label,
        sample_titles=sample_expansion_titles(result),
        default_limit=threshold,
        target_label=tr("dialog.bulk_add.target.files"),
      )
      if action == "cancel":
        return
      if action == "first_n":
        selected_items = limit_expansion_items(result, chosen_count)

    added_count, duplicate_count = self._apply_expansion_result(result, selected_items)
    self._show_expansion_summary(
      result,
      added_count=added_count,
      duplicate_count=duplicate_count,
      selected_count=len(selected_items),
    )

  @QtCore.pyqtSlot(str, dict)
  def on_expansion_error(self, key: str, params: dict[str, Any]) -> None:
    hide_progress_dialog(self._expansion_progress_dialog)
    dialogs.show_error(self, key, params or {})

  def on_transcribe_finished(self) -> None:
    self.action_bar.reset()
    if self._was_cancelled:
      self._mark_non_finished_rows_cancelled()
      if self._cancel_notice_pending:
        dialogs.show_info(
          self,
          title=tr("dialog.info.title"),
          message=tr("dialog.info.cancelled"),
        )
    self._cancel_notice_pending = False
    self._update_buttons()

  @QtCore.pyqtSlot(str, bool, bool, bool)
  def on_session_done(self, session_dir: str, processed_any: bool, had_errors: bool, was_cancelled: bool) -> None:
    self._was_cancelled = bool(was_cancelled)
    if not processed_any or had_errors or was_cancelled:
      return
    try:
      if dialogs.ask_open_transcripts_folder(self, session_dir):
        p = Path(session_dir)
        p.mkdir(parents=True, exist_ok=True)
        open_local_path(p)
    except (OSError, RuntimeError, ValueError) as ex:
      _LOG.debug("Transcripts folder open prompt follow-up skipped. session_dir=%s detail=%s", session_dir, ex)
    finally:
      self.action_bar.reset()

  @staticmethod
  def _should_reset_progress_for_status_change(prev_base: str | None, new_base: str, status: str) -> bool:
    return bool(prev_base and prev_base != new_base and not is_terminal_status(status))

  def _render_row_status_text(self, row_id: str, row: int, status: str, base_text: str) -> None:
    it = self.tbl_sources.item(row, self.COL_STATUS)
    if it is None:
      return

    pct = self._pct_by_row_id.get(row_id)
    text = compose_status_text(status, pct, fallback=base_text or status)
    it.setText(text)

  def _apply_terminal_status_state(self, row_id: str, status: str) -> None:
    if status in ("status.done", "status.saved"):
      self._pct_by_row_id[row_id] = 100
      if self._output_dir_by_row_id.get(row_id):
        self._set_preview_enabled(row_id, True)
      return

    if status in ("status.skipped", "status.error", "status.cancelled"):
      self._pct_by_row_id.pop(row_id, None)
      self._output_dir_by_row_id.pop(row_id, None)
      self._transcript_by_row_id.pop(row_id, None)
      self._set_preview_enabled(row_id, False)

  @QtCore.pyqtSlot(str, str)
  def on_item_status(self, key: str, status: str) -> None:
    if self._was_cancelled:
      return
    runtime_key = str(key or "").strip()
    status = str(status or "").strip()
    row_id = self._row_id_for_runtime_key(runtime_key)
    if not row_id:
      return

    row = self._row_for_row_id(row_id)
    if row < 0:
      return

    base_key = normalize_status_base_key(status)
    base_text = status_display_text(base_key, status)
    prev_base = self._status_base_by_row_id.get(row_id)
    if self._should_reset_progress_for_status_change(prev_base, base_key, status):
      self._pct_by_row_id.pop(row_id, None)

    if base_text:
      self._status_base_by_row_id[row_id] = base_key

    self._apply_terminal_status_state(row_id, status)
    self._render_row_status_text(row_id, row, status, base_text)
    self.tbl_sources.refresh_probe_presentation(
      row=row,
      status_col=self.COL_STATUS,
      status_key=base_key,
    )

  @QtCore.pyqtSlot(str, int)
  def on_item_progress(self, key: str, pct: int) -> None:
    if self._was_cancelled:
      return
    runtime_key = str(key or "").strip()
    row_id = self._row_id_for_runtime_key(runtime_key)
    if not row_id:
      return

    pct = max(0, min(100, int(pct)))
    self._pct_by_row_id[row_id] = pct

    row = self._row_for_row_id(row_id)
    if row < 0:
      return

    base_key = self._status_base_by_row_id.get(row_id) or "status.processing"
    text = compose_status_text(base_key, pct, fallback=base_key)

    it = self.tbl_sources.item(row, self.COL_STATUS)
    if it:
      it.setText(text)

  @QtCore.pyqtSlot(str, str, dict)
  def on_item_error(self, key: str, err_key: str, params: dict[str, Any]) -> None:
    if self._was_cancelled:
      return

    runtime_key = str(key or "").strip()
    row_id = self._row_id_for_runtime_key(runtime_key)
    if not row_id:
      return

    error_key = str(err_key or "error.generic").strip() or "error.generic"
    eparams = dict(params or {})
    self._error_by_row_id[row_id] = (error_key, eparams)

    row = self._row_for_row_id(row_id)
    if row < 0:
      return

    it = self.tbl_sources.item(row, self.COL_STATUS)
    if it:
      it.setText(tr("status.error"))
      it.setToolTip(tr(error_key, **eparams))

  @QtCore.pyqtSlot(str, str)
  def on_item_output_dir(self, key: str, out_dir: str) -> None:
    runtime_key = str(key or "").strip()
    row_id = self._row_id_for_runtime_key(runtime_key)
    if not row_id:
      return
    self._output_dir_by_row_id[row_id] = str(out_dir or "").strip()
    if self._status_base_by_row_id.get(row_id) in {"status.done", "status.saved"}:
      self._set_preview_enabled(row_id, True)

  @QtCore.pyqtSlot(str, str)
  def on_item_path_update(self, old_key: str, new_key: str) -> None:
    row_id = self._row_id_for_runtime_key(old_key)
    if not row_id:
      return

    self._replace_runtime_key(row_id, new_key)
    if self._source_kind_by_row_id.get(row_id) == "url":
      return
    self._start_metadata_for([new_key])

  @QtCore.pyqtSlot(str, str)
  def on_transcript_ready(self, key: str, transcript_path: str) -> None:
    runtime_key = str(key or "").strip()
    row_id = self._row_id_for_runtime_key(runtime_key)
    if not row_id:
      return
    self._transcript_by_row_id[row_id] = str(transcript_path or "")

  def _submit_conflict_resolution(self, action: str, new_stem: str = "") -> None:
    coord = self.coordinator()
    if coord is None:
      return
    coord.resolve_conflict(action, new_stem)

  def _submit_access_intervention(
    self,
    source_key: str,
    resolution: SourceAccessInterventionResolution,
  ) -> None:
    coord = self.coordinator()
    if coord is None:
      return
    coord.resolve_access_intervention(source_key, resolution)

  @QtCore.pyqtSlot(str, dict)
  def on_access_intervention_required(self, source_key: str, params: dict[str, Any]) -> None:
    if self._was_cancelled or self._cancel_notice_pending:
      self._submit_access_intervention(source_key, SourceAccessInterventionResolution())
      return

    resolution = dialogs.ask_source_access_intervention(
      self,
      kind=str((params or {}).get("kind") or "cookies").strip(),
      source_kind=str((params or {}).get("source_kind") or "browser").strip(),
      source_label=str((params or {}).get("source_label") or "").strip(),
      detail=str((params or {}).get("detail") or "").strip(),
      state=str((params or {}).get("state") or "").strip(),
      provider_state=str((params or {}).get("provider_state") or "").strip(),
      can_retry=bool((params or {}).get("can_retry", True)),
      can_choose_cookie_file=bool((params or {}).get("can_choose_cookie_file", True)),
      can_continue_without_cookies=bool((params or {}).get("can_continue_without_cookies", True)),
      can_retry_enhanced=bool((params or {}).get("can_retry_enhanced", False)),
      can_continue_basic=bool((params or {}).get("can_continue_basic", False)),
      can_continue_degraded=bool((params or {}).get("can_continue_degraded", False)),
      browser_policy=str((params or {}).get("browser_policy") or "").strip(),
      available_browser_policies=tuple(
        str(item or "").strip().lower()
        for item in ((params or {}).get("available_browser_policies") or ())
        if str(item or "").strip()
      ),
    )
    self._submit_access_intervention(source_key, resolution)

  @QtCore.pyqtSlot(str, str)
  def on_conflict_check(self, stem: str, _existing_dir: str) -> None:
    if self._was_cancelled or self._cancel_notice_pending:
      self._submit_conflict_resolution("skip", "")
      return

    try:
      if self._conflict_apply_all_action:
        action = self._conflict_apply_all_action
        new_stem = self._conflict_apply_all_new_base or stem if action == "new" else ""
        self._submit_conflict_resolution(action, new_stem)
        return

      action, new_stem, apply_all = dialogs.ask_conflict(self, stem)
      if apply_all and action != "new":
        self._conflict_apply_all_action = action
        self._conflict_apply_all_new_base = None

      self._submit_conflict_resolution(action, new_stem)
    except (RuntimeError, ValueError) as ex:
      _LOG.debug("Conflict resolution fallback applied. stem=%s detail=%s", stem, ex)
      self._submit_conflict_resolution("skip", "")

  def on_runtime_state_changed(self, state: AppRuntimeState) -> None:
    self.model_info.set_hardware_devices([state.transcription.hardware, state.translation.hardware])
    self._transcription_device = str(state.transcription.hardware.get('DEVICE_FRIENDLY_NAME') or '')
    self._translation_device = str(state.translation.hardware.get('DEVICE_FRIENDLY_NAME') or '')
    self._transcription_ready = bool(state.transcription.ready)
    self._transcription_error_key = str(state.transcription.error_key or "").strip() or None
    self._transcription_error_params = dict(state.transcription.error_params or {})
    self._translation_ready = bool(state.translation.ready)
    self._translation_error_key = str(state.translation.error_key or "").strip() or None
    self._translation_error_params = dict(state.translation.error_params or {})
    self._apply_runtime_model_state()
    self._refresh_target_languages_if_ready()
    self._refresh_runtime_ui()

  def _apply_runtime_model_state(self) -> None:
    """Apply model readiness state pushed by the controller."""
    summary_presentation = self._build_runtime_summary_presentation()
    self.model_info.set_summary_presentation(summary_presentation)
    self.model_info.set_summary_icon(self._icon_for_runtime_presentation(summary_presentation))
    self._refresh_mode_badge()
    self._update_buttons()
