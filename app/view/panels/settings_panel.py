# app/view/panels/settings_panel.py
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets
from app.view.ui_config import (
    build_layout_host,
    enable_styled_background,
    open_local_path,
    setup_button,
    setup_combo,
    setup_layout,
    setup_spinbox,
    system_theme_key,
    ui,
)
from app.view.components.hint_popup import InfoButton
from app.view.components.popup_combo import PopupComboBox, set_combo_data

from app.controller.tasks.settings_task import SettingsWorker
from app.model.config.app_config import AppConfig as Config
from app.controller.support.localization import tr, list_locales
from app.view import dialogs
from app.view.components.choice_toggle import ChoiceToggle
from app.view.components.section_group import SectionGroup
from app.controller.support.task_thread_runner import TaskThreadRunner
from app.controller.support.options_autosave_controller import OptionsAutosaveController
from app.model.services.ai_models_service import local_model_names_for_task

class _YesNoToggle(ChoiceToggle):
    """Convenience yes/no toggle."""

    def __init__(
            self,
            *,
            yes_text: str,
            no_text: str,
            height: int,
            parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(
            first_text=yes_text,
            second_text=no_text,
            height=height,
            parent=parent,
        )

class SettingsPanel(QtWidgets.QWidget):
    """Settings page with app, engine and downloader configuration."""

    _RESTART_SENSITIVE_KEYS: Tuple[Tuple[str, ...], ...] = (
        ("app", "language"),
        ("app", "theme"),
        ("app", "logging", "enabled"),
        ("app", "logging", "level"),
        ("engine", "preferred_device"),
        ("engine", "precision"),
        ("engine", "allow_tf32"),
        ("model", "transcription_model", "engine_name"),
        ("model", "translation_model", "engine_name"),
    )

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)

        self._init_state()
        self._build_ui()
        self._wire_signals()
        self._restore_initial_state()

    # ----- Initialization / build -----

    def _init_state(self) -> None:
        self._data: Dict[str, Any] = {}
        self._loaded_data: Optional[Dict[str, Any]] = None
        self._runner = TaskThreadRunner(self)

        self._dirty = False
        self._blocking_updates = False
        self._pending_restart_prompt = False
        self._restore_baseline_data: Optional[Dict[str, Any]] = None

        self._advanced_rows: List[QtWidgets.QWidget] = []
        self._label_widgets: List[QtWidgets.QLabel] = []
        self._dirty_row_specs: List[Tuple[QtWidgets.QWidget, QtWidgets.QWidget, Tuple[Tuple[str, ...], ...]]] = []
        self.btn_save: Optional[QtWidgets.QPushButton] = None
        self.btn_undo: Optional[QtWidgets.QPushButton] = None

    def _build_ui(self) -> None:
        cfg = self._ui
        base_h = cfg.control_min_h

        root = QtWidgets.QVBoxLayout(self)
        setup_layout(root, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        scroll = self._build_content_area(base_h)
        root.addWidget(scroll, 1)
        bottom = self._build_bottom_bar(base_h)
        root.addLayout(bottom)

    def _build_content_area(self, base_h: int) -> QtWidgets.QScrollArea:
        cfg = self._ui
        content = QtWidgets.QWidget()
        content.setProperty("uiRole", "page")
        enable_styled_background(content)
        content_lay = QtWidgets.QVBoxLayout(content)
        setup_layout(content_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        top_host, top = build_layout_host(parent=content, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        self.grp_app = SectionGroup(self, object_name="SettingsAppGroup")
        self.grp_engine = SectionGroup(self, object_name="SettingsEngineGroup")
        top.addWidget(self.grp_app, 1)
        top.addWidget(self.grp_engine, 1)
        content_lay.addWidget(top_host)

        mid_host, mid = build_layout_host(parent=content, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        self.grp_transcription = SectionGroup(self, object_name="SettingsTranscriptionGroup")
        self.grp_translation = SectionGroup(self, object_name="SettingsTranslationGroup")
        mid.addWidget(self.grp_transcription, 1)
        mid.addWidget(self.grp_translation, 1)
        content_lay.addWidget(mid_host)

        self.grp_download = SectionGroup(self, object_name="SettingsDownloadGroup")
        content_lay.addWidget(self.grp_download)

        self._build_app_section(base_h)
        self._build_engine_section(base_h)
        self._build_transcription_section(base_h)
        self._build_translation_section(base_h)
        self._build_download_section(base_h)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_bottom_bar(self, base_h: int) -> QtWidgets.QHBoxLayout:
        cfg = self._ui
        bottom = QtWidgets.QHBoxLayout()
        setup_layout(bottom, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        self.chk_show_advanced = QtWidgets.QCheckBox(tr("settings.advanced.toggle"))
        self.chk_show_advanced.setChecked(False)

        self._adv_autosave = OptionsAutosaveController(
            self,
            build_payload=self._build_advanced_payload,
            apply_snapshot=self._on_advanced_saved_snapshot,
            on_error=self._on_error,
            is_busy=lambda: self._runner.is_running(),
            interval_ms=600,
            pending_delay_ms=250,
            retry_delay_ms=600,
        )

        self.btn_restore = QtWidgets.QPushButton(tr("settings.buttons.restore_defaults"))
        self.btn_undo = QtWidgets.QPushButton(tr("settings.buttons.undo"))
        self.btn_save = QtWidgets.QPushButton(tr("settings.buttons.save"))
        setup_button(self.btn_restore, min_h=base_h)
        setup_button(self.btn_undo, min_h=base_h)
        setup_button(self.btn_save, min_h=base_h)
        self.btn_undo.setEnabled(bool(self._dirty))
        self.btn_save.setEnabled(bool(self._dirty))

        bottom.addWidget(self.chk_show_advanced, 0, QtCore.Qt.AlignLeft)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_restore)
        bottom.addWidget(self.btn_undo)
        bottom.addWidget(self.btn_save)
        return bottom

    # ----- Signal wiring -----

    def _wire_signals(self) -> None:
        self.chk_show_advanced.stateChanged.connect(self._on_toggle_advanced)
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        self.btn_undo.clicked.connect(self._on_undo_clicked)
        self.btn_save.clicked.connect(self._on_save_clicked)

    # ----- Restore / bootstrap -----

    def _restore_initial_state(self) -> None:
        self._populate_model_engines()
        self._refresh_runtime_capabilities()
        self._apply_advanced_visibility(False)
        QtCore.QTimer.singleShot(0, self._sync_column_widths)
        self._start_worker(action="load")

    # ----- Lifecycle -----

    def showEvent(self, e: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(e)
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    def _equalize_section_widths(self) -> None:
        try:
            pairs = (
                (self.grp_app, self.grp_engine),
                (self.grp_transcription, self.grp_translation),
            )
            for a, b in pairs:
                w = max(int(a.minimumSizeHint().width()), int(b.minimumSizeHint().width()), 0)
                a.setMinimumWidth(w)
                b.setMinimumWidth(w)

            if hasattr(self, "_left_col_host") and hasattr(self, "_right_col_host"):
                left_w = int(self._left_col_host.minimumSizeHint().width())
                right_w = int(self._right_col_host.minimumSizeHint().width())
                w = max(left_w, right_w, 0)
                self._left_col_host.setMinimumWidth(w)
                self._right_col_host.setMinimumWidth(w)
        except Exception:
            pass

    def _sync_column_widths(self) -> None:
        """Keep paired Settings sections visually 50/50 by equalizing minimum widths."""
        self._equalize_section_widths()

    def _section_header(self, text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setProperty("role", "sectionTitle")
        lbl.setWordWrap(True)
        return lbl

    # ----- Shared row / layout builders -----

    def _build_row_shell(self, *, advanced: bool) -> tuple[QtWidgets.QWidget, QtWidgets.QGridLayout]:
        cfg = self._ui

        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(cfg.grid_hspacing)
        g.setVerticalSpacing(0)

        if advanced:
            self._advanced_rows.append(w)

        return w, g

    def _build_setting_label(self, text: str, *, track: bool = True) -> QtWidgets.QLabel:
        cfg = self._ui

        lbl = QtWidgets.QLabel(text)
        lbl.setMinimumWidth(cfg.settings_label_min_w)
        lbl.setWordWrap(True)
        lbl.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Preferred)

        if track:
            lbl.setProperty("role", "settingsRowLabel")
            lbl.setProperty("dirtySetting", False)
            self._label_widgets.append(lbl)

        return lbl

    def _build_labeled_row(
        self,
        *,
        label: str,
        control: QtWidgets.QWidget,
        tooltip: str,
        control_host: QtWidgets.QWidget | None = None,
        advanced: bool = False,
    ) -> QtWidgets.QWidget:
        cfg = self._ui

        w, g = self._build_row_shell(advanced=advanced)
        lbl = self._build_setting_label(label)
        setattr(w, "_setting_label", lbl)
        setattr(w, "_setting_control", control)

        info = InfoButton(tooltip)
        info.setFixedSize(cfg.control_min_h, cfg.control_min_h)

        g.addWidget(lbl, 0, 0)
        g.addWidget(control_host or control, 0, 1)
        g.addWidget(info, 0, 2)

        g.setColumnStretch(0, 0)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 0)

        return w

    def _row(self, label: str, control: QtWidgets.QWidget, tooltip: str, *,
             advanced: bool = False) -> QtWidgets.QWidget:
        control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return self._build_labeled_row(label=label, control=control, tooltip=tooltip, advanced=advanced)

    def _row_checkbox(self, label: str, checkbox: QtWidgets.QCheckBox, tooltip: str, *,
                      advanced: bool = False) -> QtWidgets.QWidget:
        checkbox.setText("")
        checkbox.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        box = QtWidgets.QWidget()
        box_lay = QtWidgets.QHBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.addStretch(1)
        box_lay.addWidget(checkbox, 0)

        return self._build_labeled_row(
            label=label,
            control=checkbox,
            tooltip=tooltip,
            control_host=box,
            advanced=advanced,
        )

    def _row_toggle(self, label: str, toggle: _YesNoToggle, tooltip: str, *,
                    advanced: bool = False) -> QtWidgets.QWidget:
        toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return self._build_labeled_row(label=label, control=toggle, tooltip=tooltip, advanced=advanced)

    def _row_button(self, label: str, button: QtWidgets.QPushButton, tooltip: str, *,
                    advanced: bool = False) -> QtWidgets.QWidget:
        button.setMinimumHeight(self._ui.control_min_h)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return self._build_labeled_row(label=label, control=button, tooltip=tooltip, advanced=advanced)

    def _row_button_under_control(self, button: QtWidgets.QPushButton, *, advanced: bool = False) -> QtWidgets.QWidget:
        button.setMinimumHeight(self._ui.control_min_h)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        w, g = self._build_row_shell(advanced=advanced)
        lbl = self._build_setting_label("", track=False)

        g.addWidget(lbl, 0, 0)
        g.addWidget(button, 0, 1)

        g.setColumnStretch(0, 0)
        g.setColumnStretch(1, 1)

        return w

    def _normalize_all_labels(self) -> None:
        labels = [lbl for lbl in self._label_widgets if lbl.text().strip()]
        if not labels:
            return
        max_w = 0
        for lbl in labels:
            max_w = max(max_w, lbl.sizeHint().width())
        for lbl in labels:
            lbl.setFixedWidth(max_w)

    def _repolish(self, widget: Optional[QtWidgets.QWidget]) -> None:
        if widget is None:
            return
        try:
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
        except Exception:
            pass
        widget.update()

    def _track_dirty_row(
        self,
        row: QtWidgets.QWidget,
        *paths: Tuple[str, ...],
        value_widget: Optional[QtWidgets.QWidget] = None,
    ) -> QtWidgets.QWidget:
        control = value_widget if isinstance(value_widget, QtWidgets.QWidget) else getattr(row, "_setting_control", None)
        if isinstance(control, QtWidgets.QWidget) and paths:
            spec = tuple(tuple(path) for path in paths if path)
            if spec:
                self._dirty_row_specs.append((row, control, spec))
                self._set_dirty_marker(control, False)
        return row

    def _set_dirty_marker(self, control: QtWidgets.QWidget, dirty: bool) -> None:
        dirty = bool(dirty)
        if isinstance(control, ChoiceToggle):
            control.set_dirty_value(dirty)
            return

        widgets: List[QtWidgets.QWidget] = [control]
        if isinstance(control, QtWidgets.QAbstractSpinBox):
            line_edit = control.lineEdit()
            if line_edit is not None:
                widgets.append(line_edit)
        elif isinstance(control, QtWidgets.QComboBox):
            line_edit = control.lineEdit()
            if line_edit is not None:
                widgets.append(line_edit)

        for widget in widgets:
            if widget.property("dirtyValue") != dirty:
                widget.setProperty("dirtyValue", dirty)
                self._repolish(widget)

    def _refresh_dirty_markers(self) -> None:
        baseline = self._loaded_data if isinstance(self._loaded_data, dict) else None
        if baseline is None:
            for _row, control, _paths in self._dirty_row_specs:
                self._set_dirty_marker(control, False)
            self._set_dirty(False)
            return

        current = self._collect_payload()
        any_dirty = False
        for _row, control, paths in self._dirty_row_specs:
            row_dirty = any(self._get_nested(current, path) != self._get_nested(baseline, path) for path in paths)
            self._set_dirty_marker(control, row_dirty)
            any_dirty = any_dirty or row_dirty

        self._set_dirty(any_dirty)

    def _prepare_section_layout(self, group: SectionGroup, *, title_key: str) -> QtWidgets.QVBoxLayout:
        cfg = self._ui
        lay = group.root
        setup_layout(lay, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.grid_vspacing)
        lay.addWidget(self._section_header(tr(title_key)))
        return lay

    def _add_tracked_row(
        self,
        layout: QtWidgets.QBoxLayout,
        row: QtWidgets.QWidget,
        *paths: Tuple[str, ...],
        value_widget: Optional[QtWidgets.QWidget] = None,
    ) -> QtWidgets.QWidget:
        tracked = self._track_dirty_row(row, *paths, value_widget=value_widget)
        layout.addWidget(tracked)
        return tracked

    def _new_combo(self, base_h: int) -> PopupComboBox:
        combo = PopupComboBox()
        setup_combo(combo, min_h=base_h)
        return combo

    def _new_toggle(self) -> _YesNoToggle:
        return _YesNoToggle(
            yes_text=tr("common.yes"),
            no_text=tr("common.no"),
            height=self._ui.control_min_h,
        )

    def _new_spinbox(
        self,
        base_h: int,
        minimum: int,
        maximum: int,
        *,
        step: Optional[int] = None,
    ) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox()
        spin.setRange(minimum, maximum)
        if isinstance(step, int) and step > 0:
            spin.setSingleStep(step)
        setup_spinbox(spin, min_h=base_h)
        return spin

    def _add_combo_option(
        self,
        combo: QtWidgets.QComboBox,
        label_key: str,
        data: Any,
        *,
        tooltip_key: Optional[str] = None,
    ) -> None:
        combo.addItem(tr(label_key), data)
        if tooltip_key:
            idx = combo.count() - 1
            combo.setItemData(idx, tr(tooltip_key), QtCore.Qt.ToolTipRole)

    def _connect_mark_dirty(self, *signals: Any) -> None:
        for signal in signals:
            signal.connect(self._mark_dirty)

    @staticmethod
    def _section_dict(data: Dict[str, Any], key: str) -> Dict[str, Any]:
        value = data.get(key)
        return value if isinstance(value, dict) else {}

    # ----- Section builders -----

    def _build_logging_level_row(self, cfg: Any) -> QtWidgets.QWidget:
        log_level_row = QtWidgets.QWidget()
        log_level_lay = QtWidgets.QHBoxLayout(log_level_row)
        setup_layout(log_level_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.inline_spacing)
        log_level_lay.addWidget(self.cb_log_level, 1)
        log_level_lay.addWidget(self.btn_open_logs, 1)
        return log_level_row

    def _build_app_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_app, title_key="settings.section.app")

        self.cb_app_language = self._new_combo(base_h)
        self.cb_app_language.addItem(tr("common.auto"), Config.LANGUAGE_AUTO_VALUE)
        for code, name in list_locales(Config.LOCALES_DIR):
            self.cb_app_language.addItem(name, code)

        self.cb_app_theme = self._new_combo(base_h)
        self.cb_app_theme.addItem(tr("common.auto"), Config.LANGUAGE_AUTO_VALUE)
        self._add_combo_option(self.cb_app_theme, "settings.app.theme.light", "light")
        self._add_combo_option(self.cb_app_theme, "settings.app.theme.dark", "dark")

        self.tg_log_enabled = self._new_toggle()

        self.cb_log_level = self._new_combo(base_h)
        self._add_combo_option(self.cb_log_level, "settings.app.logging.level.debug", "debug",
                               tooltip_key="settings.app.logging.level.debug_tip")
        self._add_combo_option(self.cb_log_level, "settings.app.logging.level.info", "info",
                               tooltip_key="settings.app.logging.level.info_tip")
        self._add_combo_option(self.cb_log_level, "settings.app.logging.level.warning", "warning",
                               tooltip_key="settings.app.logging.level.warning_tip")
        self._add_combo_option(self.cb_log_level, "settings.app.logging.level.error", "error",
                               tooltip_key="settings.app.logging.level.error_tip")

        self.btn_open_logs = QtWidgets.QPushButton(tr("settings.app.logging.open_folder"))
        setup_button(self.btn_open_logs, min_h=base_h)
        self.btn_open_logs.clicked.connect(self._open_logs_folder)

        self._add_tracked_row(
            lay,
            self._row(tr("settings.app.language.label"), self.cb_app_language, tr("settings.help.ui_language")),
            ("app", "language"),
        )
        self._add_tracked_row(
            lay,
            self._row(tr("settings.app.theme.label"), self.cb_app_theme, tr("settings.help.theme")),
            ("app", "theme"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.app.logging.enabled"),
                self.tg_log_enabled,
                tr("settings.help.logging_enabled"),
                advanced=True,
            ),
            ("app", "logging", "enabled"),
        )

        self._log_level_row = self._add_tracked_row(
            lay,
            self._row(
                tr("settings.app.logging.level_label"),
                self._build_logging_level_row(self._ui),
                tr("settings.help.logging_level"),
            ),
            ("app", "logging", "level"),
            value_widget=self.cb_log_level,
        )

        lay.addStretch(1)

        self._connect_mark_dirty(
            self.cb_app_language.currentIndexChanged,
            self.cb_app_theme.currentIndexChanged,
            self.cb_log_level.currentIndexChanged,
        )
        self.tg_log_enabled.toggled(self._on_logging_toggle)
        self._on_logging_toggle()

    def _build_engine_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_engine, title_key="settings.section.engine")

        self.cb_engine_device = self._new_combo(base_h)
        self._add_combo_option(self.cb_engine_device, "settings.engine.device.auto", Config.LANGUAGE_AUTO_VALUE)
        self._add_combo_option(self.cb_engine_device, "settings.engine.device.cpu", "cpu")
        self._add_combo_option(self.cb_engine_device, "settings.engine.device.gpu", "cuda")

        self.cb_engine_precision = self._new_combo(base_h)
        self._add_combo_option(self.cb_engine_precision, "settings.engine.precision.auto", Config.LANGUAGE_AUTO_VALUE,
                               tooltip_key="settings.engine.precision.auto_tip")
        self._add_combo_option(self.cb_engine_precision, "settings.engine.precision.float32", "float32",
                               tooltip_key="settings.engine.precision.float32_tip")
        self._add_combo_option(self.cb_engine_precision, "settings.engine.precision.float16", "float16",
                               tooltip_key="settings.engine.precision.float16_tip")
        self._add_combo_option(self.cb_engine_precision, "settings.engine.precision.bfloat16", "bfloat16",
                               tooltip_key="settings.engine.precision.bfloat16_tip")

        self.tg_tf32 = self._new_toggle()
        self.tg_low_cpu_mem = self._new_toggle()

        self._add_tracked_row(
            lay,
            self._row(tr("settings.engine.device.label"), self.cb_engine_device, tr("settings.help.device")),
            ("engine", "preferred_device"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.engine.precision.label"),
                self.cb_engine_precision,
                tr("settings.help.precision_hint"),
            ),
            ("engine", "precision"),
        )
        self._row_tf32 = self._add_tracked_row(
            lay,
            self._row_toggle(tr("settings.engine.allow_tf32"), self.tg_tf32, tr("settings.help.tf32")),
            ("engine", "allow_tf32"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.engine.low_cpu_mem_usage"),
                self.tg_low_cpu_mem,
                tr("settings.help.low_cpu_mem_usage"),
                advanced=True,
            ),
            ("engine", "low_cpu_mem_usage"),
        )
        lay.addStretch(1)

        self.cb_engine_device.currentIndexChanged.connect(self._on_device_changed)
        self.cb_engine_precision.currentIndexChanged.connect(self._on_precision_changed)
        self._connect_mark_dirty(self.tg_tf32.changed, self.tg_low_cpu_mem.changed)

    def _build_transcription_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_transcription, title_key="settings.section.transcription")

        self.cb_trans_engine = self._new_combo(base_h)

        self.cb_quality = self._new_combo(base_h)
        self._add_combo_option(self.cb_quality, "settings.quality.fast", "fast", tooltip_key="settings.quality.fast_tip")
        self._add_combo_option(self.cb_quality, "settings.quality.balanced", "balanced",
                               tooltip_key="settings.quality.balanced_tip")
        self._add_combo_option(self.cb_quality, "settings.quality.accurate", "accurate",
                               tooltip_key="settings.quality.accurate_tip")

        self.tg_text_consistency = self._new_toggle()
        self.sp_chunk_len = self._new_spinbox(base_h, 5, 3600, step=5)
        self.sp_stride_len = self._new_spinbox(base_h, 0, 120, step=1)
        self.tg_ignore_empty = self._new_toggle()

        self._add_tracked_row(
            lay,
            self._row(tr("settings.transcription.model"), self.cb_trans_engine, tr("settings.help.transcription_engine")),
            ("model", "transcription_model", "engine_name"),
        )
        self._add_tracked_row(
            lay,
            self._row(tr("settings.transcription.quality_label"), self.cb_quality, tr("settings.help.trans_quality")),
            ("model", "transcription_model", "quality_preset"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.transcription.text_consistency"),
                self.tg_text_consistency,
                tr("settings.help.text_consistency"),
            ),
            ("model", "transcription_model", "text_consistency"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.chunk_length_s"),
                self.sp_chunk_len,
                tr("settings.help.chunk_length"),
                advanced=True,
            ),
            ("model", "transcription_model", "chunk_length_s"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.stride_length_s"),
                self.sp_stride_len,
                tr("settings.help.stride_length"),
                advanced=True,
            ),
            ("model", "transcription_model", "stride_length_s"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.transcription.ignore_warning"),
                self.tg_ignore_empty,
                tr("settings.help.ignore_warning"),
                advanced=True,
            ),
            ("model", "transcription_model", "ignore_warning"),
        )

        lay.addStretch(1)

        self._connect_mark_dirty(
            self.cb_trans_engine.currentIndexChanged,
            self.cb_quality.currentIndexChanged,
            self.tg_text_consistency.changed,
            self.sp_chunk_len.valueChanged,
            self.sp_stride_len.valueChanged,
            self.tg_ignore_empty.changed,
        )

    def _build_translation_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_translation, title_key="settings.section.translation")

        self.cb_tr_engine = self._new_combo(base_h)
        self.cb_tr_quality = self._new_combo(base_h)
        self._add_combo_option(self.cb_tr_quality, "settings.quality.fast", "fast", tooltip_key="settings.quality.fast_tip")
        self._add_combo_option(self.cb_tr_quality, "settings.quality.balanced", "balanced",
                               tooltip_key="settings.quality.balanced_tip")
        self._add_combo_option(self.cb_tr_quality, "settings.quality.accurate", "accurate",
                               tooltip_key="settings.quality.accurate_tip")
        self.cb_tr_engine.addItem(tr("settings.translation.engine.disabled"), "none")

        self.sp_tr_max_tokens = self._new_spinbox(base_h, 16, 8192, step=16)
        self.sp_tr_chunk_chars = self._new_spinbox(base_h, 200, 20000, step=100)

        self._add_tracked_row(
            lay,
            self._row(tr("settings.translation.engine.label"), self.cb_tr_engine, tr("settings.help.translation_engine")),
            ("model", "translation_model", "engine_name"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.quality.label"),
                self.cb_tr_quality,
                tr("settings.help.translation_quality"),
            ),
            ("model", "translation_model", "quality_preset"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.max_new_tokens"),
                self.sp_tr_max_tokens,
                tr("settings.help.translation_max_new_tokens"),
                advanced=True,
            ),
            ("model", "translation_model", "max_new_tokens"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.chunk_max_chars"),
                self.sp_tr_chunk_chars,
                tr("settings.help.translation_chunk_max_chars"),
                advanced=True,
            ),
            ("model", "translation_model", "chunk_max_chars"),
        )

        lay.addStretch(1)

        self._connect_mark_dirty(
            self.cb_tr_engine.currentIndexChanged,
            self.cb_tr_quality.currentIndexChanged,
            self.sp_tr_max_tokens.valueChanged,
            self.sp_tr_chunk_chars.valueChanged,
        )

    def _build_download_section(self, base_h: int) -> None:
        cfg = self._ui
        lay = self._prepare_section_layout(self.grp_download, title_key="settings.section.download")

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(cfg.grid_hspacing)
        lay.addLayout(cols)

        self._left_col_host = QtWidgets.QWidget()
        self._right_col_host = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(self._left_col_host)
        right = QtWidgets.QVBoxLayout(self._right_col_host)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(cfg.spacing)
        right.setSpacing(cfg.spacing)
        cols.addWidget(self._left_col_host, 1)
        cols.addWidget(self._right_col_host, 1)

        self.sp_min_height = self._new_spinbox(base_h, 0, 10000)
        self.sp_max_height = self._new_spinbox(base_h, 0, 10000)
        self.sp_retries = self._new_spinbox(base_h, 0, 50)
        self.sp_bandwidth = self._new_spinbox(base_h, 0, 10_000_000, step=100)
        self.sp_fragments = self._new_spinbox(base_h, 1, 64)
        self.sp_timeout = self._new_spinbox(base_h, 1, 600)

        self._add_tracked_row(
            left,
            self._row(tr("settings.downloader.min_video_height"), self.sp_min_height, tr("settings.help.min_video_height")),
            ("downloader", "min_video_height"),
        )
        self._add_tracked_row(
            left,
            self._row(tr("settings.downloader.max_video_height"), self.sp_max_height, tr("settings.help.max_video_height")),
            ("downloader", "max_video_height"),
        )
        self._add_tracked_row(
            left,
            self._row(tr("settings.network.retries"), self.sp_retries, tr("settings.help.retries")),
            ("network", "retries"),
        )

        self._add_tracked_row(
            right,
            self._row(
                tr("settings.network.max_bandwidth_kbps"),
                self.sp_bandwidth,
                tr("settings.help.max_bandwidth_kbps"),
                advanced=True,
            ),
            ("network", "max_bandwidth_kbps"),
        )
        self._add_tracked_row(
            right,
            self._row(
                tr("settings.network.concurrent_fragments"),
                self.sp_fragments,
                tr("settings.help.concurrent_fragments"),
                advanced=True,
            ),
            ("network", "concurrent_fragments"),
        )
        self._add_tracked_row(
            right,
            self._row(
                tr("settings.network.http_timeout_s"),
                self.sp_timeout,
                tr("settings.help.http_timeout_s"),
                advanced=True,
            ),
            ("network", "http_timeout_s"),
        )

        lay.addStretch(1)

        self._connect_mark_dirty(
            self.sp_min_height.valueChanged,
            self.sp_max_height.valueChanged,
            self.sp_retries.valueChanged,
            self.sp_bandwidth.valueChanged,
            self.sp_fragments.valueChanged,
            self.sp_timeout.valueChanged,
        )

    # ----- Worker flow -----


    def _start_worker(self, *, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self._runner.is_running():
            return

        wk = SettingsWorker(action=action, payload=payload)

        def _connect(worker: SettingsWorker) -> None:
            worker.settings_loaded.connect(self._on_settings_loaded)
            worker.settings_loaded_snapshot.connect(self._on_settings_loaded_snapshot)
            worker.saved.connect(self._on_saved)
            worker.saved_snapshot.connect(self._on_saved_snapshot)
            worker.error.connect(self._on_error)

        self._runner.start(wk, connect=_connect)

    def _on_settings_loaded(self, data: object) -> None:
        if isinstance(data, dict):
            self._data = data
            self._loaded_data = copy.deepcopy(data)

        self._blocking_updates = True
        try:
            self._populate_from_data()
            self._refresh_runtime_capabilities()
        finally:
            self._blocking_updates = False
        self._refresh_dirty_markers()
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    def _on_settings_loaded_snapshot(self, snap: object) -> None:
        try:
            Config.initialize_from_snapshot(snap)
        except Exception:
            pass

    def _on_saved_snapshot(self, snap: object) -> None:
        try:
            Config.initialize_from_snapshot(snap)
        except Exception:
            pass

    def _on_saved(self, data: object) -> None:
        need_restart = self._pending_restart_prompt
        if self._restore_baseline_data is not None and isinstance(data, dict):
            need_restart = self._needs_restart_between(self._restore_baseline_data, data)

        if isinstance(data, dict):
            self._data = data
            self._loaded_data = copy.deepcopy(data)
            self._blocking_updates = True
            try:
                self._populate_from_data()
                self._refresh_runtime_capabilities()
            finally:
                self._blocking_updates = False

        self._refresh_dirty_markers()
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

        self._pending_restart_prompt = False
        self._restore_baseline_data = None

        if need_restart:
            restart_now = dialogs.ask_restart_required(self)
            if restart_now:
                self._restart_application()
        else:
            dialogs.show_info(self, title=tr("dialog.info.title"), message=tr("settings.msg.saved"), header=tr("dialog.info.header"))

    def _on_error(self, key: str, params: dict) -> None:
        dialogs.show_error(self, key, params or {})

    # ----- Dirty state / advanced UI -----

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        if isinstance(self.btn_save, QtWidgets.QAbstractButton):
            self.btn_save.setEnabled(self._dirty)
        if isinstance(self.btn_undo, QtWidgets.QAbstractButton):
            self.btn_undo.setEnabled(self._dirty)

    def _mark_dirty(self) -> None:
        if self._blocking_updates:
            return
        self._refresh_dirty_markers()

    def _on_toggle_advanced(self) -> None:
        show = bool(self.chk_show_advanced.isChecked())
        self._apply_advanced_visibility(show)

        if self._blocking_updates:
            return
        self._adv_autosave.trigger()

    def _on_logging_toggle(self) -> None:
        enabled = bool(self.tg_log_enabled.is_checked())
        self._log_level_row.setVisible(enabled)
        self._mark_dirty()

    def _apply_advanced_visibility(self, show: bool) -> None:
        for w in self._advanced_rows:
            w.setVisible(bool(show))

    def _build_advanced_payload(self) -> Dict[str, Any]:
        return {
            "app": {
                "ui": {
                    "show_advanced_settings": bool(self.chk_show_advanced.isChecked()),
                }
            }
        }

    def _on_advanced_saved_snapshot(self, snap: object) -> None:
        try:
            Config.update_from_snapshot(snap, sections=("app",))
        except Exception:
            pass

    # ----- User actions -----

    def _on_undo_clicked(self) -> None:
        if not self._loaded_data:
            return
        self._blocking_updates = True
        try:
            self._data = copy.deepcopy(self._loaded_data)
            self._populate_from_data()
        finally:
            self._blocking_updates = False
        self._refresh_dirty_markers()
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    def _on_restore_clicked(self) -> None:
        if not dialogs.ask_restore_defaults(self):
            return
        self._restore_baseline_data = copy.deepcopy(self._data or {})
        self._start_worker(action="restore_defaults")

    def _on_save_clicked(self) -> None:
        if not dialogs.ask_save_settings(self):
            return
        self._restore_baseline_data = None
        payload = self._collect_payload()
        self._pending_restart_prompt = self._needs_restart(payload)
        self._start_worker(action="save", payload=payload)

    def _restart_application(self) -> None:
        os.execl(sys.executable, sys.executable, *sys.argv)

    def _open_logs_folder(self) -> None:
        path = Config.LOGS_DIR
        if isinstance(path, Path):
            open_local_path(path)

    def _on_device_changed(self) -> None:
        self._mark_dirty()
        self._refresh_runtime_capabilities()

    def _on_precision_changed(self) -> None:
        self._mark_dirty()
        self._refresh_runtime_capabilities()

    # ----- Populate / collect payload helpers -----

    def _populate_from_data(self) -> None:
        d = self._data or {}

        app = self._section_dict(d, "app")
        eng = self._section_dict(d, "engine")
        model = self._section_dict(d, "model")
        downloader = self._section_dict(d, "downloader")
        network = self._section_dict(d, "network")

        self._populate_app_settings(app)
        self._populate_engine_settings(eng)
        self._populate_model_settings(model)
        self._populate_download_settings(downloader, network)

        self._refresh_auto_option_labels()
        self._refresh_dirty_markers()

    def _populate_app_settings(self, app: Dict[str, Any]) -> None:
        set_combo_data(self.cb_app_language, str(app.get("language", Config.LANGUAGE_AUTO_VALUE)), fallback_data=Config.LANGUAGE_AUTO_VALUE)
        set_combo_data(self.cb_app_theme, str(app.get("theme", Config.LANGUAGE_AUTO_VALUE)), fallback_data=Config.LANGUAGE_AUTO_VALUE)

        ui_cfg = self._section_dict(app, "ui")
        show_adv = bool(ui_cfg.get("show_advanced_settings", False))
        self.chk_show_advanced.blockSignals(True)
        self.chk_show_advanced.setChecked(show_adv)
        self.chk_show_advanced.blockSignals(False)
        self._apply_advanced_visibility(show_adv)

        log_cfg = self._section_dict(app, "logging")
        self.tg_log_enabled.set_checked(bool(log_cfg.get("enabled", True)))
        set_combo_data(self.cb_log_level, str(log_cfg.get("level", "warning")), fallback_data="warning")
        self._on_logging_toggle()

    def _populate_engine_settings(self, eng: Dict[str, Any]) -> None:
        set_combo_data(self.cb_engine_device, str(eng.get("preferred_device", Config.LANGUAGE_AUTO_VALUE)), fallback_data=Config.LANGUAGE_AUTO_VALUE)
        set_combo_data(self.cb_engine_precision, str(eng.get("precision", Config.LANGUAGE_AUTO_VALUE)), fallback_data=Config.LANGUAGE_AUTO_VALUE)
        self.tg_tf32.set_checked(bool(eng.get("allow_tf32", True)))
        self.tg_low_cpu_mem.set_checked(bool(eng.get("low_cpu_mem_usage", True)))

    def _populate_model_settings(self, model: Dict[str, Any]) -> None:
        t_model = self._section_dict(model, "transcription_model")
        x_model = self._section_dict(model, "translation_model")

        self._populate_model_engines()

        trans_engine_name = Config.resolve_transcription_engine_name(model)
        if trans_engine_name == Config.MISSING_VALUE:
            trans_engine_name = str(t_model.get("engine_name", "none"))
        set_combo_data(self.cb_trans_engine, trans_engine_name, fallback_data="none")
        set_combo_data(self.cb_quality, str(t_model.get("quality_preset", "balanced")), fallback_data="balanced")
        self.tg_text_consistency.set_checked(bool(t_model.get("text_consistency", True)))
        self.sp_chunk_len.setValue(int(t_model.get("chunk_length_s", 60)))
        self.sp_stride_len.setValue(int(t_model.get("stride_length_s", 5)))
        self.tg_ignore_empty.set_checked(bool(t_model.get("ignore_warning", False)))

        tr_engine_name = Config.resolve_translation_engine_name(model)
        if tr_engine_name == Config.MISSING_VALUE:
            tr_engine_name = str(x_model.get("engine_name", "none"))
        set_combo_data(self.cb_tr_engine, tr_engine_name, fallback_data="none")
        set_combo_data(self.cb_tr_quality, str(x_model.get("quality_preset", "balanced")), fallback_data="balanced")
        self.sp_tr_max_tokens.setValue(int(x_model.get("max_new_tokens", 256)))
        self.sp_tr_chunk_chars.setValue(int(x_model.get("chunk_max_chars", 1200)))

    def _populate_download_settings(self, downloader: Dict[str, Any], network: Dict[str, Any]) -> None:
        self.sp_min_height.setValue(int(downloader.get("min_video_height", Config.VIDEO_MIN_HEIGHT)))
        self.sp_max_height.setValue(int(downloader.get("max_video_height", Config.VIDEO_MAX_HEIGHT)))

        self.sp_retries.setValue(int(network.get("retries", Config.NET_RETRIES)))
        bw = network.get("max_bandwidth_kbps", Config.NET_MAX_KBPS)
        self.sp_bandwidth.setValue(int(bw or 0))
        self.sp_fragments.setValue(int(network.get("concurrent_fragments", Config.NET_CONC_FRAG)))
        self.sp_timeout.setValue(int(network.get("http_timeout_s", Config.NET_TIMEOUT_S)))
    def _collect_payload(self) -> Dict[str, Any]:
        return {
            "app": self._collect_app_payload(),
            "engine": self._collect_engine_payload(),
            "model": self._collect_model_payload(),
            "downloader": self._collect_downloader_payload(),
            "network": self._collect_network_payload(),
        }

    def _collect_app_payload(self) -> Dict[str, Any]:
        return {
            "language": str(self.cb_app_language.currentData() or Config.LANGUAGE_AUTO_VALUE),
            "theme": str(self.cb_app_theme.currentData() or Config.LANGUAGE_AUTO_VALUE),
            "ui": {
                "show_advanced_settings": bool(self.chk_show_advanced.isChecked()),
            },
            "logging": {
                "enabled": bool(self.tg_log_enabled.is_checked()),
                "level": str(self.cb_log_level.currentData() or "warning"),
            },
        }

    def _collect_engine_payload(self) -> Dict[str, Any]:
        return {
            "preferred_device": str(self.cb_engine_device.currentData() or Config.LANGUAGE_AUTO_VALUE),
            "precision": str(self.cb_engine_precision.currentData() or Config.LANGUAGE_AUTO_VALUE),
            "allow_tf32": bool(self.tg_tf32.is_checked()),
            "low_cpu_mem_usage": bool(self.tg_low_cpu_mem.is_checked()),
        }

    def _collect_model_payload(self) -> Dict[str, Any]:
        return {
            "transcription_model": {
                "engine_name": str(self.cb_trans_engine.currentData() or "none"),
                "quality_preset": str(self.cb_quality.currentData() or "balanced"),
                "text_consistency": bool(self.tg_text_consistency.is_checked()),
                "chunk_length_s": int(self.sp_chunk_len.value()),
                "stride_length_s": int(self.sp_stride_len.value()),
                "ignore_warning": bool(self.tg_ignore_empty.is_checked()),
            },
            "translation_model": {
                "engine_name": str(self.cb_tr_engine.currentData() or "none"),
                "max_new_tokens": int(self.sp_tr_max_tokens.value()),
                "chunk_max_chars": int(self.sp_tr_chunk_chars.value()),
                "quality_preset": str(self.cb_tr_quality.currentData() or "balanced"),
            },
        }

    def _collect_downloader_payload(self) -> Dict[str, Any]:
        return {
            "min_video_height": int(self.sp_min_height.value()),
            "max_video_height": int(self.sp_max_height.value()),
        }

    def _collect_network_payload(self) -> Dict[str, Any]:
        bandwidth = int(self.sp_bandwidth.value())
        return {
            "retries": int(self.sp_retries.value()),
            "max_bandwidth_kbps": bandwidth if bandwidth > 0 else None,
            "concurrent_fragments": int(self.sp_fragments.value()),
            "http_timeout_s": int(self.sp_timeout.value()),
        }
    def _needs_restart(self, payload: Dict[str, Any]) -> bool:
        return self._needs_restart_between(self._data or {}, payload)

    def _needs_restart_between(self, current: Dict[str, Any], updated: Dict[str, Any]) -> bool:
        for path in self._RESTART_SENSITIVE_KEYS:
            if self._get_nested(current, path) != self._get_nested(updated, path):
                return True
        return False

    @staticmethod
    def _get_nested(d: Dict[str, Any], path: Tuple[str, ...]) -> Any:
        cur: Any = d
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    # ----- Runtime capability helpers -----

    @staticmethod
    def _short_label(text: str) -> str:
        s = str(text or "").strip()
        if "(" in s:
            s = s.split("(", 1)[0].strip()
        return s or str(text or "").strip()

    def _refresh_auto_option_labels(self) -> None:
        try:
            sys_hint = QtCore.QLocale.system().name().split("_", 1)[0].lower()
            available: Dict[str, str] = {}
            for i in range(self.cb_app_language.count()):
                code = str(self.cb_app_language.itemData(i) or "").strip().lower()
                if code and code != Config.LANGUAGE_AUTO_VALUE:
                    available[code] = self.cb_app_language.itemText(i)
            resolved_lang = available.get(sys_hint) or available.get("en") or next(iter(available.values()), sys_hint or "")
            idx_auto = self.cb_app_language.findData(Config.LANGUAGE_AUTO_VALUE)
            if idx_auto >= 0:
                self.cb_app_language.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_lang})')
        except Exception:
            pass

        try:
            theme = system_theme_key(QtWidgets.QApplication.instance())
            resolved_theme = tr("settings.app.theme.dark") if theme == "dark" else tr("settings.app.theme.light")
            idx_auto = self.cb_app_theme.findData(Config.LANGUAGE_AUTO_VALUE)
            if idx_auto >= 0:
                self.cb_app_theme.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_theme})')
        except Exception:
            pass

        try:
            auto_dev = Config.auto_device_key()
            resolved_dev = tr("settings.engine.device.gpu") if auto_dev == "cuda" else tr("settings.engine.device.cpu")
            idx_auto = self.cb_engine_device.findData(Config.LANGUAGE_AUTO_VALUE)
            if idx_auto >= 0:
                self.cb_engine_device.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_dev})')
        except Exception:
            pass

        try:
            auto_prec = Config.auto_precision_key()
            resolved_prec = self._short_label(
                tr("settings.engine.precision.float16") if auto_prec == "float16" else tr(
                    "settings.engine.precision.float32")
            )
            idx_auto = self.cb_engine_precision.findData(Config.LANGUAGE_AUTO_VALUE)
            if idx_auto >= 0:
                self.cb_engine_precision.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_prec})')
        except Exception:
            pass

    def _populate_model_engines(self) -> None:
        trans_names = local_model_names_for_task("transcription")
        tr_names = local_model_names_for_task("translation")

        self.cb_trans_engine.blockSignals(True)
        try:
            current = str(self.cb_trans_engine.currentData() or "none")
            self.cb_trans_engine.clear()
            self.cb_trans_engine.addItem(tr("settings.translation.engine.disabled"), "none")

            for name in trans_names:
                self.cb_trans_engine.addItem(name, name)

            set_combo_data(self.cb_trans_engine, current, fallback_data="none")
        finally:
            self.cb_trans_engine.blockSignals(False)

        self.cb_tr_engine.blockSignals(True)
        try:
            current_tr = str(self.cb_tr_engine.currentData() or "none")
            self.cb_tr_engine.clear()
            self.cb_tr_engine.addItem(tr("settings.translation.engine.disabled"), "none")

            for name in tr_names:
                self.cb_tr_engine.addItem(name, name)

            set_combo_data(self.cb_tr_engine, current_tr, fallback_data="none")
        finally:
            self.cb_tr_engine.blockSignals(False)

    def _refresh_runtime_capabilities(self) -> None:
        caps = Config.runtime_capabilities()
        has_cuda = bool(caps.get("has_cuda", False))
        bf16_supported = bool(caps.get("bf16_supported", False))
        tf32_supported = bool(caps.get("tf32_supported", False))

        idx_cuda = self.cb_engine_device.findData("cuda")
        if idx_cuda >= 0:
            model = self.cb_engine_device.model()
            if model is not None:
                item = model.item(idx_cuda)
                if item is not None:
                    item.setEnabled(has_cuda)

        if not has_cuda and str(self.cb_engine_device.currentData() or Config.LANGUAGE_AUTO_VALUE) == "cuda":
            set_combo_data(self.cb_engine_device, Config.LANGUAGE_AUTO_VALUE, fallback_data=Config.LANGUAGE_AUTO_VALUE)

        prec_model = self.cb_engine_precision.model()
        if prec_model is not None:
            idx_f16 = self.cb_engine_precision.findData("float16")
            if idx_f16 >= 0:
                item = prec_model.item(idx_f16)
                if item is not None:
                    item.setEnabled(has_cuda)

            idx_bf16 = self.cb_engine_precision.findData("bfloat16")
            if idx_bf16 >= 0:
                item = prec_model.item(idx_bf16)
                if item is not None:
                    item.setEnabled(has_cuda and bf16_supported)

        cur_prec = str(self.cb_engine_precision.currentData() or Config.LANGUAGE_AUTO_VALUE)
        if cur_prec == "float16" and not has_cuda:
            set_combo_data(self.cb_engine_precision, Config.LANGUAGE_AUTO_VALUE, fallback_data=Config.LANGUAGE_AUTO_VALUE)
        if cur_prec == "bfloat16" and not (has_cuda and bf16_supported):
            set_combo_data(self.cb_engine_precision, Config.LANGUAGE_AUTO_VALUE, fallback_data=Config.LANGUAGE_AUTO_VALUE)

        cur_dev = str(self.cb_engine_device.currentData() or Config.LANGUAGE_AUTO_VALUE)
        tf32_enable_allowed = bool(
            has_cuda and tf32_supported and cur_dev in (Config.LANGUAGE_AUTO_VALUE, "cuda") and cur_prec in (Config.LANGUAGE_AUTO_VALUE, "float32"))
        self.tg_tf32.setEnabled(tf32_enable_allowed)

        tf32_row_label = None
        try:
            tf32_row = getattr(self, "_row_tf32", None)
            tf32_row_label = getattr(tf32_row, "_setting_label", None) if tf32_row is not None else None
        except Exception:
            tf32_row_label = None

        if tf32_row_label is not None:
            tf32_row_label.setEnabled(tf32_enable_allowed)

        if not tf32_enable_allowed:
            self.tg_tf32.clear_selection()

        self._refresh_auto_option_labels()
