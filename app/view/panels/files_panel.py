# app/view/panels/files_panel.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

import logging

from PyQt5 import QtCore, QtGui, QtWidgets

from app.view.components.popup_combo import (
    LanguageCombo,
    PopupComboBox,
    combo_current_code,
    set_combo_data,
)
from app.view.components.choice_toggle import ChoiceToggle
from app.view.components import dialogs
from app.view.components.progress_action_bar import ProgressActionBar
from app.view.components.runtime_badge import RuntimeBadgeWidget
from app.view.components.section_group import SectionGroup
from app.view.components.source_table import SourceTable
from app.controller.support.localization import tr, Translator
from app.controller.support.runtime_resolver import (
    build_entries,
    build_files_quick_options_payload,
    build_files_transcription_patch,
    collect_media_files,
    is_playlist_url,
    parse_source_input,
    translation_language_codes,
    try_add_source_key,
    build_transcription_runtime_overrides,
    translation_runtime_available,
)
from app.controller.tasks.media_probe_task import MediaProbeWorker
from app.controller.support.options_autosave_controller import OptionsAutosaveController
from app.controller.support.task_thread_runner import TaskThreadRunner
from app.controller.tasks.transcription_task import TranscriptionWorker
from app.model.config.app_config import AppConfig as Config
from app.model.helpers.string_utils import format_hms, normalize_lang_code
from app.model.io.media_probe import is_url_source
from app.model.services.ai_models_service import current_transcription_model_cfg, current_translation_model_cfg
from app.model.services.settings_service import SettingsCatalog, SettingsSnapshot
from app.view.support.theme_runtime import status_icon
from app.view.support.view_runtime import (
    normalize_network_status,
    open_external_url,
    open_local_path,
    read_network_status,
)
from app.view.support.widget_effects import enable_styled_background
from app.view.support.widget_setup import (
    build_field_stack,
    make_grid,
    setup_button,
    setup_combo,
    setup_input,
    setup_option_checkbox,
    setup_layout,
)
from app.view.ui_config import ui

_LOG = logging.getLogger(__name__)

class FilesPanel(QtWidgets.QWidget):
    """Files tab: manage sources and batch transcription/translation."""

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
    def _theme() -> str:
        app = QtWidgets.QApplication.instance()
        t = str(app.property("theme") if app else "light").strip().lower()
        return "dark" if t == "dark" else "light"

    def _status_icon(self, key: str, fallback: QtGui.QIcon) -> QtGui.QIcon:
        try:
            icon = status_icon(key, theme=self._theme())
            if not icon.isNull():
                return icon
        except Exception:
            pass
        return fallback

    def __init__(self, parent: QtWidgets.QWidget | None = None, boot_ctx: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilesPanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)
        self._boot_ctx: dict[str, Any] = boot_ctx if isinstance(boot_ctx, dict) else {}

        self._init_runtime_state(parent)
        self._build_ui()
        self._wire_signals(parent)
        self._restore_initial_state()

    # ----- Initialization / build -----

    def _init_runtime_state(self, parent: QtWidgets.QWidget | None) -> None:
        self._transcribe_runner = TaskThreadRunner(self)
        self._transcribe_worker: TranscriptionWorker | None = None

        self._meta_thread: QtCore.QThread | None = None
        self._meta_worker: MediaProbeWorker | None = None
        self._was_cancelled: bool = False
        self._cancel_notice_pending: bool = False
        self._conflict_apply_all_action: str | None = None
        self._conflict_apply_all_new_base: str | None = None
        self._status_base_by_key: dict[str, str] = {}
        self._pct_by_key: dict[str, int] = {}
        self._error_by_key: dict[str, tuple[str, dict[str, Any]]] = {}
        self._output_dir_by_key: dict[str, str] = {}

        self.pipe = None
        self._keys: set[str] = set()
        self._row_by_key: dict[str, int] = {}
        self._transcript_by_key: dict[str, str] = {}
        self._origin_src_by_key: dict[str, str] = {}
        self._display_path_by_key: dict[str, str] = {}
        self._audio_lang_by_key: dict[str, str | None] = {}

        self._network_status = read_network_status(parent)
        self._session_target_language = Config.LANGUAGE_DEFAULT_UI_VALUE
        self._session_source_language = Config.LANGUAGE_AUTO_VALUE

    def _build_ui(self) -> None:
        cfg = self._ui
        root = QtWidgets.QVBoxLayout(self)
        setup_layout(root, cfg=cfg, margins=(0, cfg.spacing, 0, 0), spacing=cfg.spacing)
        base_h = cfg.control_min_h

        self.model_info = RuntimeBadgeWidget()
        self.model_info.set_summary_status(tr("files.runtime.status_loading"), state="loading")
        self.model_info.set_summary_icon(
            self._status_icon(
                "status_loading",
                self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload),
            )
        )
        self.model_info.set_device_value(tr("common.na"))
        self.model_info.set_asr_value(tr("common.na"), state="neutral")
        self.model_info.set_translation_value(tr("common.na"), state="neutral")

        self._build_top_section(root, base_h)
        self._build_details_section(root)
        self._build_action_bar(root, base_h)
        self._build_main_row(root, base_h)

    def _build_top_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        self._top_section_host = QtWidgets.QWidget(self)
        top_grid = make_grid(4, cfg)
        top_grid.setVerticalSpacing(cfg.space_l)
        self._top_section_host.setLayout(top_grid)

        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setObjectName("FilesSourceInput")
        setup_input(self.src_edit, placeholder=tr("files.placeholder"), min_h=base_h)

        self.btn_src_add = QtWidgets.QPushButton(tr("ctrl.add"))
        self.btn_src_add.setObjectName("FilesAddSource")
        self.btn_src_add.setProperty("variant", "primary")
        setup_button(self.btn_src_add, min_h=base_h, min_w=cfg.control_min_w)

        self.btn_open_output = QtWidgets.QPushButton(tr("files.open_output"))
        self.btn_open_output.setObjectName("FilesOpenOutput")
        self.btn_open_output.setProperty("variant", "secondary")
        setup_button(self.btn_open_output, min_h=base_h, min_w=cfg.control_min_w)

        top_btn_host = QtWidgets.QWidget(self._top_section_host)
        top_btn_box = QtWidgets.QHBoxLayout(top_btn_host)
        setup_layout(top_btn_box, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_l)
        top_btn_box.addWidget(self.btn_src_add, 1)
        top_btn_box.addWidget(self.btn_open_output, 3)

        top_grid.addWidget(self.src_edit, 0, 0, 1, 3)
        top_grid.addWidget(top_btn_host, 0, 3)

        self.btn_add_files = QtWidgets.QPushButton(tr("files.add_files"))
        self.btn_add_folder = QtWidgets.QPushButton(tr("files.add_folder"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))

        self.btn_add_files.setProperty("variant", "primary")
        self.btn_add_folder.setProperty("variant", "primary")
        self.btn_remove_selected.setProperty("variant", "secondary")
        self.btn_clear_list.setProperty("variant", "secondary")

        for button in (self.btn_add_files, self.btn_add_folder, self.btn_remove_selected, self.btn_clear_list):
            setup_button(button, min_h=base_h, min_w=cfg.control_min_w)

        top_grid.addWidget(self.btn_add_files, 1, 0)
        top_grid.addWidget(self.btn_add_folder, 1, 1)
        top_grid.addWidget(self.btn_remove_selected, 1, 2)
        top_grid.addWidget(self.btn_clear_list, 1, 3)

        root.addWidget(self._top_section_host)

    def _build_details_section(self, root: QtWidgets.QVBoxLayout) -> None:
        details_group = SectionGroup(self, object_name="FilesDetailsGroup")
        details_layout = cast(QtWidgets.QVBoxLayout, details_group.root)

        self.tbl = SourceTable()
        self.tbl.setObjectName("SourcesTable")
        self.tbl.setColumnCount(9)
        self.tbl.setHorizontalHeaderLabels([
            "",
            "#",
            tr("files.details.col.name"),
            tr("files.details.col.duration"),
            tr("files.details.col.source"),
            tr("files.details.col.language"),
            tr("files.details.col.path"),
            tr("files.details.col.status"),
            tr("files.details.col.preview"),
        ])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setCornerButtonEnabled(False)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)

        self._apply_empty_header_mode()

        details_layout.addWidget(self.tbl, 2)
        self._details_group = details_group
        root.addWidget(details_group, 2)

    def _build_action_bar(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        self.action_bar = ProgressActionBar(
            primary_text=tr("ctrl.start"),
            secondary_text=tr("ctrl.cancel"),
            height=base_h,
        )
        self.action_bar.setObjectName("FilesActionBar")
        root.addWidget(self.action_bar)

    def _build_main_row(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        self.options_group = SectionGroup(
            self,
            object_name="QuickOptions",
            role="panelGroup",
            layout="grid",
        )
        self.options_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        ql = cast(QtWidgets.QGridLayout, self.options_group.root)
        setup_layout(
            ql,
            cfg=cfg,
            margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin),
            spacing=cfg.spacing,
            hspacing=cfg.space_l,
            vspacing=cfg.space_s,
            column_stretches={0: 1, 1: 1},
        )

        mode_host, out_host, source_host, target_host, tmp_host = self._build_quick_options_controls(base_h)
        ql.addWidget(mode_host, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        ql.addWidget(out_host, 0, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        ql.addWidget(source_host, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        ql.addWidget(target_host, 1, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        ql.addWidget(tmp_host, 2, 0, 1, 2)

        self.model_group = SectionGroup(
            self,
            object_name="ModelSection",
            role="panelGroup",
            layout="vbox",
        )
        self.model_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        mg = cast(QtWidgets.QVBoxLayout, self.model_group.root)
        setup_layout(mg, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.spacing)
        mg.addWidget(self.model_info)
        mg.addStretch(1)

        self._main_row_host = QtWidgets.QWidget(self)
        main_row = QtWidgets.QHBoxLayout(self._main_row_host)
        setup_layout(main_row, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_l)
        main_row.addWidget(self.model_group, 1)
        main_row.addWidget(self.options_group, 3)
        root.addWidget(self._main_row_host, 0)

    def _build_mode_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        self.tg_mode = ChoiceToggle(
            first_text=tr("files.options.mode.transcribe"),
            second_text=tr("files.options.mode.transcribe_translate"),
            height=base_h,
        )
        self.tg_mode.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return build_field_stack(
            self,
            tr("common.field.mode"),
            self.tg_mode,
            buddy=self.tg_mode,
        )

    def _build_target_language_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        self.cmb_target_lang = LanguageCombo(
            special_first=("lang.special.default_ui", Config.LANGUAGE_DEFAULT_UI_VALUE),
        )
        self.cmb_target_lang.setMinimumHeight(base_h)
        return build_field_stack(
            self,
            tr("common.field.target_language"),
            self.cmb_target_lang,
            buddy=self.cmb_target_lang,
        )

    def _build_source_language_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        self.cmb_source_lang = LanguageCombo(
            special_first=("lang.special.auto_detect", Config.LANGUAGE_AUTO_VALUE),
            codes_provider=SettingsCatalog.transcription_language_codes,
        )
        self.cmb_source_lang.setMinimumHeight(base_h)
        return build_field_stack(
            self,
            tr("common.field.source_language"),
            self.cmb_source_lang,
            buddy=self.cmb_source_lang,
        )

    def _build_output_formats_field(self, base_h: int) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
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

        for mode in SettingsCatalog.transcription_output_modes():
            mid = str(mode.get("id", "")).strip().lower()
            if not mid:
                continue
            cb = QtWidgets.QCheckBox(tr(str(mode.get("tr_key", ""))))
            setup_option_checkbox(cb, min_h=base_h)
            self._out_checks[mid] = cb
            out_checks_lay.addWidget(cb)

        return build_field_stack(
            self,
            tr("files.options.output_format.label"),
            self.out_checks_host,
            buddy=self.out_checks_host,
        )

    def _create_quick_options_autosave_controller(self) -> None:
        self._opt_autosave = OptionsAutosaveController(
            self,
            build_payload=self._build_quick_options_payload,
            apply_snapshot=self._on_quick_options_saved_snapshot,
            on_error=self._on_quick_options_save_error,
            is_busy=self._is_transcription_running,
            interval_ms=1200,
            pending_delay_ms=300,
            retry_delay_ms=600,
        )

    def _build_url_temp_options_field(self, base_h: int) -> QtWidgets.QWidget:
        cfg = self._ui
        self.opt_download_audio_only = QtWidgets.QCheckBox(tr("files.options.temp.download_audio_only"))
        self.opt_download_audio_only.setToolTip(tr("files.options.help.download_audio_only"))
        setup_option_checkbox(self.opt_download_audio_only, min_h=base_h)

        self.chk_keep_url_audio = QtWidgets.QCheckBox(tr("files.options.temp.keep_audio"))
        self.chk_keep_url_audio.setToolTip(tr("files.options.help.keep_audio"))
        setup_option_checkbox(self.chk_keep_url_audio, min_h=base_h)

        self.cmb_audio_ext = PopupComboBox()
        setup_combo(self.cmb_audio_ext, min_h=base_h)
        self.cmb_audio_ext.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.chk_keep_url_video = QtWidgets.QCheckBox(tr("files.options.temp.keep_video"))
        self.chk_keep_url_video.setToolTip(tr("files.options.help.keep_video"))
        setup_option_checkbox(self.chk_keep_url_video, min_h=base_h)

        self.cmb_video_ext = PopupComboBox()
        setup_combo(self.cmb_video_ext, min_h=base_h)
        self.cmb_video_ext.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self._fill_audio_ext_combo()
        self._fill_video_ext_combo()
        self._create_quick_options_autosave_controller()

        tmp_host = QtWidgets.QWidget()
        tmp_grid = QtWidgets.QGridLayout(tmp_host)
        setup_layout(
            tmp_grid,
            cfg=cfg,
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
            hspacing=cfg.space_l,
            vspacing=cfg.space_s,
            column_stretches={0: 1, 1: 1},
        )
        tmp_grid.addWidget(self.opt_download_audio_only, 2, 0, 1, 2)

        aud_row = QtWidgets.QWidget()
        aud_lay = QtWidgets.QHBoxLayout(aud_row)
        setup_layout(aud_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_s)
        aud_lay.addWidget(self.chk_keep_url_audio, 0)
        aud_lay.addWidget(self.cmb_audio_ext, 1)
        tmp_grid.addWidget(aud_row, 3, 0)

        vid_row = QtWidgets.QWidget()
        vid_lay = QtWidgets.QHBoxLayout(vid_row)
        setup_layout(vid_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_s)
        vid_lay.addWidget(self.chk_keep_url_video, 0)
        vid_lay.addWidget(self.cmb_video_ext, 1)
        tmp_grid.addWidget(vid_row, 3, 1)
        return tmp_host

    def _build_quick_options_controls(
        self,
        base_h: int,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget, QtWidgets.QWidget]:
        mode_host, self.lbl_mode = self._build_mode_field(base_h)
        target_host, self.lbl_target_lang = self._build_target_language_field(base_h)
        source_host, self.lbl_source_lang = self._build_source_language_field(base_h)
        out_host, self.lbl_output = self._build_output_formats_field(base_h)
        tmp_host = self._build_url_temp_options_field(base_h)
        return mode_host, out_host, source_host, target_host, tmp_host

    # ----- Signal wiring -----

    def _wire_signals(self, parent: QtWidgets.QWidget | None) -> None:
        self.btn_src_add.clicked.connect(self._on_add_clicked)
        self.src_edit.returnPressed.connect(self._on_add_clicked)

        self.btn_add_files.clicked.connect(self._on_add_files_clicked)
        self.btn_add_folder.clicked.connect(self._on_add_folder_clicked)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_clicked)
        self.btn_open_output.clicked.connect(self._open_output_folder)

        self.action_bar.primary_clicked.connect(self._on_start_clicked)
        self.action_bar.secondary_clicked.connect(self._on_cancel_clicked)

        self.tbl.itemSelectionChanged.connect(self._update_buttons)
        self.tbl.cellClicked.connect(self._on_table_cell_clicked)
        self.tbl.viewport().installEventFilter(self)
        self.tbl.paths_dropped.connect(self._on_paths_dropped)
        self.tbl.delete_pressed.connect(self._on_remove_selected)
        self.tbl.preview_requested.connect(self._on_preview_requested)
        self.tbl.cellDoubleClicked.connect(lambda row, _col: self._open_transcript_for_row(row))

        self.tg_mode.changed.connect(self._on_quick_option_changed)
        self.opt_download_audio_only.toggled.connect(self._on_quick_option_changed)
        self.chk_keep_url_audio.toggled.connect(self._on_quick_option_changed)
        self.chk_keep_url_video.toggled.connect(self._on_quick_option_changed)

        for cb in self._out_checks.values():
            cb.toggled.connect(self._on_quick_option_changed)
        self.cmb_audio_ext.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_video_ext.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_target_lang.currentTextChanged.connect(self._on_target_language_changed)
        self.cmb_source_lang.currentTextChanged.connect(self._on_source_language_changed)

        parent_signal = getattr(parent, "network_status_changed", None)
        if parent_signal is not None:
            try:
                parent_signal.connect(self._on_network_status_changed)
            except Exception:
                pass

    # ----- Restore / bootstrap -----

    def _restore_initial_state(self) -> None:
        self._apply_saved_quick_options()
        self._apply_boot_model_state()
        self._sync_options_ui()
        self._refresh_runtime_ui()

    def _apply_saved_quick_options(self) -> None:
        self._opt_autosave.set_blocked(True)
        try:
            tcfg = self._get_transcription_cfg()

            self.opt_download_audio_only.setChecked(bool(tcfg.get("download_audio_only", True)))
            self.chk_keep_url_audio.setChecked(bool(tcfg.get("url_keep_audio", False)))
            self.chk_keep_url_video.setChecked(bool(tcfg.get("url_keep_video", False)))

            aext_raw = tcfg.get("url_audio_ext")
            if aext_raw:
                aext = str(aext_raw).strip().lower().lstrip(".")
                if aext in Config.DOWNLOAD_AUDIO_OUTPUT_EXTS:
                    set_combo_data(self.cmb_audio_ext, aext)

            vext_raw = tcfg.get("url_video_ext")
            if vext_raw:
                vext = str(vext_raw).strip().lower().lstrip(".")
                if vext in Config.DOWNLOAD_VIDEO_OUTPUT_EXTS:
                    set_combo_data(self.cmb_video_ext, vext)

            translate_after = bool(tcfg.get("translate_after_transcription", False))
            self.tg_mode.set_first_checked(not translate_after)

            tr_mdl = self._get_translation_model_cfg()
            tr_eng = str(tr_mdl.get("engine_name", "none") or "none").strip().lower()
            tr_enabled = bool(tr_eng and tr_eng not in ("none", "off", "disabled"))
            self.tg_mode.set_second_enabled(tr_enabled)

            self.cmb_target_lang.set_code(Config.translation_target_language())
            self.cmb_source_lang.set_code(Config.translation_source_language())

            output_formats = tcfg.get("output_formats")
            if isinstance(output_formats, str):
                selected = {output_formats.strip().lower()}
            elif isinstance(output_formats, (list, tuple)):
                selected = {str(x or "").strip().lower() for x in output_formats}
            else:
                selected = {"txt"}

            for mid, cb in self._out_checks.items():
                cb.setChecked(mid in selected)
        except Exception:
            pass
        finally:
            self._session_target_language = str(self.cmb_target_lang.code() or Config.LANGUAGE_DEFAULT_UI_VALUE).strip().lower() or Config.LANGUAGE_DEFAULT_UI_VALUE
            self._session_source_language = str(self.cmb_source_lang.code() or Config.LANGUAGE_AUTO_VALUE).strip().lower() or Config.LANGUAGE_AUTO_VALUE
            self._opt_autosave.set_blocked(False)

    # ----- Lifecycle -----

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        try:
            self._refresh_runtime_ui()
        except Exception:
            pass

    def on_parent_close(self) -> None:
        try:
            if self._is_transcription_running():
                self._transcribe_runner.cancel()
        except Exception:
            pass

        try:
            if self._meta_thread and self._meta_worker:
                self._meta_thread.requestInterruption()
        except Exception:
            pass

    # ----- Runtime state / quick options -----

    def _refresh_runtime_ui(self) -> None:
        self._refresh_runtime_badge()
        self._refresh_pending_row_statuses()
        self._update_buttons()

    def _is_transcription_running(self) -> bool:
        return bool(self._transcribe_runner.is_running())

    def _sync_options_and_autosave(self, *, refresh_targets: bool = False) -> None:
        if refresh_targets:
            self._refresh_target_languages_if_ready()
        self._sync_options_ui()
        self._opt_autosave.trigger()

    def _network_available(self) -> bool:
        return self._network_status != 'offline'

    def _pending_status_for_key(self, key: str) -> str:
        src = str(self._origin_src_by_key.get(str(key), '') or '').strip().lower()
        if src == 'url' and not self._network_available():
            return 'status.offline'
        return 'status.queued'

    def _refresh_pending_row_statuses(self) -> None:
        for row in range(self.tbl.rowCount()):
            key = self.tbl.internal_key_at(row, self.COL_PATH)
            if not key or key in self._transcript_by_key:
                continue
            self._set_pending_row_status(row, self._pending_status_for_key(key))

    def _set_pending_row_status(self, row: int, status_key: str) -> None:
        item = self.tbl.item(row, self.COL_STATUS)
        if item is None:
            return
        text = tr(status_key) if str(status_key or '').startswith('status.') else str(status_key or '')
        item.setText(text)
        item.setToolTip('')

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
            self.model_info.set_network_value(tr(network_key), state=network_state)
        except Exception:
            pass

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

    def _on_quick_option_changed(self, *_args) -> None:
        self._sync_options_and_autosave(refresh_targets=True)

    def _on_target_language_changed(self, *_args) -> None:
        self._session_target_language = combo_current_code(self.cmb_target_lang, default=Config.LANGUAGE_DEFAULT_UI_VALUE)
        self._sync_options_and_autosave()

    def _on_source_language_changed(self, *_args) -> None:
        self._session_source_language = combo_current_code(self.cmb_source_lang, default=Config.LANGUAGE_AUTO_VALUE)
        self._sync_options_and_autosave()

    def _gather_quick_options_patch(self) -> dict[str, Any]:
        translate_after = bool(not self.tg_mode.is_first_checked())
        output_formats = [mid for mid, cb in self._out_checks.items() if cb.isChecked()]

        audio_only = bool(self.opt_download_audio_only.isChecked())
        keep_audio = bool(self.chk_keep_url_audio.isChecked())
        keep_video = bool(self.chk_keep_url_video.isChecked())

        aext = str(self.cmb_audio_ext.currentData() or "m4a")
        vext = str(self.cmb_video_ext.currentData() or "mp4")

        return build_files_transcription_patch(
            translate_after_transcription=translate_after,
            output_formats=output_formats,
            download_audio_only=audio_only,
            url_keep_audio=keep_audio,
            url_audio_ext=aext,
            url_keep_video=keep_video,
            url_video_ext=vext,
        )

    def _build_quick_options_payload(self) -> dict[str, Any]:
        return build_files_quick_options_payload(
            transcription_patch=self._gather_quick_options_patch(),
            source_language=self._session_source_language,
            target_language=self._session_target_language,
        )

    @staticmethod
    def _on_quick_options_saved_snapshot(snap: object) -> None:
        try:
            Config.update_from_snapshot(cast(SettingsSnapshot, snap),
                                        sections=("transcription", "translation", "model"))
        except Exception:
            pass

    def _on_quick_options_save_error(self, key: str, params: dict[str, Any]) -> None:
        dialogs.show_error(self, key=key, params=params or {})

    def _fill_audio_ext_combo(self) -> None:
        self.cmb_audio_ext.clear()
        for ext in Config.DOWNLOAD_AUDIO_OUTPUT_EXTS:
            self.cmb_audio_ext.addItem(str(ext), ext)
        self.cmb_audio_ext.setCurrentIndex(0)

    def _fill_video_ext_combo(self) -> None:
        self.cmb_video_ext.clear()
        for ext in Config.DOWNLOAD_VIDEO_OUTPUT_EXTS:
            self.cmb_video_ext.addItem(str(ext), ext)
        self.cmb_video_ext.setCurrentIndex(0)

    @staticmethod
    def _get_transcription_cfg() -> dict[str, Any]:
        return Config.transcription_cfg_dict()

    @staticmethod
    def _get_transcription_model_cfg() -> dict[str, Any]:
        return current_transcription_model_cfg()

    @staticmethod
    def _get_translation_model_cfg() -> dict[str, Any]:
        return current_translation_model_cfg()

    def _refresh_target_languages_if_ready(self) -> None:
        if not self._translation_enabled():
            return
        try:
            codes = list(translation_language_codes())
        except Exception:
            codes = []
        if not codes:
            return
        if self.cmb_target_lang.count() > 1:
            return

        desired = str(self._session_target_language or self.cmb_target_lang.code() or Config.LANGUAGE_DEFAULT_UI_VALUE).strip()
        self.cmb_target_lang.rebuild()
        self.cmb_target_lang.set_code(desired)
        self._session_target_language = self.cmb_target_lang.code() or Config.LANGUAGE_DEFAULT_UI_VALUE

    # ----- Mode / preview / table layout -----

    def _set_preview_enabled(self, key: str, enabled: bool) -> None:
        row = self._row_by_key.get(str(key))
        if row is None:
            return
        w = self.tbl.cellWidget(row, self.COL_PREVIEW)
        if not w:
            return
        btn = w.findChild(QtWidgets.QAbstractButton)
        if btn:
            btn.setEnabled(bool(enabled))

    def _reset_previews(self) -> None:
        self._output_dir_by_key.clear()
        self._transcript_by_key.clear()
        for r in range(self.tbl.rowCount()):
            w = self.tbl.cellWidget(r, self.COL_PREVIEW)
            if not w:
                continue
            btn = w.findChild(QtWidgets.QAbstractButton)
            if btn:
                btn.setEnabled(False)

    def _translation_enabled(self) -> bool:
        return translation_runtime_available(model_cfg=self._get_translation_model_cfg())

    def _refresh_mode_badge(self) -> None:
        t_cfg = self._get_transcription_model_cfg()
        asr_eng_raw = str(t_cfg.get("engine_name", "none") or "none").strip()
        asr_eng_norm = asr_eng_raw.lower()
        asr_disabled = Config.is_disabled_engine_name(asr_eng_norm) or asr_eng_norm == "null"
        asr_value = self._disabled_value() if asr_disabled else asr_eng_raw

        x_cfg = self._get_translation_model_cfg()
        tr_eng_raw = str(x_cfg.get("engine_name", "none") or "none").strip()
        tr_eng_norm = tr_eng_raw.lower()
        tr_disabled = Config.is_disabled_engine_name(tr_eng_norm) or tr_eng_norm == "null"
        tr_value = self._disabled_value() if tr_disabled else tr_eng_raw

        self.model_info.set_asr_value(asr_value, state="disabled" if asr_disabled else "ready")
        self.model_info.set_translation_value(tr_value, state="disabled" if tr_disabled else "ready")

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if obj is self.tbl.viewport() and event.type() == QtCore.QEvent.Type.Resize:
            if getattr(self, "_header_mode", "") == "empty":
                self._apply_empty_column_widths()
        return super().eventFilter(obj, event)

    def _sync_options_ui(self) -> None:
        running = self._is_transcription_running()
        model_ready = self.pipe is not None

        tr_enabled = self._translation_enabled()

        if not model_ready:
            self.lbl_mode.setEnabled(False)
            self.tg_mode.setEnabled(False)
            self.tg_mode.set_second_enabled(False)

            self.lbl_output.setEnabled(False)
            self.out_checks_host.setEnabled(False)

            self.lbl_source_lang.setEnabled(False)
            self.cmb_source_lang.setEnabled(False)
            self.lbl_target_lang.setEnabled(False)
            self.cmb_target_lang.setEnabled(False)

            self.opt_download_audio_only.setEnabled(False)
            self.chk_keep_url_audio.setEnabled(False)
            self.cmb_audio_ext.setEnabled(False)
            self.chk_keep_url_video.setEnabled(False)
            self.cmb_video_ext.setEnabled(False)
            return

        self.tg_mode.setEnabled(not running)
        self.tg_mode.set_second_enabled(bool(tr_enabled and (not running)))
        if not tr_enabled:
            if not self.tg_mode.is_first_checked():
                self.tg_mode.set_first_checked(True)

        translate_mode = bool(not self.tg_mode.is_first_checked()) and tr_enabled
        self.lbl_target_lang.setEnabled((not running) and translate_mode)
        self.cmb_target_lang.setEnabled((not running) and translate_mode)

        self.lbl_source_lang.setEnabled(not running)
        self.cmb_source_lang.setEnabled(not running)

        audio_only = bool(self.opt_download_audio_only.isChecked())

        self.chk_keep_url_video.setEnabled((not running) and (not audio_only))
        self.cmb_video_ext.setEnabled((not running) and (not audio_only) and self.chk_keep_url_video.isChecked())

        self.chk_keep_url_audio.setEnabled(not running)
        self.cmb_audio_ext.setEnabled((not running) and self.chk_keep_url_audio.isChecked())

        self.opt_download_audio_only.setEnabled(not running)
        self.lbl_mode.setEnabled(not running)
        self.lbl_output.setEnabled(not running)
        self.out_checks_host.setEnabled(not running)

    def _status_width(self) -> int:
        cfg = self._ui
        labels = [str(tr('files.details.col.status') or '').strip()]
        for key in (
            'status.queued',
            'status.offline',
            'status.downloading',
            'status.transcribing',
            'status.processing',
            'status.saved',
            'status.done',
            'status.error',
        ):
            labels.append(str(tr(key) or '').strip())
        metrics = QtGui.QFontMetrics(self.tbl.font())
        text_width = max((metrics.horizontalAdvance(label) for label in labels if label), default=0)
        min_w = int(cfg.control_min_w + cfg.pad_x_l)
        pad_w = int(cfg.pad_x_l + cfg.pad_y_l + cfg.space_l - 1)
        max_w = int(cfg.control_min_w + cfg.control_min_h * 3 + max(0, cfg.space_s - 1))
        return max(
            min_w,
            min(text_width + pad_w + metrics.horizontalAdvance(' (100%)'), max_w),
        )

    def _preview_width(self) -> int:
        cfg = self._ui
        return int(cfg.control_min_h * 2 + cfg.margin * 3)

    def _apply_empty_header_mode(self) -> None:
        self._header_mode = 'empty'
        cfg = self._ui
        status_width = self._status_width()
        preview_width = self._preview_width()
        title_min_w = int(cfg.control_min_w + cfg.margin * 5)
        duration_min_w = int(preview_width)
        source_min_w = int(preview_width + max(0, cfg.space_s - 1))
        language_min_w = int(cfg.control_min_w + cfg.pad_x_l)
        path_min_w = int(cfg.control_min_w + cfg.control_min_h * 3 + max(0, cfg.space_s - 1))
        self.tbl.reset_header_user_widths()
        self.tbl.apply_weighted_header_layout(
            check_col=self.COL_CHECK,
            number_col=self.COL_NO,
            fill_column=self.COL_PATH,
            weights={
                self.COL_TITLE: 2,
                self.COL_DUR: 1,
                self.COL_SRC: 1,
                self.COL_LANG: 1,
                self.COL_PATH: 3,
            },
            min_widths={
                self.COL_TITLE: title_min_w,
                self.COL_DUR: duration_min_w,
                self.COL_SRC: source_min_w,
                self.COL_LANG: language_min_w,
                self.COL_PATH: path_min_w,
            },
            fixed_widths={
                self.COL_STATUS: status_width,
                self.COL_PREVIEW: preview_width,
            },
        )

    def _apply_populated_header_mode(self) -> None:
        self._header_mode = 'populated'
        cfg = self._ui
        status_width = self._status_width()
        preview_width = self._preview_width()
        language_min_w = int(cfg.control_min_w + cfg.pad_x_l)
        language_fallback_w = int(language_min_w + cfg.margin * 2)
        language_pad_w = int(cfg.margin)
        language_cap_w = int(cfg.control_min_w + cfg.control_min_h + cfg.margin * 3 + max(0, cfg.space_s - 1))
        language_floor_w = int(cfg.control_min_w + cfg.margin * 5)
        language_extra_w = int(cfg.pad_x_l)
        title_min_w = int(cfg.control_min_w + cfg.margin * 6)
        duration_min_w = int(preview_width)
        source_min_w = int(preview_width + max(0, cfg.space_s - 1))
        path_min_w = int(cfg.control_min_w + cfg.control_min_h * 3 + max(0, cfg.space_s - 1))
        status_min_w = int(cfg.control_min_w + cfg.pad_x_l)
        fit_padding = int(cfg.margin + cfg.pad_x_l)
        language_width = self.tbl.column_widget_width_hint(
            self.COL_LANG,
            fallback=language_fallback_w,
            pad=language_pad_w,
            cap=language_cap_w,
        )
        self.tbl.apply_content_header_layout(
            check_col=self.COL_CHECK,
            number_col=self.COL_NO,
            fill_column=self.COL_PATH,
            stretch_weights={
                self.COL_TITLE: 2,
                self.COL_PATH: 3,
            },
            fit_columns=[self.COL_DUR, self.COL_SRC],
            preferred_widths={
                self.COL_LANG: language_width,
                self.COL_STATUS: status_width,
            },
            min_widths={
                self.COL_TITLE: title_min_w,
                self.COL_DUR: duration_min_w,
                self.COL_SRC: source_min_w,
                self.COL_LANG: language_min_w,
                self.COL_PATH: path_min_w,
                self.COL_STATUS: status_min_w,
            },
            max_widths={
                self.COL_LANG: max(language_width + language_extra_w, language_floor_w),
                self.COL_STATUS: int(cfg.control_min_w + cfg.control_min_h * 3 + max(0, cfg.space_s - 1)),
            },
            fixed_widths={
                self.COL_PREVIEW: preview_width,
            },
            fit_padding=fit_padding,
        )

    def _apply_empty_column_widths(self) -> None:
        if getattr(self, '_header_mode', '') == 'empty':
            self.tbl.reapply_header_layout()

    # ----- Source row helpers -----

    def _checkbox_at_row(self, row: int) -> QtWidgets.QCheckBox | None:
        return self.tbl.checkbox_at(row, self.COL_CHECK)

    def _on_preview_requested(self, key: str) -> None:
        key = str(key or "").strip()
        if not key:
            return
        out_dir = self._output_dir_by_key.get(key)
        if not out_dir:
            return
        try:
            p = Path(out_dir)
            p.mkdir(parents=True, exist_ok=True)
            open_local_path(p)
        except Exception as e:
            _LOG.exception("Opening the preview output folder failed. key=%s path=%s", key, out_dir)
            dialogs.show_error(self, key="dialog.error.unexpected", params={"msg": str(e)})

    @QtCore.pyqtSlot(int)
    def _on_lang_combo_changed(self, idx: int) -> None:
        w = self.sender()
        if not isinstance(w, QtWidgets.QComboBox):
            return

        key = str(w.property("internal_key") or "").strip()
        if not key:
            return

        codes = w.property("lang_codes") or [None]
        try:
            code = codes[idx] if 0 <= idx < len(codes) else None
        except Exception:
            code = None

        self._audio_lang_by_key[key] = normalize_lang_code(code, drop_region=True) or None

    def _update_audio_tracks(self, row: int, meta: dict[str, Any]) -> None:
        default_text = tr("down.select.audio_track.default")
        lang_codes = self.tbl.update_audio_tracks(row=row, col=self.COL_LANG, meta=meta, default_text=default_text)

        internal_key = self.tbl.internal_key_at(row, self.COL_PATH)
        if internal_key:
            w = self.tbl.combo_at(row, self.COL_LANG)
            if isinstance(w, QtWidgets.QComboBox):
                w.setProperty("internal_key", internal_key)
                w.setProperty("lang_codes", lang_codes)
            self._audio_lang_by_key.setdefault(internal_key, None)
            sel = self._audio_lang_by_key.get(internal_key)
            if sel and isinstance(w, QtWidgets.QComboBox):
                try:
                    idx = list(lang_codes).index(sel) if sel in list(lang_codes) else -1
                except Exception:
                    idx = -1
                if idx < 0:
                    base = normalize_lang_code(sel, drop_region=True)
                    for j, c in enumerate(list(lang_codes)):
                        if c and normalize_lang_code(c, drop_region=True) == base:
                            idx = j
                            break
                if idx >= 0:
                    w.blockSignals(True)
                    w.setCurrentIndex(int(idx))
                    w.blockSignals(False)

        self._update_buttons()

    def _reset_url_rows_to_original_keys(self) -> None:
        for r in range(self.tbl.rowCount()):
            if self.tbl.text_at(r, self.COL_SRC) != tr("files.source.url"):
                continue

            it_path = self.tbl.item(r, self.COL_PATH)
            if not it_path:
                continue

            display_url = (it_path.text() or "").strip()
            if not display_url:
                continue

            current_internal = self.tbl.internal_key_at(r, self.COL_PATH)
            if current_internal == display_url:
                it_path.setData(QtCore.Qt.ItemDataRole.UserRole, display_url)
                self._display_path_by_key[display_url] = display_url
                self._origin_src_by_key[display_url] = "url"
                self._row_by_key[display_url] = r
                w = self.tbl.combo_at(r, self.COL_LANG)
                if isinstance(w, QtWidgets.QComboBox):
                    w.setProperty("internal_key", display_url)
                self._audio_lang_by_key.setdefault(display_url, None)
                continue

            old_key = current_internal
            new_key = display_url

            if old_key in self._keys:
                self._keys.discard(old_key)
            self._keys.add(new_key)

            old_row = self._row_by_key.pop(old_key, None)
            self._row_by_key[new_key] = old_row if old_row is not None else r

            if old_key in self._origin_src_by_key:
                self._origin_src_by_key.pop(old_key, None)
            self._origin_src_by_key[new_key] = "url"

            if old_key in self._display_path_by_key:
                self._display_path_by_key.pop(old_key, None)
            self._display_path_by_key[new_key] = new_key

            if old_key in self._audio_lang_by_key:
                self._audio_lang_by_key[new_key] = self._audio_lang_by_key.pop(old_key)

            w = self.tbl.combo_at(r, self.COL_LANG)
            if isinstance(w, QtWidgets.QComboBox):
                w.setProperty("internal_key", new_key)

            self._transcript_by_key.pop(old_key, None)

            it_path.setData(QtCore.Qt.ItemDataRole.UserRole, new_key)
            it_path.setToolTip(new_key)
            it_path.setText(new_key)

    @QtCore.pyqtSlot(int, int)
    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if row < 0:
            return

        mods = QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.KeyboardModifier.ControlModifier):
            return

        if col not in (self.COL_SRC, self.COL_PATH):
            return

        target = self.tbl.text_at(row, self.COL_PATH)
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

            if os.name == "nt" and p.is_file():
                import subprocess
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                open_local_path(p.parent if p.is_file() else p)
        except Exception:
            pass

    def _source_keys_in_table(self) -> list[str]:
        keys: list[str] = []
        for r in range(self.tbl.rowCount()):
            key = self.tbl.internal_key_at(r, self.COL_PATH)
            if key:
                keys.append(key)
        return keys

    def _insert_placeholder_row(self, key: str, *, src_label: str) -> None:
        if self.tbl.rowCount() == 0:
            self._apply_populated_header_mode()

        row = self.tbl.rowCount()
        self.tbl.insertRow(row)

        self.tbl.setCellWidget(row, self.COL_CHECK, self.tbl.make_checkbox_cell(on_changed=self._update_buttons))

        it_no = QtWidgets.QTableWidgetItem(str(row + 1))
        it_no.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
        self.tbl.setItem(row, self.COL_NO, it_no)

        it_title = QtWidgets.QTableWidgetItem(tr("common.loading"))
        self.tbl.setItem(row, self.COL_TITLE, it_title)

        it_dur = QtWidgets.QTableWidgetItem(tr("common.na"))
        it_dur.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
        self.tbl.setItem(row, self.COL_DUR, it_dur)

        it_src = QtWidgets.QTableWidgetItem(src_label)
        it_src.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
        self.tbl.setItem(row, self.COL_SRC, it_src)
        it_src.setToolTip(src_label)

        lang_cb = self.tbl.make_audio_track_combo(
            internal_key=key,
            default_text=tr("down.select.audio_track.default"),
            on_changed=self._on_lang_combo_changed,
            enabled=False,
        )
        self.tbl.setCellWidget(row, self.COL_LANG, lang_cb)
        self._audio_lang_by_key.setdefault(key, None)

        it_path = QtWidgets.QTableWidgetItem(key)
        it_path.setToolTip(key)
        it_path.setData(QtCore.Qt.ItemDataRole.UserRole, key)
        self.tbl.setItem(row, self.COL_PATH, it_path)

        pending_status = self._pending_status_for_key(key)
        it_status = QtWidgets.QTableWidgetItem(tr(pending_status))
        it_status.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
        self.tbl.setItem(row, self.COL_STATUS, it_status)

        self.tbl.setCellWidget(
            row,
            self.COL_PREVIEW,
            self.tbl.make_preview_cell(
                internal_key=key,
                tooltip=tr("files.preview.open_folder"),
                enabled=False,
            ),
        )

        self._row_by_key[key] = row
        self._origin_src_by_key[key] = src_label
        self._display_path_by_key[key] = key

    def _update_row_from_meta(self, row: int, meta: dict[str, Any]) -> None:
        if row < 0 or row >= self.tbl.rowCount():
            return

        title = str(meta.get("name") or meta.get("title") or tr("common.na"))
        duration = meta.get("duration")

        self.tbl.item(row, self.COL_TITLE).setText(title)
        txt = format_hms(duration, blank_for_none=True)
        self.tbl.item(row, self.COL_DUR).setText(txt or tr("common.na"))
        self._update_audio_tracks(row, meta)

    def _start_metadata_for(self, keys: list[str]) -> None:
        if not keys:
            return
        if self._meta_thread is not None:
            try:
                self._meta_thread.requestInterruption()
            except Exception:
                pass

        entries = []
        for k in keys:
            entries.append({"type": ("url" if is_url_source(k) else "file"), "value": k})

        self._meta_thread = QtCore.QThread(self)
        self._meta_worker = MediaProbeWorker(entries)
        self._meta_worker.moveToThread(self._meta_thread)

        self._meta_thread.started.connect(self._meta_worker.run)
        self._meta_worker.table_ready.connect(self._on_meta_rows_ready)
        self._meta_worker.item_error.connect(self._on_meta_item_error)

        self._meta_worker.finished.connect(self._meta_thread.quit)
        self._meta_worker.finished.connect(self._meta_worker.deleteLater)
        self._meta_thread.finished.connect(self._meta_thread.deleteLater)
        self._meta_thread.finished.connect(self._on_meta_finished)

        self._meta_thread.start()

    # ----- Source collection state -----

    def _update_buttons(self) -> None:
        has_items = self.tbl.rowCount() > 0
        has_sel = bool(self.tbl.rows_for_removal(self.COL_CHECK))
        model_ready = self.pipe is not None
        running = self._is_transcription_running()

        self.src_edit.setEnabled((not running) and model_ready)

        self.action_bar.set_primary_enabled(has_items and model_ready and not running)
        self.action_bar.set_secondary_enabled(running)

        self.btn_clear_list.setEnabled(has_items and not running and model_ready)
        self.btn_remove_selected.setEnabled(has_sel and not running and model_ready)

        self.btn_src_add.setEnabled(not running and model_ready)
        self.btn_open_output.setEnabled(True)

        self.btn_add_files.setEnabled(not running and model_ready)
        self.btn_add_folder.setEnabled(not running and model_ready)

        self.tbl.setEnabled(model_ready)
        self.tbl.setAcceptDrops(bool(model_ready and (not running)))
        if model_ready and (not running):
            self.tbl.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        else:
            self.tbl.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)

        if running:
            self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        else:
            self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

        self.tbl.set_header_checkbox_enabled(bool((not running) and model_ready and has_items))

        for r in range(self.tbl.rowCount()):
            cb = self.tbl.checkbox_at(r, self.COL_CHECK)
            if cb is not None:
                cb.setEnabled(bool((not running) and model_ready))

            w = self.tbl.combo_at(r, self.COL_LANG)
            if isinstance(w, QtWidgets.QComboBox):
                can_choose = bool(w.property("has_choices"))
                w.setEnabled(bool(can_choose and (not running) and model_ready))

        self._sync_options_ui()

    # ----- Source collection actions -----

    def _discard_source_state(self, key: str) -> None:
        source_key = str(key or "").strip()
        if not source_key:
            return
        self._keys.discard(source_key)
        self._row_by_key.pop(source_key, None)
        self._transcript_by_key.pop(source_key, None)
        self._origin_src_by_key.pop(source_key, None)
        self._display_path_by_key.pop(source_key, None)
        self._audio_lang_by_key.pop(source_key, None)
        self._status_base_by_key.pop(source_key, None)
        self._pct_by_key.pop(source_key, None)
        self._error_by_key.pop(source_key, None)
        self._output_dir_by_key.pop(source_key, None)

    def _clear_source_collections(self) -> None:
        self._keys.clear()
        self._row_by_key.clear()
        self._transcript_by_key.clear()
        self._origin_src_by_key.clear()
        self._display_path_by_key.clear()
        self._audio_lang_by_key.clear()
        self._status_base_by_key.clear()
        self._pct_by_key.clear()
        self._error_by_key.clear()
        self._output_dir_by_key.clear()

    def _finalize_source_rows_changed(self) -> None:
        self._refresh_order_numbers()
        self._update_buttons()

    def _try_parse_and_validate_manual_source(self) -> dict[str, Any] | None:
        parsed = parse_source_input(self.src_edit.text())
        if not parsed.get("ok", False):
            err = str(parsed.get("error") or "")
            if err in ("not_found", "unsupported"):
                dialogs.show_info(
                    self,
                    title=tr("dialog.info.title"),
                    header=tr("dialog.info.header"),
                    message=tr("dialog.info.source_missing"),
                )
            return None

        key = str(parsed.get("key") or "").strip()
        if not key:
            return None

        if str(parsed.get("type") or "") == "url" and is_playlist_url(key):
            dialogs.info_playlist_not_supported(self)
            return None

        parsed["key"] = key
        return parsed

    def _collect_added_local_keys(self, paths: list[str]) -> list[str]:
        added: list[str] = []
        for key in collect_media_files(paths):
            ok, key, _dup = try_add_source_key(self._keys, key)
            if not ok:
                continue
            self._insert_placeholder_row(key, src_label=tr("files.source.local"))
            added.append(key)
        return added

    def _reset_sources_view_state(self) -> None:
        self.tbl.setRowCount(0)
        self.action_bar.reset()
        self._apply_empty_header_mode()
        self._update_buttons()

    def _on_add_clicked(self) -> None:
        parsed = self._try_parse_and_validate_manual_source()
        if not parsed:
            return

        key = str(parsed.get("key") or "").strip()
        ok, key, dup = try_add_source_key(self._keys, key)
        if not ok:
            if dup:
                dialogs.show_info(
                    self,
                    title=tr("dialog.info.title"),
                    header=tr("dialog.info.header"),
                    message=tr("status.skipped"),
                )
            return

        src_label = tr("files.source.url") if str(parsed.get("type")) == "url" else tr("files.source.local")
        self._insert_placeholder_row(key, src_label=src_label)
        self._start_metadata_for([key])

        self.src_edit.clear()
        self._finalize_source_rows_changed()

    def _on_paths_dropped(self, paths: list[str]) -> None:
        added = self._collect_added_local_keys(paths)
        if added:
            self._start_metadata_for(added[:30])
        self._finalize_source_rows_changed()

    def _remove_rows(self, rows: list[int]) -> None:
        if not rows:
            return
        for r in sorted(set(rows), reverse=True):
            key = self.tbl.internal_key_at(r, self.COL_PATH)
            if key:
                self._discard_source_state(key)
            self.tbl.removeRow(r)

        if self.tbl.rowCount() == 0:
            self._apply_empty_header_mode()

        self._finalize_source_rows_changed()

    def _refresh_order_numbers(self) -> None:
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, self.COL_NO)
            if it:
                it.setText(str(r + 1))

    def _open_transcript_for_row(self, row: int) -> None:
        key = self.tbl.internal_key_at(row, self.COL_PATH)
        if not key:
            return
        path = self._transcript_by_key.get(key)
        if not path:
            return
        try:
            open_local_path(Path(path))
        except Exception:
            pass

    def _on_add_files_clicked(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            tr("files.add_files"),
            "",
            tr("files.details.filters.audio_video"),
        )
        if not files:
            return
        self._on_paths_dropped(files)

    def _on_add_folder_clicked(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, tr("files.add_folder"))
        if not folder:
            return
        p = Path(folder)
        if not p.exists() or not p.is_dir():
            return

        exts = {e.lower() for e in Config.files_media_input_file_exts()}
        files: list[str] = []
        for fp in p.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in exts:
                files.append(str(fp))
        self._on_paths_dropped(files)

    def _on_remove_selected(self) -> None:
        rows = self.tbl.rows_for_removal(self.COL_CHECK)
        self._remove_rows(rows)

    def _on_clear_clicked(self) -> None:
        self._clear_source_collections()
        self._reset_sources_view_state()

    def _open_output_folder(self) -> None:
        try:
            out_dir = Config.TRANSCRIPTIONS_DIR
            out_dir.mkdir(parents=True, exist_ok=True)
            open_local_path(out_dir)
        except Exception as e:
            _LOG.exception("Opening the transcriptions output folder failed. path=%s", Config.TRANSCRIPTIONS_DIR)
            dialogs.show_error(self, key="dialog.error.unexpected", params={"msg": str(e)})

    # ----- Transcription flow -----

    def _can_start_transcription(self) -> bool:
        if self._is_transcription_running() or self._transcribe_worker is not None:
            return False
        if not self.pipe:
            return False
        if getattr(Config, "SETTINGS", None) is None:
            return False
        return True

    def _prepare_transcription_entries(self) -> list[dict[str, Any]]:
        self._reset_url_rows_to_original_keys()
        self._refresh_target_languages_if_ready()
        self._reset_previews()
        return build_entries(self._source_keys_in_table(), self._audio_lang_by_key)

    def _reset_transcription_run_state(self, run_keys: list[str]) -> None:
        self._pct_by_key = {}
        self._status_base_by_key = {}
        self._error_by_key = {}
        self._output_dir_by_key = {}
        self._transcript_by_key = {}

        for key in run_keys:
            row = self._row_by_key.get(key)
            if row is None:
                continue
            it = self.tbl.item(row, self.COL_STATUS)
            if it:
                it.setText("-")

        self.action_bar.reset()
        self._was_cancelled = False
        self._cancel_notice_pending = False
        self._conflict_apply_all_action = None
        self._conflict_apply_all_new_base = None

    def _build_transcription_overrides(self) -> dict[str, Any]:
        return build_transcription_runtime_overrides(
            source_language=str(self._session_source_language or Config.LANGUAGE_AUTO_VALUE),
            target_language=str(self._session_target_language or Config.LANGUAGE_AUTO_VALUE),
            translate_after_transcription=bool(
                (not self.tg_mode.is_first_checked()) and self._translation_enabled()
            ),
            ui_language=Translator.current_language(),
            cfg_target=Config.translation_target_language(),
            supported=translation_language_codes(),
        )

    def _create_transcription_worker(self, entries: list[dict[str, Any]], overrides: dict[str, Any]) -> None:
        self._transcribe_worker = TranscriptionWorker(pipe=self.pipe, entries=entries, overrides=overrides)

    def _wire_transcription_worker_signals(self, worker: TranscriptionWorker) -> None:
        if worker is None:
            return

        worker.progress.connect(self._on_global_progress)
        worker.item_status.connect(self._on_item_status)
        worker.item_progress.connect(self._on_item_progress)
        worker.item_path_update.connect(self._on_item_path_update)
        worker.transcript_ready.connect(self._on_transcript_ready)
        worker.item_error.connect(self._on_item_error)
        worker.item_output_dir.connect(self._on_item_output_dir)
        worker.conflict_check.connect(self._on_conflict_check)
        worker.session_done.connect(self._on_session_done)

    def _request_transcription_cancel(self) -> None:
        try:
            if self._is_transcription_running():
                self._transcribe_runner.cancel()
            elif self._transcribe_worker is not None:
                self._transcribe_worker.cancel()
        except Exception:
            pass

    def _reset_non_finished_rows_after_cancel(self) -> None:
        for row in range(self.tbl.rowCount()):
            key = self.tbl.internal_key_at(row, self.COL_PATH)
            finished = bool(key and self._transcript_by_key.get(key))
            if finished:
                continue
            it = self.tbl.item(row, self.COL_STATUS)
            if it:
                it.setText("-")

    def _on_start_clicked(self) -> None:
        if not self._can_start_transcription():
            return

        entries = self._prepare_transcription_entries()
        if not entries:
            return

        run_keys = [str(e.get("src")) for e in entries if isinstance(e, dict) and e.get("src")]
        self._reset_transcription_run_state(run_keys)

        overrides = self._build_transcription_overrides()
        self._create_transcription_worker(entries, overrides)
        if self._transcribe_worker is None:
            return
        self._transcribe_runner.start(
            self._transcribe_worker,
            connect=self._wire_transcription_worker_signals,
            on_finished=self._on_transcribe_finished,
        )
        self._update_buttons()

    def _on_cancel_clicked(self) -> None:
        if not self._transcribe_worker:
            return
        if not dialogs.ask_cancel(self):
            return

        self._cancel_notice_pending = True
        self._request_transcription_cancel()
        self._update_buttons()

    # ----- Worker events / status rendering -----

    @QtCore.pyqtSlot(int)
    def _on_global_progress(self, value: int) -> None:
        if self._was_cancelled:
            return
        self.action_bar.set_progress(int(value))

    @QtCore.pyqtSlot(list)
    def _on_meta_rows_ready(self, batch: list[dict[str, Any]]) -> None:
        for meta in batch:
            key = str(meta.get("path") or "").strip()
            if not key:
                continue
            row = self._row_by_key.get(key)
            if row is None:
                continue
            self._update_row_from_meta(row, meta)

    @QtCore.pyqtSlot(str, str, dict)
    def _on_meta_item_error(self, key: str, err_key: str, params: dict[str, Any]) -> None:
        k = str(key or "").strip()
        row = self._row_by_key.get(k) if k else None
        if row is not None:
            self._remove_rows([int(row)])
        dialogs.show_error(self, err_key, params or {})

    def _on_meta_finished(self) -> None:
        self._meta_thread = None
        self._meta_worker = None

    def _on_transcribe_finished(self) -> None:
        self.action_bar.reset()
        if self._was_cancelled:
            self._reset_non_finished_rows_after_cancel()
            self._reset_url_rows_to_original_keys()
            if self._cancel_notice_pending:
                dialogs.show_info(
                    self,
                    title=tr("dialog.info.title"),
                    header=tr("dialog.info.header"),
                    message=tr("dialog.info.cancelled"),
                )
        self._cancel_notice_pending = False
        self._transcribe_worker = None
        self._update_buttons()

    @QtCore.pyqtSlot(str, bool, bool, bool)
    def _on_session_done(self, session_dir: str, processed_any: bool, had_errors: bool, was_cancelled: bool) -> None:
        self._was_cancelled = bool(was_cancelled)
        if not processed_any or had_errors or was_cancelled:
            return
        try:
            if dialogs.ask_open_transcripts_folder(self, session_dir):
                p = Path(session_dir)
                p.mkdir(parents=True, exist_ok=True)
                open_local_path(p)
        except Exception:
            pass
        finally:
            self.action_bar.reset()

    @staticmethod
    def _normalize_status_base_key(status: str) -> str:
        try:
            return re.sub(r"\s*\(\d+%\)\s*$", "", str(status or "")).strip()
        except Exception:
            return str(status or "").strip()

    @staticmethod
    def _is_terminal_status(status: str) -> bool:
        return status in ("status.done", "status.saved", "status.skipped", "status.error")

    @staticmethod
    def _status_display_text(base_key: str, raw_status: str) -> str:
        if str(base_key or "").startswith("status."):
            return tr(base_key)
        return str(base_key or raw_status or "")

    def _should_reset_progress_for_status_change(self, prev_base: str | None, new_base: str, status: str) -> bool:
        return bool(prev_base and prev_base != new_base and not self._is_terminal_status(status))

    def _render_row_status_text(self, key: str, row: int, status: str, base_text: str) -> None:
        it = self.tbl.item(row, self.COL_STATUS)
        if it is None:
            return

        pct = self._pct_by_key.get(key)
        text = base_text or status
        if status not in ("status.done", "status.saved") and pct is not None and 0 < int(pct) < 100 and "(" not in status:
            text = f"{base_text} ({int(pct)}%)"
        it.setText(text)

    def _apply_terminal_status_state(self, key: str, status: str) -> None:
        if status in ("status.done", "status.saved"):
            self._pct_by_key[key] = 100
            if self._output_dir_by_key.get(key):
                self._set_preview_enabled(key, True)
            return

        if status in ("status.skipped", "status.error"):
            self._pct_by_key.pop(key, None)
            self._output_dir_by_key.pop(key, None)
            self._transcript_by_key.pop(key, None)
            self._set_preview_enabled(key, False)

    def _compose_progress_status_text(self, base_key: str, pct: int) -> str:
        base_text = self._status_display_text(base_key, base_key)
        text = base_text
        show_zero_for = {"status.downloading", "status.transcribing", "status.saving"}
        if pct < 100 and (pct > 0 or base_key in show_zero_for):
            text = f"{base_text} ({pct}%)"
        return text

    @QtCore.pyqtSlot(str, str)
    def _on_item_status(self, key: str, status: str) -> None:
        if self._was_cancelled:
            return
        key = str(key)
        status = str(status or "").strip()

        row = self._row_by_key.get(key)
        if row is None:
            return

        base_key = self._normalize_status_base_key(status)
        base_text = self._status_display_text(base_key, status)
        prev_base = self._status_base_by_key.get(key)
        if self._should_reset_progress_for_status_change(prev_base, base_key, status):
            self._pct_by_key.pop(key, None)

        if base_text:
            self._status_base_by_key[key] = base_key

        self._apply_terminal_status_state(key, status)
        self._render_row_status_text(key, row, status, base_text)

    @QtCore.pyqtSlot(str, int)
    def _on_item_progress(self, key: str, pct: int) -> None:
        if self._was_cancelled:
            return
        key = str(key)
        pct = max(0, min(100, int(pct)))
        self._pct_by_key[key] = pct

        row = self._row_by_key.get(key)
        if row is None:
            return

        base_key = self._status_base_by_key.get(key) or "status.processing"
        text = self._compose_progress_status_text(base_key, pct)

        it = self.tbl.item(row, self.COL_STATUS)
        if it:
            it.setText(text)

    @QtCore.pyqtSlot(str, str, dict)
    def _on_item_error(self, key: str, err_key: str, params: dict[str, Any]) -> None:
        if self._was_cancelled:
            return

        ekey = str(err_key or "error.generic").strip() or "error.generic"
        eparams = dict(params or {})
        self._error_by_key[str(key)] = (ekey, eparams)

        row = self._row_by_key.get(str(key))
        if row is None:
            return

        it = self.tbl.item(row, self.COL_STATUS)
        if it:
            it.setText(tr("status.error"))
            it.setToolTip(tr(ekey, **eparams))

    @QtCore.pyqtSlot(str, str)
    def _on_item_output_dir(self, key: str, out_dir: str) -> None:
        self._output_dir_by_key[str(key)] = str(out_dir or "").strip()
        row = self._row_by_key.get(str(key))
        if row is None:
            return
        w = self.tbl.cellWidget(row, self.COL_PREVIEW)
        if w:
            btn = w.findChild(QtWidgets.QAbstractButton)
            if btn:
                btn.setProperty("internal_key", str(key))

    # ----- Source retargeting / runtime map helpers -----

    def _migrate_source_runtime_maps(self, old_key: str, new_key: str, row: int) -> str:
        display = self._display_path_by_key.get(old_key, old_key)
        src_label = self._origin_src_by_key.get(old_key)

        self._keys.discard(old_key)
        self._keys.add(new_key)
        self._row_by_key[new_key] = row

        for mapping in (
            self._transcript_by_key,
            self._audio_lang_by_key,
            self._status_base_by_key,
            self._pct_by_key,
            self._error_by_key,
            self._output_dir_by_key,
        ):
            if old_key in mapping:
                mapping[new_key] = mapping.pop(old_key)

        if src_label:
            self._origin_src_by_key[new_key] = src_label
        self._origin_src_by_key.pop(old_key, None)

        self._display_path_by_key[new_key] = display
        self._display_path_by_key.pop(old_key, None)
        return display

    def _retarget_source_row_widgets(self, row: int, new_key: str) -> None:
        w = self.tbl.combo_at(row, self.COL_LANG)
        if isinstance(w, QtWidgets.QComboBox):
            w.setProperty("internal_key", new_key)

    def _apply_source_row_path_display(self, row: int, new_key: str, display: str) -> None:
        it_path = self.tbl.item(row, self.COL_PATH)
        if it_path is None:
            return
        it_path.setData(QtCore.Qt.ItemDataRole.UserRole, new_key)
        it_path.setText(display)
        it_path.setToolTip(display)

    @QtCore.pyqtSlot(str, str)
    def _on_item_path_update(self, old_key: str, new_key: str) -> None:
        row = self._row_by_key.pop(old_key, None)
        if row is None:
            return

        display = self._migrate_source_runtime_maps(old_key, new_key, row)
        self._retarget_source_row_widgets(row, new_key)
        self._apply_source_row_path_display(row, new_key, display)
        self._start_metadata_for([new_key])

    @QtCore.pyqtSlot(str, str)
    def _on_transcript_ready(self, key: str, transcript_path: str) -> None:
        self._transcript_by_key[str(key)] = str(transcript_path)

    @QtCore.pyqtSlot(str, str)
    def _on_conflict_check(self, stem: str, _existing_dir: str) -> None:
        worker = self._transcribe_worker
        if self._was_cancelled or self._cancel_notice_pending:
            if worker is not None:
                worker.on_conflict_decided("skip", "")
            return

        try:
            if self._conflict_apply_all_action:
                action = self._conflict_apply_all_action
                new_stem = self._conflict_apply_all_new_base or stem if action == "new" else ""
                if worker is not None:
                    worker.on_conflict_decided(action, new_stem)
                return

            action, new_stem, apply_all = dialogs.ask_conflict(self, stem)
            if apply_all and action != "new":
                self._conflict_apply_all_action = action
                self._conflict_apply_all_new_base = None

            if worker is not None:
                worker.on_conflict_decided(action, new_stem)
        except Exception:
            if worker is not None:
                worker.on_conflict_decided("skip", "")

    @staticmethod
    def _on_anchor_clicked(url: QtCore.QUrl) -> None:
        try:
            u = url.toString()
            if not u:
                return
            if u.startswith("file://"):
                p = u.replace("file://", "", 1)
                open_local_path(Path(p))
                return
            open_external_url(u)
        except Exception:
            pass

    def _apply_boot_model_state(self) -> None:
        """Apply model readiness from boot context (no in-panel model loading)."""
        self.pipe = self._boot_ctx.get("transcription_pipeline")
        if self.pipe is not None:
            self._on_model_ready(self.pipe)
            return

        model_cfg = current_transcription_model_cfg()
        engine = str(model_cfg.get("engine_name") or "none").strip().lower()

        if Config.is_disabled_engine_name(engine) or engine == "null":
            self.model_info.set_summary_status(tr("files.runtime.status_disabled"), state="disabled")
            self.model_info.set_summary_icon(
                self._status_icon(
                    "status_info",
                    self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation),
                )
            )
        else:
            self.model_info.set_summary_status(tr("files.runtime.status_missing"), state="missing")
            self.model_info.set_summary_icon(
                self._status_icon(
                    "status_error",
                    self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxCritical),
                )
            )

        self.model_info.set_device_value(str(Config.DEVICE_FRIENDLY_NAME or tr("common.na")))
        self._refresh_mode_badge()
        self._update_buttons()

    def _on_model_ready(self, pipe) -> None:
        self.pipe = pipe
        self.model_info.set_summary_status(tr("files.runtime.status_ready"), state="ready")
        self.model_info.set_summary_icon(
            self._status_icon(
                "status_ready",
                self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton),
            )
        )
        self.model_info.set_device_value(str(Config.DEVICE_FRIENDLY_NAME or tr("common.na")))
        self._refresh_mode_badge()
        self._update_buttons()
