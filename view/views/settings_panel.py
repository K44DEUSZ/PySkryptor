# view/views/settings_panel.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch
from PyQt5 import QtCore, QtGui, QtWidgets

from controller.tasks.settings_task import SettingsWorker
from model.config.app_config import AppConfig as Config
from model.services.settings_service import SettingsCatalog
from view.utils.translating import tr
from view.views import dialogs
from view.widgets.language_combo import LanguageCombo


class _InfoButton(QtWidgets.QToolButton):
    def __init__(self, tooltip: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setText(tr("icon.info"))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAutoRaise(True)
        self.setFixedSize(18, 18)


class _YesNoToggle(QtWidgets.QWidget):
    def __init__(
        self,
        *,
        yes_text: str,
        no_text: str,
        height: int,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._btn_yes = QtWidgets.QPushButton(yes_text)
        self._btn_no = QtWidgets.QPushButton(no_text)

        for b in (self._btn_yes, self._btn_no):
            b.setCheckable(True)
            b.setMinimumHeight(height)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self._btn_yes)
        group.addButton(self._btn_no)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._btn_yes, 1)
        lay.addWidget(self._btn_no, 1)

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setObjectName("YesNoToggle")
        self.setStyleSheet(
            "QWidget#YesNoToggle QPushButton{border:1px solid rgba(255,255,255,0.20); padding:3px 10px;}"
            "QWidget#YesNoToggle QPushButton:first-child{border-top-left-radius:6px;border-bottom-left-radius:6px;}"
            "QWidget#YesNoToggle QPushButton:last-child{border-top-right-radius:6px;border-bottom-right-radius:6px;}"
            "QWidget#YesNoToggle QPushButton:checked{background:rgba(120,180,255,0.25);border-color:rgba(120,180,255,0.45);}"
        )

    def set_checked(self, value: bool) -> None:
        if value:
            self._btn_yes.setChecked(True)
        else:
            self._btn_no.setChecked(True)

    def is_checked(self) -> bool:
        return bool(self._btn_yes.isChecked())

    def toggled(self, fn) -> None:
        self._btn_yes.toggled.connect(fn)
        self._btn_no.toggled.connect(fn)


class SettingsPanel(QtWidgets.QWidget):
    CONTROL_HEIGHT = 24

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

        self._data: Dict[str, Any] = {}
        self._loaded_data: Optional[Dict[str, Any]] = None
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[SettingsWorker] = None

        self._dirty = False
        self._blocking_updates = False
        self._pending_restart_prompt = False

        self._advanced_rows: List[QtWidgets.QWidget] = []
        self._label_widgets: List[QtWidgets.QLabel] = []

        base_h = self.CONTROL_HEIGHT

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        # ----- Content -----
        content = QtWidgets.QWidget()
        content_lay = QtWidgets.QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(10)

        # App + Engine
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        self.grp_app = QtWidgets.QGroupBox(tr("settings.section.app"))
        self.grp_engine = QtWidgets.QGroupBox(tr("settings.section.engine"))
        top.addWidget(self.grp_app, 1)
        top.addWidget(self.grp_engine, 1)
        content_lay.addLayout(top)

        # Transcription + Translation
        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(10)
        self.grp_transcription = QtWidgets.QGroupBox(tr("settings.section.transcription"))
        self.grp_translation = QtWidgets.QGroupBox(tr("settings.section.translation"))
        mid.addWidget(self.grp_transcription, 1)
        mid.addWidget(self.grp_translation, 1)
        content_lay.addLayout(mid)

        # Download + Network
        self.grp_download = QtWidgets.QGroupBox(tr("settings.section.download"))
        content_lay.addWidget(self.grp_download)

        root.addWidget(content, 1)

        # ----- Bottom bar (advanced toggle + actions) -----
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(10)

        self.chk_show_advanced = QtWidgets.QCheckBox(tr("settings.advanced.toggle"))
        self.chk_show_advanced.setChecked(False)
        self.chk_show_advanced.stateChanged.connect(self._on_toggle_advanced)

        bottom.addWidget(self.chk_show_advanced, 0, QtCore.Qt.AlignLeft)
        bottom.addStretch(1)

        self.btn_restore = QtWidgets.QPushButton(tr("settings.buttons.restore_defaults"))
        self.btn_undo = QtWidgets.QPushButton(tr("settings.buttons.undo"))
        self.btn_save = QtWidgets.QPushButton(tr("settings.buttons.save"))
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        self.btn_undo.clicked.connect(self._on_undo_clicked)
        self.btn_save.clicked.connect(self._on_save_clicked)

        self.btn_undo.setEnabled(False)

        bottom.addWidget(self.btn_restore)
        bottom.addWidget(self.btn_undo)
        bottom.addWidget(self.btn_save)

        root.addLayout(bottom)

        # ----- Build UI sections -----
        self._build_app_section(base_h)
        self._build_engine_section(base_h)
        self._build_transcription_section(base_h)
        self._build_translation_section(base_h)
        self._build_download_section(base_h)

        self._populate_model_engines()
        self._refresh_runtime_capabilities()

        self._apply_advanced_visibility(False)
        self._start_worker(action="load")

    # ----- Row helpers -----

    def _row(self, label: str, control: QtWidgets.QWidget, tooltip: str, *, advanced: bool = False) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QtWidgets.QLabel(label)
        lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._label_widgets.append(lbl)

        ctrl_wrap = QtWidgets.QWidget()
        ctrl_lay = QtWidgets.QHBoxLayout(ctrl_wrap)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.setSpacing(6)
        control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        ctrl_lay.addWidget(control, 1)

        lay.addWidget(lbl, 1)
        lay.addWidget(ctrl_wrap, 1)
        lay.addWidget(_InfoButton(tooltip), 0)

        if advanced:
            self._advanced_rows.append(w)

        return w

    def _row_checkbox(self, label: str, checkbox: QtWidgets.QCheckBox, tooltip: str, *, advanced: bool = False) -> QtWidgets.QWidget:
        checkbox.setText("")
        checkbox.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QtWidgets.QLabel(label)
        lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._label_widgets.append(lbl)

        ctrl_wrap = QtWidgets.QWidget()
        ctrl_lay = QtWidgets.QHBoxLayout(ctrl_wrap)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.addStretch(1)
        ctrl_lay.addWidget(checkbox, 0)

        lay.addWidget(lbl, 1)
        lay.addWidget(ctrl_wrap, 1)
        lay.addWidget(_InfoButton(tooltip), 0)

        if advanced:
            self._advanced_rows.append(w)

        return w

    def _row_toggle(self, label: str, toggle: _YesNoToggle, tooltip: str, *, advanced: bool = False) -> QtWidgets.QWidget:
        toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QtWidgets.QLabel(label)
        lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._label_widgets.append(lbl)

        ctrl_wrap = QtWidgets.QWidget()
        ctrl_lay = QtWidgets.QHBoxLayout(ctrl_wrap)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.setSpacing(6)
        ctrl_lay.addWidget(toggle, 1)

        lay.addWidget(lbl, 1)
        lay.addWidget(ctrl_wrap, 1)
        lay.addWidget(_InfoButton(tooltip), 0)

        if advanced:
            self._advanced_rows.append(w)

        return w

    def _row_button(self, label: str, button: QtWidgets.QPushButton, tooltip: str, *, advanced: bool = False) -> QtWidgets.QWidget:
        button.setMinimumHeight(self.CONTROL_HEIGHT)

        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QtWidgets.QLabel(label)
        lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self._label_widgets.append(lbl)

        ctrl_wrap = QtWidgets.QWidget()
        ctrl_lay = QtWidgets.QHBoxLayout(ctrl_wrap)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.addWidget(button, 1)

        lay.addWidget(lbl, 1)
        lay.addWidget(ctrl_wrap, 1)
        lay.addWidget(_InfoButton(tooltip), 0)

        if advanced:
            self._advanced_rows.append(w)

        return w

    def _row_button_under_control(self, button: QtWidgets.QPushButton, *, advanced: bool = False) -> QtWidgets.QWidget:
        button.setMinimumHeight(self.CONTROL_HEIGHT)
        button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QtWidgets.QLabel("")
        lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

        ctrl_wrap = QtWidgets.QWidget()
        ctrl_lay = QtWidgets.QHBoxLayout(ctrl_wrap)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.addWidget(button, 1)

        lay.addWidget(lbl, 1)
        lay.addWidget(ctrl_wrap, 1)

        if advanced:
            self._advanced_rows.append(w)
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

    # ----- Section builders -----

    def _build_app_section(self, base_h: int) -> None:
        lay = QtWidgets.QVBoxLayout(self.grp_app)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.cb_app_language = QtWidgets.QComboBox()
        self.cb_app_language.setMinimumHeight(base_h)
        self.cb_app_language.addItem(tr("settings.app.language.auto"), "auto")
        self.cb_app_language.addItem("English", "en")
        self.cb_app_language.addItem("Polski", "pl")

        self.cb_app_theme = QtWidgets.QComboBox()
        self.cb_app_theme.setMinimumHeight(base_h)
        self.cb_app_theme.addItem(tr("settings.app.theme.auto"), "auto")
        self.cb_app_theme.addItem(tr("settings.app.theme.light"), "light")
        self.cb_app_theme.addItem(tr("settings.app.theme.dark"), "dark")

        self.tg_log_enabled = _YesNoToggle(
            yes_text=tr("common.yes"),
            no_text=tr("common.no"),
            height=self.CONTROL_HEIGHT,
        )
        self.cb_log_level = QtWidgets.QComboBox()
        self.cb_log_level.setMinimumHeight(base_h)
        self.cb_log_level.addItem(tr("settings.app.logging.level.info"), "info")
        self.cb_log_level.setItemData(0, tr("settings.app.logging.level.info_tip"), QtCore.Qt.ToolTipRole)
        self.cb_log_level.addItem(tr("settings.app.logging.level.warning"), "warning")
        self.cb_log_level.setItemData(1, tr("settings.app.logging.level.warning_tip"), QtCore.Qt.ToolTipRole)
        self.cb_log_level.addItem(tr("settings.app.logging.level.error"), "error")
        self.cb_log_level.setItemData(2, tr("settings.app.logging.level.error_tip"), QtCore.Qt.ToolTipRole)
        self.cb_log_level.addItem(tr("settings.app.logging.level.debug"), "debug")
        self.cb_log_level.setItemData(3, tr("settings.app.logging.level.debug_tip"), QtCore.Qt.ToolTipRole)

        self.btn_open_logs = QtWidgets.QPushButton(tr("settings.app.logging.open_folder"))
        self.btn_open_logs.clicked.connect(self._open_logs_folder)

        self.cb_log_level.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.btn_open_logs.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        lay.addWidget(self._row(tr("settings.app.language.label"), self.cb_app_language, tr("settings.help.ui_language")))
        lay.addWidget(self._row(tr("settings.app.theme.label"), self.cb_app_theme, tr("settings.help.theme")))
        lay.addWidget(self._row_toggle(tr("settings.app.logging.enabled"), self.tg_log_enabled, tr("settings.help.logging_enabled")))

        log_level_row = QtWidgets.QWidget()
        log_level_lay = QtWidgets.QHBoxLayout(log_level_row)
        log_level_lay.setContentsMargins(0, 0, 0, 0)
        log_level_lay.setSpacing(6)
        log_level_lay.addWidget(self.cb_log_level, 1)
        log_level_lay.addWidget(self.btn_open_logs, 1)

        lay.addWidget(self._row(tr("settings.app.logging.level_label"), log_level_row, tr("settings.help.logging_level")))

        lay.addStretch(1)

        self.cb_app_language.currentIndexChanged.connect(self._mark_dirty)
        self.cb_app_theme.currentIndexChanged.connect(self._mark_dirty)
        self.tg_log_enabled.toggled(self._on_logging_toggle)
        self.cb_log_level.currentIndexChanged.connect(self._mark_dirty)

    def _build_engine_section(self, base_h: int) -> None:
        lay = QtWidgets.QVBoxLayout(self.grp_engine)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.cb_engine_device = QtWidgets.QComboBox()
        self.cb_engine_device.setMinimumHeight(base_h)
        self.cb_engine_device.addItem(tr("settings.engine.device.auto"), "auto")
        self.cb_engine_device.addItem(tr("settings.engine.device.cpu"), "cpu")
        self.cb_engine_device.addItem(tr("settings.engine.device.gpu"), "cuda")

        self.cb_engine_precision = QtWidgets.QComboBox()
        self.cb_engine_precision.setMinimumHeight(base_h)
        self.cb_engine_precision.addItem(tr("settings.engine.precision.auto"), "auto")
        self.cb_engine_precision.setItemData(0, tr("settings.engine.precision.auto_tip"), QtCore.Qt.ToolTipRole)
        self.cb_engine_precision.addItem(tr("settings.engine.precision.float32"), "float32")
        self.cb_engine_precision.setItemData(1, tr("settings.engine.precision.float32_tip"), QtCore.Qt.ToolTipRole)
        self.cb_engine_precision.addItem(tr("settings.engine.precision.float16"), "float16")
        self.cb_engine_precision.setItemData(2, tr("settings.engine.precision.float16_tip"), QtCore.Qt.ToolTipRole)
        self.cb_engine_precision.addItem(tr("settings.engine.precision.bfloat16"), "bfloat16")
        self.cb_engine_precision.setItemData(3, tr("settings.engine.precision.bfloat16_tip"), QtCore.Qt.ToolTipRole)

        self.tg_tf32 = _YesNoToggle(
            yes_text=tr("common.yes"),
            no_text=tr("common.no"),
            height=self.CONTROL_HEIGHT,
        )

        lay.addWidget(self._row(tr("settings.engine.device.label"), self.cb_engine_device, tr("settings.help.device")))
        lay.addWidget(self._row(tr("settings.engine.precision.label"), self.cb_engine_precision, tr("settings.help.precision_hint")))
        lay.addWidget(self._row_toggle(tr("settings.engine.allow_tf32"), self.tg_tf32, tr("settings.help.tf32")))
        lay.addStretch(1)

        self.cb_engine_device.currentIndexChanged.connect(self._on_device_changed)
        self.cb_engine_precision.currentIndexChanged.connect(self._on_precision_changed)
        self.tg_tf32.toggled(self._mark_dirty)

    def _build_transcription_section(self, base_h: int) -> None:
        lay = QtWidgets.QVBoxLayout(self.grp_transcription)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.cb_trans_engine = QtWidgets.QComboBox()
        self.cb_trans_engine.setMinimumHeight(base_h)

        self.cb_quality = QtWidgets.QComboBox()
        self.cb_quality.setMinimumHeight(base_h)
        self.cb_quality.addItem(tr("settings.transcription.quality.fast"), "fast")
        self.cb_quality.addItem(tr("settings.transcription.quality.balanced"), "balanced")
        self.cb_quality.addItem(tr("settings.transcription.quality.accurate"), "accurate")

        self.tg_text_consistency = _YesNoToggle(
            yes_text=tr("common.yes"),
            no_text=tr("common.no"),
            height=self.CONTROL_HEIGHT,
        )

        self.sp_chunk_len = QtWidgets.QSpinBox()
        self.sp_chunk_len.setRange(5, 3600)
        self.sp_chunk_len.setSingleStep(5)
        self.sp_chunk_len.setMinimumHeight(base_h)

        self.sp_stride_len = QtWidgets.QSpinBox()
        self.sp_stride_len.setRange(0, 120)
        self.sp_stride_len.setSingleStep(1)
        self.sp_stride_len.setMinimumHeight(base_h)

        self.tg_ignore_empty = _YesNoToggle(
            yes_text=tr("common.yes"),
            no_text=tr("common.no"),
            height=self.CONTROL_HEIGHT,
        )
        self.tg_low_cpu_mem = _YesNoToggle(
            yes_text=tr("common.yes"),
            no_text=tr("common.no"),
            height=self.CONTROL_HEIGHT,
        )

        lay.addWidget(self._row(tr("settings.transcription.model"), self.cb_trans_engine, tr("settings.help.transcription_engine")))
        lay.addWidget(self._row(tr("settings.transcription.quality_label"), self.cb_quality, tr("settings.help.trans_quality")))
        lay.addWidget(self._row_toggle(tr("settings.transcription.text_consistency"), self.tg_text_consistency, tr("settings.help.text_consistency")))

        lay.addWidget(self._row(tr("settings.transcription.chunk_length_s"), self.sp_chunk_len, tr("settings.help.chunk_length"), advanced=True))
        lay.addWidget(self._row(tr("settings.transcription.stride_length_s"), self.sp_stride_len, tr("settings.help.stride_length"), advanced=True))
        lay.addWidget(self._row_toggle(tr("settings.transcription.ignore_warning"), self.tg_ignore_empty, tr("settings.help.ignore_warning"), advanced=True))
        lay.addWidget(self._row_toggle(tr("settings.model.low_cpu_mem_usage"), self.tg_low_cpu_mem, tr("settings.help.low_cpu_mem_usage"), advanced=True))

        lay.addStretch(1)

        self.cb_trans_engine.currentIndexChanged.connect(self._mark_dirty)
        self.cb_quality.currentIndexChanged.connect(self._mark_dirty)
        self.tg_text_consistency.toggled(self._mark_dirty)
        self.sp_chunk_len.valueChanged.connect(self._mark_dirty)
        self.sp_stride_len.valueChanged.connect(self._mark_dirty)
        self.tg_ignore_empty.toggled(self._mark_dirty)
        self.tg_low_cpu_mem.toggled(self._mark_dirty)

    def _build_translation_section(self, base_h: int) -> None:
        lay = QtWidgets.QVBoxLayout(self.grp_translation)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.cb_tr_engine = QtWidgets.QComboBox()
        self.cb_tr_engine.setMinimumHeight(base_h)
        self.cb_tr_engine.addItem(tr("settings.translation.engine.disabled"), "none")

        self.sp_tr_max_tokens = QtWidgets.QSpinBox()
        self.sp_tr_max_tokens.setRange(16, 8192)
        self.sp_tr_max_tokens.setSingleStep(16)
        self.sp_tr_max_tokens.setMinimumHeight(base_h)

        self.sp_tr_chunk_chars = QtWidgets.QSpinBox()
        self.sp_tr_chunk_chars.setRange(200, 20000)
        self.sp_tr_chunk_chars.setSingleStep(100)
        self.sp_tr_chunk_chars.setMinimumHeight(base_h)

        lay.addWidget(self._row(tr("settings.translation.engine.label"), self.cb_tr_engine, tr("settings.help.translation_engine")))
        lay.addWidget(self._row(tr("settings.translation.max_new_tokens"), self.sp_tr_max_tokens, tr("settings.help.translation_max_new_tokens"), advanced=True))
        lay.addWidget(self._row(tr("settings.translation.chunk_max_chars"), self.sp_tr_chunk_chars, tr("settings.help.translation_chunk_max_chars"), advanced=True))

        lay.addStretch(1)

        self.cb_tr_engine.currentIndexChanged.connect(self._mark_dirty)
        self.sp_tr_max_tokens.valueChanged.connect(self._mark_dirty)
        self.sp_tr_chunk_chars.valueChanged.connect(self._mark_dirty)

    def _build_download_section(self, base_h: int) -> None:
        lay = QtWidgets.QVBoxLayout(self.grp_download)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(16)
        lay.addLayout(cols)

        left = QtWidgets.QVBoxLayout()
        right = QtWidgets.QVBoxLayout()
        left.setSpacing(8)
        right.setSpacing(8)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)

        self.sp_min_height = QtWidgets.QSpinBox()
        self.sp_min_height.setRange(0, 10000)
        self.sp_min_height.setMinimumHeight(base_h)

        self.sp_max_height = QtWidgets.QSpinBox()
        self.sp_max_height.setRange(0, 10000)
        self.sp_max_height.setMinimumHeight(base_h)

        self.sp_retries = QtWidgets.QSpinBox()
        self.sp_retries.setRange(0, 50)
        self.sp_retries.setMinimumHeight(base_h)

        self.sp_bandwidth = QtWidgets.QSpinBox()
        self.sp_bandwidth.setRange(0, 10_000_000)
        self.sp_bandwidth.setSingleStep(100)
        self.sp_bandwidth.setMinimumHeight(base_h)

        self.sp_fragments = QtWidgets.QSpinBox()
        self.sp_fragments.setRange(1, 64)
        self.sp_fragments.setMinimumHeight(base_h)

        self.sp_timeout = QtWidgets.QSpinBox()
        self.sp_timeout.setRange(1, 600)
        self.sp_timeout.setMinimumHeight(base_h)

        left.addWidget(self._row(tr("settings.downloader.min_video_height"), self.sp_min_height, tr("settings.help.min_video_height")))
        left.addWidget(self._row(tr("settings.downloader.max_video_height"), self.sp_max_height, tr("settings.help.max_video_height")))
        left.addWidget(self._row(tr("settings.network.retries"), self.sp_retries, tr("settings.help.retries")))

        right.addWidget(self._row(tr("settings.network.max_bandwidth_kbps"), self.sp_bandwidth, tr("settings.help.max_bandwidth_kbps"), advanced=True))
        right.addWidget(self._row(tr("settings.network.concurrent_fragments"), self.sp_fragments, tr("settings.help.concurrent_fragments"), advanced=True))
        right.addWidget(self._row(tr("settings.network.http_timeout_s"), self.sp_timeout, tr("settings.help.http_timeout_s"), advanced=True))

        lay.addStretch(1)

        self.sp_min_height.valueChanged.connect(self._mark_dirty)
        self.sp_max_height.valueChanged.connect(self._mark_dirty)
        self.sp_retries.valueChanged.connect(self._mark_dirty)
        self.sp_bandwidth.valueChanged.connect(self._mark_dirty)
        self.sp_fragments.valueChanged.connect(self._mark_dirty)
        self.sp_timeout.valueChanged.connect(self._mark_dirty)

    # ----- Worker -----

    def _start_worker(self, *, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self._thread is not None:
            return

        thread = QtCore.QThread(self)
        worker = SettingsWorker(action=action, payload=payload)

        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        worker.settings_loaded.connect(self._on_settings_loaded)
        worker.saved.connect(self._on_saved)
        worker.error.connect(self._on_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._cleanup_worker)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _cleanup_worker(self) -> None:
        self._thread = None
        self._worker = None

    def _on_settings_loaded(self, data: object) -> None:
        if isinstance(data, dict):
            self._data = data
            self._loaded_data = dict(data)

        self._blocking_updates = True
        try:
            self._populate_from_data()
            self._set_dirty(False)
        finally:
            self._blocking_updates = False

    def _on_saved(self, data: object) -> None:
        if isinstance(data, dict):
            self._data = data
            self._loaded_data = dict(data)
            self._populate_from_data()

        self._set_dirty(False)

        need_restart = self._pending_restart_prompt
        self._pending_restart_prompt = False

        if need_restart:
            restart_now = dialogs.ask_restart_required(self)
            if restart_now:
                self._restart_application()
        else:
            QtWidgets.QMessageBox.information(self, tr("app.title"), tr("settings.msg.saved"))

    def _on_error(self, msg: str) -> None:
        QtWidgets.QMessageBox.critical(self, tr("app.title"), msg)

    # ----- UI state -----

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        self.btn_save.setEnabled(self._dirty)
        self.btn_undo.setEnabled(self._dirty)

    def _mark_dirty(self) -> None:
        if self._blocking_updates:
            return
        self._set_dirty(True)

    def _on_toggle_advanced(self) -> None:
        self._apply_advanced_visibility(self.chk_show_advanced.isChecked())

    def _apply_advanced_visibility(self, show: bool) -> None:
        for w in self._advanced_rows:
            w.setVisible(bool(show))

    # ----- Actions -----

    def _on_undo_clicked(self) -> None:
        if not self._loaded_data:
            return
        self._blocking_updates = True
        try:
            self._data = dict(self._loaded_data)
            self._populate_from_data()
            self._set_dirty(False)
        finally:
            self._blocking_updates = False


    def _on_restore_clicked(self) -> None:
        if not dialogs.ask_restore_defaults(self):
            return
        self._start_worker(action="restore")

    def _on_save_clicked(self) -> None:
        if not dialogs.ask_save_settings(self):
            return
        payload = self._collect_payload()
        self._pending_restart_prompt = self._needs_restart(payload)
        self._start_worker(action="save", payload=payload)

    def _restart_application(self) -> None:
        os.execl(sys.executable, sys.executable, *sys.argv)

    def _open_logs_folder(self) -> None:
        path = getattr(Config, "LOGS_DIR", None)
        if isinstance(path, Path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _on_logging_toggle(self) -> None:
        self._mark_dirty()
        self._apply_logging_enabled_state()

    def _apply_logging_enabled_state(self) -> None:
        enabled = bool(self.tg_log_enabled.is_checked())
        self.cb_log_level.setEnabled(enabled)

    def _on_device_changed(self) -> None:
        self._mark_dirty()
        self._refresh_runtime_capabilities()

    def _on_precision_changed(self) -> None:
        self._mark_dirty()
        self._refresh_runtime_capabilities()

    # ----- Data mapping -----

    @staticmethod
    def _select_combo_by_data(cb: QtWidgets.QComboBox, value: str, *, fallback: str) -> None:
        value = (value or "").strip().lower() or fallback
        for i in range(cb.count()):
            if str(cb.itemData(i)) == value:
                cb.setCurrentIndex(i)
                return
        cb.setCurrentIndex(0)

    def _populate_from_data(self) -> None:
        d = self._data or {}

        app = d.get("app", {}) if isinstance(d.get("app"), dict) else {}
        eng = d.get("engine", {}) if isinstance(d.get("engine"), dict) else {}
        model = d.get("model", {}) if isinstance(d.get("model"), dict) else {}
        t_model = model.get("transcription_model", {}) if isinstance(model.get("transcription_model"), dict) else {}
        x_model = model.get("translation_model", {}) if isinstance(model.get("translation_model"), dict) else {}
        trn = d.get("translation", {}) if isinstance(d.get("translation"), dict) else {}
        dl = d.get("downloader", {}) if isinstance(d.get("downloader"), dict) else {}
        net = d.get("network", {}) if isinstance(d.get("network"), dict) else {}

        self._select_combo_by_data(self.cb_app_language, str(app.get("language", "auto")), fallback="auto")
        self._select_combo_by_data(self.cb_app_theme, str(app.get("theme", "auto")), fallback="auto")

        log_cfg = app.get("logging", {}) if isinstance(app.get("logging"), dict) else {}
        self.tg_log_enabled.set_checked(bool(log_cfg.get("enabled", True)))
        self._select_combo_by_data(self.cb_log_level, str(log_cfg.get("level", "info")), fallback="info")
        self._apply_logging_enabled_state()

        self._select_combo_by_data(self.cb_engine_device, str(eng.get("preferred_device", "auto")), fallback="auto")
        self._select_combo_by_data(self.cb_engine_precision, str(eng.get("precision", "auto")), fallback="auto")
        self.tg_tf32.set_checked(bool(eng.get("allow_tf32", True)))

        self._populate_model_engines()

        self._select_combo_by_data(self.cb_trans_engine, str(t_model.get("engine_name", "auto")), fallback="auto")

        self._select_combo_by_data(self.cb_quality, str(t_model.get("quality_preset", "balanced")), fallback="balanced")
        self.tg_text_consistency.set_checked(bool(t_model.get("text_consistency", True)))

        self.sp_chunk_len.setValue(int(t_model.get("chunk_length_s", 60)))
        self.sp_stride_len.setValue(int(t_model.get("stride_length_s", 5)))
        self.tg_ignore_empty.set_checked(bool(t_model.get("ignore_warning", False)))
        self.tg_low_cpu_mem.set_checked(bool(t_model.get("low_cpu_mem_usage", True)))

        self._select_combo_by_data(self.cb_tr_engine, str(x_model.get("engine_name", "none")), fallback="none")

        tgt = str(trn.get("target_language", "auto") or "auto").strip().lower()

        self.sp_tr_max_tokens.setValue(int(x_model.get("max_new_tokens", 256)))
        self.sp_tr_chunk_chars.setValue(int(x_model.get("chunk_max_chars", 1200)))

        self.sp_min_height.setValue(int(dl.get("min_video_height", 144)))
        self.sp_max_height.setValue(int(dl.get("max_video_height", 4320)))

        self.sp_retries.setValue(int(net.get("retries", 3)))
        self.sp_bandwidth.setValue(int(net.get("max_bandwidth_kbps", 0) or 0))
        self.sp_fragments.setValue(int(net.get("concurrent_fragments", 2)))
        self.sp_timeout.setValue(int(net.get("http_timeout_s", 30)))

    def _collect_payload(self) -> Dict[str, Any]:
        app = {
            "language": str(self.cb_app_language.currentData() or "auto"),
            "theme": str(self.cb_app_theme.currentData() or "auto"),
            "logging": {
                "enabled": bool(self.tg_log_enabled.is_checked()),
                "level": str(self.cb_log_level.currentData() or "info"),
            },
        }

        engine = {
            "preferred_device": str(self.cb_engine_device.currentData() or "auto"),
            "precision": str(self.cb_engine_precision.currentData() or "auto"),
            "allow_tf32": bool(self.tg_tf32.is_checked()),
        }

        prev_default_language = None
        try:
            prev_default_language = (
                (((self._data or {}).get("model") or {}).get("transcription_model") or {}).get("default_language")
            )
        except Exception:
            prev_default_language = None

        transcription_model = {
            "engine_name": str(self.cb_trans_engine.currentData() or "auto"),
            "quality_preset": str(self.cb_quality.currentData() or "balanced"),
            "text_consistency": bool(self.tg_text_consistency.is_checked()),
            "chunk_length_s": int(self.sp_chunk_len.value()),
            "stride_length_s": int(self.sp_stride_len.value()),
            "ignore_warning": bool(self.tg_ignore_empty.is_checked()),
            "default_language": prev_default_language,
            "low_cpu_mem_usage": bool(self.tg_low_cpu_mem.is_checked()),
        }

        translation_model = {
            "engine_name": str(self.cb_tr_engine.currentData() or "none"),
            "max_new_tokens": int(self.sp_tr_max_tokens.value()),
            "chunk_max_chars": int(self.sp_tr_chunk_chars.value()),
            "low_cpu_mem_usage": True,
        }

        model = {
            "transcription_model": transcription_model,
            "translation_model": translation_model,
        }

        downloader = {
            "min_video_height": int(self.sp_min_height.value()),
            "max_video_height": int(self.sp_max_height.value()),
        }

        network = {
            "retries": int(self.sp_retries.value()),
            "max_bandwidth_kbps": int(self.sp_bandwidth.value()) if int(self.sp_bandwidth.value()) > 0 else None,
            "concurrent_fragments": int(self.sp_fragments.value()),
            "http_timeout_s": int(self.sp_timeout.value()),
        }

        return {
            "app": app,
            "engine": engine,
            "model": model,
            "downloader": downloader,
            "network": network,
        }

    def _needs_restart(self, payload: Dict[str, Any]) -> bool:
        cur = self._data or {}
        for path in self._RESTART_SENSITIVE_KEYS:
            if self._get_nested(cur, path) != self._get_nested(payload, path):
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

    # ----- Model engines -----

    def _populate_model_engines(self) -> None:
        base = getattr(Config, "AI_MODELS_DIR", None)
        base_ok = isinstance(base, Path) and base.exists()

        self.cb_trans_engine.blockSignals(True)
        try:
            current = str(self.cb_trans_engine.currentData() or "auto")
            self.cb_trans_engine.clear()
            self.cb_trans_engine.addItem(tr("common.auto"), "auto")

            if base_ok:
                for p in sorted(base.iterdir()):
                    if not p.is_dir() or p.name.startswith("__"):
                        continue
                    if p.name.lower() in ("m2m100",):
                        continue
                    self.cb_trans_engine.addItem(p.name, p.name)

            self._select_combo_by_data(self.cb_trans_engine, current, fallback="auto")
        finally:
            self.cb_trans_engine.blockSignals(False)

        self.cb_tr_engine.blockSignals(True)
        try:
            current_tr = str(self.cb_tr_engine.currentData() or "none")
            self.cb_tr_engine.clear()
            self.cb_tr_engine.addItem(tr("settings.translation.engine.disabled"), "none")

            if base_ok:
                for p in sorted(base.iterdir()):
                    if not p.is_dir() or p.name.startswith("__"):
                        continue
                    if p.name.lower() not in ("m2m100",):
                        continue
                    self.cb_tr_engine.addItem(p.name, p.name)

            self._select_combo_by_data(self.cb_tr_engine, current_tr, fallback="none")
        finally:
            self.cb_tr_engine.blockSignals(False)

    def _refresh_runtime_capabilities(self) -> None:
        has_cuda = bool(torch.cuda.is_available())
        bf16_supported = False
        tf32_supported = False
        try:
            if has_cuda and hasattr(torch.cuda, "is_bf16_supported"):
                bf16_supported = bool(torch.cuda.is_bf16_supported())
        except Exception:
            bf16_supported = False

        try:
            if has_cuda:
                cap = torch.cuda.get_device_capability()
                tf32_supported = bool(cap and cap[0] >= 8)
        except Exception:
            tf32_supported = False

        # Device: GPU option
        idx_cuda = self.cb_engine_device.findData("cuda")
        if idx_cuda >= 0:
            model = self.cb_engine_device.model()
            if model is not None:
                item = model.item(idx_cuda)
                if item is not None:
                    item.setEnabled(has_cuda)

        if not has_cuda and str(self.cb_engine_device.currentData() or "auto") == "cuda":
            self._select_combo_by_data(self.cb_engine_device, "auto", fallback="auto")

        # Precision: disable float16/bfloat16 when CUDA does not support it
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

        cur_prec = str(self.cb_engine_precision.currentData() or "auto")
        if cur_prec == "float16" and not has_cuda:
            self._select_combo_by_data(self.cb_engine_precision, "auto", fallback="auto")
        if cur_prec == "bfloat16" and not (has_cuda and bf16_supported):
            self._select_combo_by_data(self.cb_engine_precision, "auto", fallback="auto")

        # TF32 toggle
        cur_dev = str(self.cb_engine_device.currentData() or "auto")
        tf32_enable_allowed = bool(has_cuda and tf32_supported and cur_dev in ("auto", "cuda") and cur_prec in ("auto", "float32"))
        self.tg_tf32.setEnabled(tf32_enable_allowed)
        if not tf32_enable_allowed:
            self.tg_tf32.set_checked(False)
