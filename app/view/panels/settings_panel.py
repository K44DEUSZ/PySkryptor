# app/view/panels/settings_panel.py
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.controller.panel_protocols import SettingsCoordinatorProtocol
from app.model.core.config.config import AppConfig
from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.entities import SettingsSnapshot, snapshot_to_dict
from app.model.core.runtime.localization import list_locales, tr
from app.model.download.runtime import available_cookie_browsers, resolve_effective_cookie_browser
from app.model.download.policy import DownloadPolicy
from app.model.engines.resolution import EngineResolver
from app.model.settings.resolution import transcription_language_codes, translation_language_codes
from app.view import dialogs
from app.view.components.choice_toggle import ChoiceToggle
from app.view.components.popup_combo import LanguageCombo, PopupComboBox, set_combo_data
from app.view.components.section_group import SectionGroup
from app.view.support.options_autosave import OptionsAutosave
from app.view.support.theme_runtime import system_theme_key
from app.view.support.host_runtime import open_local_path
from app.view.support.widget_effects import enable_styled_background, repolish_widget
from app.view.support.widget_setup import (
    build_layout_host,
    build_setting_row,
    connect_qt_signal,
    setup_button,
    setup_combo,
    setup_input,
    setup_layout,
    setup_spinbox,
    setup_toggle_button,
    set_passive_cursor,
)
from app.view.ui_config import ui


def _resolve_field_default(default: Any) -> Any:
    """Return a concrete field default for settings mapping helpers."""

    return default() if callable(default) else default


def _populate_combo_fields(
    data: dict[str, Any],
    specs: tuple[tuple[str, QtWidgets.QComboBox, Any], ...],
) -> None:
    """Populate combo boxes from a flat settings section."""

    for key, combo, default in specs:
        fallback = _resolve_field_default(default)
        value = data.get(key, fallback)
        if value is None:
            value = fallback
        set_combo_data(combo, str(value), fallback_data=fallback)


def _populate_toggle_fields(
    data: dict[str, Any],
    specs: tuple[tuple[str, ChoiceToggle, bool], ...],
) -> None:
    """Populate two-state toggles from a flat settings section."""

    for key, toggle, default in specs:
        toggle.set_first_checked(bool(data.get(key, default)))


def _populate_spin_fields(
    data: dict[str, Any],
    specs: tuple[tuple[str, QtWidgets.QSpinBox, int], ...],
) -> None:
    """Populate spin boxes from a flat settings section."""

    for key, spin, default in specs:
        raw = data.get(key, default)
        try:
            spin.setValue(int(raw))
        except (TypeError, ValueError):
            spin.setValue(int(default))


def _collect_combo_fields(
    specs: tuple[tuple[str, QtWidgets.QComboBox, Any], ...],
) -> dict[str, Any]:
    """Collect combo box values into a flat payload fragment."""

    payload: dict[str, Any] = {}
    for key, combo, default in specs:
        fallback = _resolve_field_default(default)
        value = combo.currentData()
        payload[key] = fallback if value is None else str(value)
    return payload


def _collect_toggle_fields(
    specs: tuple[tuple[str, ChoiceToggle], ...],
) -> dict[str, bool]:
    """Collect two-state toggles into a flat payload fragment."""

    return {key: bool(toggle.is_first_checked()) for key, toggle in specs}


def _collect_spin_fields(
    specs: tuple[tuple[str, QtWidgets.QSpinBox], ...],
    *,
    none_if_non_positive: set[str] | None = None,
    none_if_negative: set[str] | None = None,
) -> dict[str, int | None]:
    """Collect spin box values into a flat payload fragment."""

    nullable_non_positive = set(none_if_non_positive or ())
    nullable_negative = set(none_if_negative or ())
    payload: dict[str, int | None] = {}
    for key, spin in specs:
        value = int(spin.value())
        if key in nullable_negative and value < 0:
            payload[key] = None
            continue
        payload[key] = None if key in nullable_non_positive and value <= 0 else value
    return payload


class _YesNoToggle(ChoiceToggle):
    """Convenience yes/no toggle."""

    def __init__(
        self,
        *,
        yes_text: str,
        no_text: str,
        height: int,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(
            first_text=yes_text,
            second_text=no_text,
            height=height,
            parent=parent,
        )


class SettingsPanel(QtWidgets.QWidget):
    """Settings page with app, engine and downloader configuration."""

    _data: dict[str, Any]
    _loaded_data: dict[str, Any] | None
    _blocking_updates: bool
    _pending_restart_prompt: bool
    _restore_baseline_data: dict[str, Any] | None

    _RESTART_SENSITIVE_KEYS: tuple[tuple[str, ...], ...] = (
        ("app", "language"),
        ("app", "theme"),
        ("app", "logging", "enabled"),
        ("app", "logging", "level"),
        ("engine", "preferred_device"),
        ("engine", "precision"),
        ("engine", "fp32_math_mode"),
        ("model", "transcription_model", "engine_name"),
        ("model", "translation_model", "engine_name"),
    )

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)
        set_passive_cursor(self)
        self._panel_coordinator: SettingsCoordinatorProtocol | None = None

        self._init_state()
        self._build_ui()
        self._wire_signals()
        self._restore_initial_state()

    def bind_coordinator(self, coordinator: SettingsCoordinatorProtocol) -> None:
        self._panel_coordinator = coordinator

    def coordinator(self) -> SettingsCoordinatorProtocol | None:
        return self._panel_coordinator

    def _coordinator_is_busy(self) -> bool:
        coord = self.coordinator()
        return bool(coord is not None and coord.is_busy())

    def _init_state(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded_data: dict[str, Any] | None = None
        self._dirty = False
        self._blocking_updates: bool = False
        self._pending_restart_prompt: bool = False
        self._restore_baseline_data: dict[str, Any] | None = None

        self._advanced_rows: list[QtWidgets.QWidget] = []
        self._dirty_row_specs: list[tuple[QtWidgets.QWidget, QtWidgets.QWidget, tuple[tuple[str, ...], ...]]] = []
        self._setting_labels_by_row_id: dict[int, QtWidgets.QWidget] = {}
        self._setting_controls_by_row_id: dict[int, QtWidgets.QWidget] = {}
        self._cookie_browser_row: QtWidgets.QWidget | None = None
        self._cookie_file_row: QtWidgets.QWidget | None = None
        self._bulk_add_threshold_row: QtWidgets.QWidget | None = None
        self._log_level_row: QtWidgets.QWidget | None = None
        self._row_fp32_math_mode: QtWidgets.QWidget | None = None
        self._left_col_host: QtWidgets.QWidget | None = None
        self._right_col_host: QtWidgets.QWidget | None = None
        self.btn_save: QtWidgets.QPushButton | None = None
        self.btn_undo: QtWidgets.QPushButton | None = None
        self.chk_show_advanced: QtWidgets.QCheckBox | None = None

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
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_bottom_bar(self, base_h: int) -> QtWidgets.QHBoxLayout:
        cfg = self._ui
        bottom = QtWidgets.QHBoxLayout()
        setup_layout(bottom, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        self.chk_show_advanced = QtWidgets.QCheckBox(tr("settings.advanced.toggle"))
        setup_toggle_button(self.chk_show_advanced, min_h=base_h)
        self.chk_show_advanced.setChecked(False)

        self._adv_autosave = OptionsAutosave(
            self,
            build_payload=self._build_advanced_payload,
            commit=self._commit_advanced_payload,
            is_busy=self._coordinator_is_busy,
            interval_ms=600,
            pending_delay_ms=250,
        )

        self.btn_restore = QtWidgets.QPushButton(tr("settings.buttons.restore_defaults"))
        self.btn_undo = QtWidgets.QPushButton(tr("settings.buttons.undo"))
        self.btn_save = QtWidgets.QPushButton(tr("settings.buttons.save"))
        setup_button(self.btn_restore, min_h=base_h)
        setup_button(self.btn_undo, min_h=base_h)
        setup_button(self.btn_save, min_h=base_h)
        self.btn_undo.setEnabled(bool(self._dirty))
        self.btn_save.setEnabled(bool(self._dirty))

        bottom.addWidget(self.chk_show_advanced, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_restore)
        bottom.addWidget(self.btn_undo)
        bottom.addWidget(self.btn_save)
        return bottom

    def _wire_signals(self) -> None:
        btn_restore = self.btn_restore
        btn_undo = self.btn_undo
        btn_save = self.btn_save
        if btn_restore is None or btn_undo is None or btn_save is None:
            raise RuntimeError("Settings panel controls are not initialized.")
        chk_show_advanced = self.chk_show_advanced
        if chk_show_advanced is None:
            raise RuntimeError("Advanced-settings toggle is not initialized.")
        connect_qt_signal(chk_show_advanced.stateChanged, self._on_toggle_advanced)
        btn_restore.clicked.connect(self._on_restore_clicked)
        btn_undo.clicked.connect(self._on_undo_clicked)
        btn_save.clicked.connect(self._on_save_clicked)

    def _restore_initial_state(self) -> None:
        self._populate_model_engines()
        self._refresh_runtime_capabilities()
        self._apply_advanced_visibility(False)
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    def showEvent(self, e: QtGui.QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(e)
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    def _equalize_section_widths(self) -> None:
        pairs = (
            (self.grp_app, self.grp_engine),
            (self.grp_transcription, self.grp_translation),
        )
        for a, b in pairs:
            w = max(int(a.minimumSizeHint().width()), int(b.minimumSizeHint().width()), 0)
            a.setMinimumWidth(w)
            b.setMinimumWidth(w)

        if self._left_col_host is not None and self._right_col_host is not None:
            left_w = int(self._left_col_host.minimumSizeHint().width())
            right_w = int(self._right_col_host.minimumSizeHint().width())
            w = max(left_w, right_w, 0)
            self._left_col_host.setMinimumWidth(w)
            self._right_col_host.setMinimumWidth(w)

    def _sync_column_widths(self) -> None:
        """Keep paired Settings sections visually 50/50 by equalizing minimum widths."""
        self._equalize_section_widths()

    @staticmethod
    def _section_header(text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setProperty("role", "sectionTitle")
        lbl.setWordWrap(True)
        return lbl

    def _bind_setting_row(
        self,
        row: QtWidgets.QWidget,
        *,
        label: QtWidgets.QWidget | None = None,
        control: QtWidgets.QWidget | None = None,
    ) -> None:
        row_id = id(row)
        if isinstance(label, QtWidgets.QWidget):
            self._setting_labels_by_row_id[row_id] = label
        if isinstance(control, QtWidgets.QWidget):
            self._setting_controls_by_row_id[row_id] = control

    def _row_label(self, row: QtWidgets.QWidget | None) -> QtWidgets.QWidget | None:
        if not isinstance(row, QtWidgets.QWidget):
            return None
        return self._setting_labels_by_row_id.get(id(row))

    def _row_control(self, row: QtWidgets.QWidget | None) -> QtWidgets.QWidget | None:
        if not isinstance(row, QtWidgets.QWidget):
            return None
        return self._setting_controls_by_row_id.get(id(row))

    def _build_labeled_row(
        self,
        *,
        label: str,
        control: QtWidgets.QWidget,
        tooltip: str,
        control_host: QtWidgets.QWidget | None = None,
        advanced: bool = False,
        include_info: bool = True,
        track_dirty_label: bool = True,
    ) -> QtWidgets.QWidget:
        row, label_widget = build_setting_row(
            label_text=label,
            control=control,
            tooltip=tooltip,
            cfg=self._ui,
            control_host=control_host,
            include_info=include_info,
            label_role="settingsRowLabel" if track_dirty_label else None,
        )
        self._bind_setting_row(row, label=label_widget, control=control)
        if advanced:
            self._advanced_rows.append(row)
        return row

    def _row(self, label: str, control: QtWidgets.QWidget, tooltip: str, *,
             advanced: bool = False) -> QtWidgets.QWidget:
        control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return self._build_labeled_row(label=label, control=control, tooltip=tooltip, advanced=advanced)

    def _row_toggle(self, label: str, toggle: ChoiceToggle, tooltip: str, *,
                    advanced: bool = False) -> QtWidgets.QWidget:
        toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return self._build_labeled_row(label=label, control=toggle, tooltip=tooltip, advanced=advanced)

    def _track_dirty_row(
        self,
        row: QtWidgets.QWidget,
        *paths: tuple[str, ...],
        value_widget: QtWidgets.QWidget | None = None,
    ) -> QtWidgets.QWidget:
        control = value_widget if isinstance(value_widget, QtWidgets.QWidget) else self._row_control(row)
        if isinstance(control, QtWidgets.QWidget) and paths:
            spec = tuple(tuple(path) for path in paths if path)
            if spec:
                self._dirty_row_specs.append((row, control, spec))
                self._set_dirty_marker(control, False)
        return row

    @staticmethod
    def _set_dirty_marker(control: QtWidgets.QWidget, dirty: bool) -> None:
        dirty = bool(dirty)
        if isinstance(control, ChoiceToggle):
            control.set_dirty_value(dirty)
            return

        widgets: list[QtWidgets.QWidget] = [control]
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
                repolish_widget(widget)

    @staticmethod
    def _normalize_payload_for_compare(data: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        normalized = copy.deepcopy(data)

        model = normalized.get("model")
        if isinstance(model, dict):
            x_model = model.get("translation_model")
            if isinstance(x_model, dict):
                for key, fallback in (("max_new_tokens", 256), ("chunk_max_chars", 1200)):
                    try:
                        x_model[key] = int(x_model.get(key, fallback))
                    except (TypeError, ValueError):
                        x_model[key] = int(fallback)

        transcription = normalized.get("transcription")
        if isinstance(transcription, dict):
            raw_source = transcription.get("default_source_language", LanguagePolicy.AUTO)
            transcription["default_source_language"] = (
                LanguagePolicy.normalize_default_source_language_policy(raw_source)
            )

        translation = normalized.get("translation")
        if isinstance(translation, dict):
            raw_target = translation.get("default_target_language", LanguagePolicy.DEFAULT_UI)
            translation["default_target_language"] = LanguagePolicy.normalize_default_target_language_policy(raw_target)

        app = normalized.get("app")
        if isinstance(app, dict):
            ui_cfg = app.get("ui")
            if isinstance(ui_cfg, dict):
                bulk_cfg = ui_cfg.get("bulk_add_confirmation")
                if isinstance(bulk_cfg, dict):
                    try:
                        bulk_cfg["threshold"] = int(
                            bulk_cfg.get("threshold", AppConfig.ui_bulk_add_confirmation_threshold())
                        )
                    except (TypeError, ValueError):
                        bulk_cfg["threshold"] = int(AppConfig.ui_bulk_add_confirmation_threshold())

        return normalized

    def _is_transcription_custom_profile_active(self) -> bool:
        profile = RuntimeProfiles.normalize_transcription_profile(
            self.cmb_transcription_profile.currentData() or RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE
        )
        return profile == RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM

    def _is_translation_custom_profile_active(self) -> bool:
        profile = RuntimeProfiles.normalize_translation_profile(
            self.cmb_translation_profile.currentData() or RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE
        )
        return profile == RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM

    def _should_ignore_dirty_paths(self, paths: tuple[tuple[str, ...], ...]) -> bool:
        if not paths:
            return False

        transcription_custom_prefix = ("model", "transcription_model", "advanced")
        translation_custom_prefix = ("model", "translation_model", "advanced")
        transcription_custom_keys = {
            "context_policy",
            "silence_guard",
            "language_stability",
            "chunk_length_s",
            "stride_length_s",
        }
        translation_custom_keys = {
            "style",
            "num_beams",
            "no_repeat_ngram_size",
        }

        for path in paths:
            if len(path) == 4 and path[:3] == transcription_custom_prefix and path[3] in transcription_custom_keys:
                return not self._is_transcription_custom_profile_active()
            if len(path) == 4 and path[:3] == translation_custom_prefix and path[3] in translation_custom_keys:
                return not self._is_translation_custom_profile_active()
        return False

    def _refresh_dirty_markers(self) -> None:
        baseline = self._normalize_payload_for_compare(
            self._loaded_data if isinstance(self._loaded_data, dict) else None
        )
        if baseline is None:
            for _row, control, _paths in self._dirty_row_specs:
                self._set_dirty_marker(control, False)
            self._set_dirty(False)
            return

        current = self._normalize_payload_for_compare(self._collect_payload()) or {}
        any_dirty = False
        for _row, control, paths in self._dirty_row_specs:
            if self._should_ignore_dirty_paths(paths):
                row_dirty = False
            else:
                row_dirty = any(self._get_nested(current, path) != self._get_nested(baseline, path) for path in paths)
            self._set_dirty_marker(control, row_dirty)
            any_dirty = any_dirty or row_dirty

        self._set_dirty(any_dirty)

    def _prepare_section_layout(self, group: SectionGroup, *, title_key: str) -> QtWidgets.QVBoxLayout:
        cfg = self._ui
        lay = cast(QtWidgets.QVBoxLayout, group.root)
        setup_layout(lay, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.space_s)
        lay.addWidget(self._section_header(tr(title_key)))
        return lay

    def _add_tracked_row(
        self,
        layout: QtWidgets.QBoxLayout,
        row: QtWidgets.QWidget,
        *paths: tuple[str, ...],
        value_widget: QtWidgets.QWidget | None = None,
    ) -> QtWidgets.QWidget:
        tracked = self._track_dirty_row(row, *paths, value_widget=value_widget)
        layout.addWidget(tracked)
        return tracked

    @staticmethod
    def _new_combo(base_h: int) -> PopupComboBox:
        combo = PopupComboBox()
        setup_combo(combo, min_h=base_h)
        return combo

    def _new_toggle(
        self,
        *,
        yes_text: str | None = None,
        no_text: str | None = None,
    ) -> _YesNoToggle:
        return _YesNoToggle(
            yes_text=yes_text or tr("common.yes"),
            no_text=no_text or tr("common.no"),
            height=self._ui.control_min_h,
        )

    @staticmethod
    def _new_spinbox(
        base_h: int,
        minimum: int,
        maximum: int,
        *,
        step: int | None = None,
    ) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox()
        spin.setRange(minimum, maximum)
        if isinstance(step, int) and step > 0:
            spin.setSingleStep(step)
        setup_spinbox(spin, min_h=base_h)
        return spin

    @staticmethod
    def _new_line_edit(base_h: int) -> QtWidgets.QLineEdit:
        edit = QtWidgets.QLineEdit()
        setup_input(edit, min_h=base_h)
        return edit

    @staticmethod
    def _add_combo_option(
        combo: QtWidgets.QComboBox,
        label_key: str,
        data: Any,
        *,
        tooltip_key: str | None = None,
    ) -> None:
        combo.addItem(tr(label_key), data)
        if tooltip_key:
            idx = combo.count() - 1
            combo.setItemData(idx, tr(tooltip_key), QtCore.Qt.ItemDataRole.ToolTipRole)

    def _connect_mark_dirty(self, *signals: Any) -> None:
        for signal in signals:
            signal.connect(self._mark_dirty)

    def _connect_spinbox_mark_dirty(self, *spins: QtWidgets.QAbstractSpinBox) -> None:
        for spin in spins:
            spin.valueChanged.connect(self._mark_dirty)
            spin.editingFinished.connect(self._mark_dirty)

    @staticmethod
    def _section_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
        value = data.get(key)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _build_split_control_row(
        cfg: Any,
        left: QtWidgets.QWidget,
        right: QtWidgets.QWidget,
    ) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        setup_layout(layout, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.space_s)
        layout.addWidget(left, 1)
        layout.addWidget(right, 1)
        return row

    def _build_logging_level_row(self, cfg: Any) -> QtWidgets.QWidget:
        return self._build_split_control_row(cfg, self.cmb_log_level, self.btn_open_logs)

    def _rebuild_browser_cookies_mode_combo(self, selected_mode: str | None = None) -> None:
        current_mode = str(
            selected_mode or self.cmb_browser_cookies_mode.currentData() or AppConfig.browser_cookies_mode()
        ).strip().lower() or "none"
        checkbox = self.chk_show_advanced
        show_advanced = bool(checkbox is not None and checkbox.isChecked())
        allow_file = show_advanced or current_mode == "from_file"
        self.cmb_browser_cookies_mode.blockSignals(True)
        try:
            self.cmb_browser_cookies_mode.clear()
            self._add_combo_option(self.cmb_browser_cookies_mode, "settings.browser_cookies.mode.none", "none")
            self._add_combo_option(
                self.cmb_browser_cookies_mode,
                "settings.browser_cookies.mode.from_browser",
                "from_browser",
            )
            if allow_file:
                self._add_combo_option(
                    self.cmb_browser_cookies_mode,
                    "settings.browser_cookies.mode.from_file",
                    "from_file",
                )
            set_combo_data(
                self.cmb_browser_cookies_mode,
                current_mode,
                fallback_data=AppConfig.browser_cookies_mode(),
            )
        finally:
            self.cmb_browser_cookies_mode.blockSignals(False)

    def _build_app_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_app, title_key="settings.section.app")

        self.cmb_app_language = self._new_combo(base_h)
        self.cmb_app_language.addItem(tr("common.auto"), LanguagePolicy.AUTO)
        for code, name in list_locales(AppConfig.PATHS.LOCALES_DIR):
            self.cmb_app_language.addItem(name, code)

        self.cmb_app_theme = self._new_combo(base_h)
        self.cmb_app_theme.addItem(tr("common.auto"), LanguagePolicy.AUTO)
        self._add_combo_option(self.cmb_app_theme, "settings.app.theme.light", "light")
        self._add_combo_option(self.cmb_app_theme, "settings.app.theme.dark", "dark")

        self.sp_bulk_add_threshold = self._new_spinbox(
            base_h,
            AppConfig.BULK_ADD_CONFIRMATION_MIN_THRESHOLD,
            AppConfig.BULK_ADD_CONFIRMATION_MAX_THRESHOLD,
        )
        self.tg_bulk_add_warning_enabled = self._new_toggle(
            yes_text=tr("common.enable"),
            no_text=tr("common.disable"),
        )

        self.tg_log_enabled = self._new_toggle()

        self.cmb_log_level = self._new_combo(base_h)
        self._add_combo_option(self.cmb_log_level, "settings.app.logging.level.debug", "debug",
                               tooltip_key="settings.app.logging.level.debug_tip")
        self._add_combo_option(self.cmb_log_level, "settings.app.logging.level.info", "info",
                               tooltip_key="settings.app.logging.level.info_tip")
        self._add_combo_option(self.cmb_log_level, "settings.app.logging.level.warning", "warning",
                               tooltip_key="settings.app.logging.level.warning_tip")
        self._add_combo_option(self.cmb_log_level, "settings.app.logging.level.error", "error",
                               tooltip_key="settings.app.logging.level.error_tip")

        self.btn_open_logs = QtWidgets.QPushButton(tr("settings.app.logging.open_folder"))
        setup_button(self.btn_open_logs, min_h=base_h)
        self.btn_open_logs.clicked.connect(self._open_logs_folder)

        self._add_tracked_row(
            lay,
            self._row(tr("settings.app.language.label"), self.cmb_app_language, tr("settings.help.ui_language")),
            ("app", "language"),
        )
        self._add_tracked_row(
            lay,
            self._row(tr("settings.app.theme.label"), self.cmb_app_theme, tr("settings.help.theme")),
            ("app", "theme"),
        )
        self._bulk_add_threshold_row = self._add_tracked_row(
            lay,
            self._row(
                tr("settings.app.bulk_add_confirmation.threshold"),
                self.sp_bulk_add_threshold,
                tr("settings.help.bulk_add_confirmation.threshold"),
            ),
            ("app", "ui", "bulk_add_confirmation", "threshold"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.app.bulk_add_confirmation.label"),
                self.tg_bulk_add_warning_enabled,
                tr("settings.help.bulk_add_confirmation.enabled"),
            ),
            ("app", "ui", "bulk_add_confirmation", "enabled"),
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
            self._build_labeled_row(
                label=tr("settings.app.logging.level_label"),
                control=self.cmb_log_level,
                tooltip=tr("settings.help.logging_level"),
                control_host=self._build_logging_level_row(self._ui),
                advanced=True,
            ),
            ("app", "logging", "level"),
        )

        lay.addStretch(1)

        self._connect_mark_dirty(
            self.cmb_app_language.currentIndexChanged,
            self.cmb_app_theme.currentIndexChanged,
            self.cmb_log_level.currentIndexChanged,
        )
        self._connect_spinbox_mark_dirty(self.sp_bulk_add_threshold)
        self.tg_bulk_add_warning_enabled.changed.connect(self._on_bulk_add_warning_toggle)
        self.tg_log_enabled.changed.connect(self._on_logging_toggle)
        self._on_bulk_add_warning_toggle()
        self._on_logging_toggle()

    def _build_engine_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_engine, title_key="settings.section.engine")

        self.cmb_engine_device = self._new_combo(base_h)
        self._add_combo_option(self.cmb_engine_device, "settings.engine.device.auto", LanguagePolicy.AUTO)
        self._add_combo_option(self.cmb_engine_device, "settings.engine.device.cpu", "cpu")
        self._add_combo_option(self.cmb_engine_device, "settings.engine.device.gpu", "cuda")

        self.cmb_engine_precision = self._new_combo(base_h)
        self._add_combo_option(self.cmb_engine_precision, "settings.engine.precision.auto", LanguagePolicy.AUTO,
                               tooltip_key="settings.engine.precision.auto_tip")
        self._add_combo_option(self.cmb_engine_precision, "settings.engine.precision.float32", "float32",
                               tooltip_key="settings.engine.precision.float32_tip")
        self._add_combo_option(self.cmb_engine_precision, "settings.engine.precision.float16", "float16",
                               tooltip_key="settings.engine.precision.float16_tip")
        self._add_combo_option(self.cmb_engine_precision, "settings.engine.precision.bfloat16", "bfloat16",
                               tooltip_key="settings.engine.precision.bfloat16_tip")

        self.tg_fp32_math_mode = self._new_toggle()
        self.tg_low_cpu_mem = self._new_toggle()

        self._add_tracked_row(
            lay,
            self._row(tr("settings.engine.device.label"), self.cmb_engine_device, tr("settings.help.device")),
            ("engine", "preferred_device"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.engine.precision.label"),
                self.cmb_engine_precision,
                tr("settings.help.precision_hint"),
            ),
            ("engine", "precision"),
        )
        self._row_fp32_math_mode = self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.engine.fp32_math_mode"),
                self.tg_fp32_math_mode,
                tr("settings.help.fp32_math_mode"),
            ),
            ("engine", "fp32_math_mode"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.engine.low_cpu_mem_usage"),
                self.tg_low_cpu_mem,
                tr("settings.help.low_cpu_mem_usage"),
            ),
            ("engine", "low_cpu_mem_usage"),
        )
        lay.addStretch(1)

        self.cmb_engine_device.currentIndexChanged.connect(self._on_device_changed)
        self.cmb_engine_precision.currentIndexChanged.connect(self._on_precision_changed)
        self._connect_mark_dirty(self.tg_fp32_math_mode.changed, self.tg_low_cpu_mem.changed)

    def _build_transcription_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_transcription, title_key="settings.section.transcription")

        self.cmb_trans_engine = self._new_combo(base_h)

        self.cmb_transcription_profile = self._new_combo(base_h)
        self._add_combo_option(
            self.cmb_transcription_profile,
            "settings.profile.fast",
            RuntimeProfiles.TRANSCRIPTION_PROFILE_FAST,
            tooltip_key="settings.profile.fast_tip",
        )
        self._add_combo_option(
            self.cmb_transcription_profile,
            "settings.profile.balanced",
            RuntimeProfiles.TRANSCRIPTION_PROFILE_BALANCED,
            tooltip_key="settings.profile.balanced_tip",
        )
        self._add_combo_option(
            self.cmb_transcription_profile,
            "settings.profile.accurate",
            RuntimeProfiles.TRANSCRIPTION_PROFILE_ACCURATE,
            tooltip_key="settings.profile.accurate_tip",
        )
        self._add_combo_option(
            self.cmb_transcription_profile,
            "settings.profile.guarded",
            RuntimeProfiles.TRANSCRIPTION_PROFILE_GUARDED,
            tooltip_key="settings.profile.guarded_tip",
        )
        self._add_combo_option(
            self.cmb_transcription_profile,
            "settings.profile.custom",
            RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM,
            tooltip_key="settings.profile.custom_tip",
        )

        self.cmb_default_language = LanguageCombo(
            special_first=(
                ("lang.special.auto_detect", LanguagePolicy.AUTO),
                ("lang.special.last_used", LanguagePolicy.LAST_USED),
            ),
            codes_provider=transcription_language_codes,
        )
        self.cmb_default_language.setMinimumHeight(base_h)

        self.cmb_context_policy = self._new_combo(base_h)
        self._add_combo_option(
            self.cmb_context_policy,
            "settings.context_policy.off",
            RuntimeProfiles.CONTEXT_POLICY_OFF,
            tooltip_key="settings.context_policy.off_tip",
        )
        self._add_combo_option(
            self.cmb_context_policy,
            "settings.context_policy.auto",
            RuntimeProfiles.CONTEXT_POLICY_AUTO,
            tooltip_key="settings.context_policy.auto_tip",
        )
        self._add_combo_option(
            self.cmb_context_policy,
            "settings.context_policy.aggressive",
            RuntimeProfiles.CONTEXT_POLICY_AGGRESSIVE,
            tooltip_key="settings.context_policy.aggressive_tip",
        )

        self.cmb_silence_guard = self._new_combo(base_h)
        self._add_combo_option(
            self.cmb_silence_guard,
            "settings.silence_guard.off",
            RuntimeProfiles.SILENCE_GUARD_OFF,
            tooltip_key="settings.silence_guard.off_tip",
        )
        self._add_combo_option(
            self.cmb_silence_guard,
            "settings.silence_guard.normal",
            RuntimeProfiles.SILENCE_GUARD_NORMAL,
            tooltip_key="settings.silence_guard.normal_tip",
        )
        self._add_combo_option(
            self.cmb_silence_guard,
            "settings.silence_guard.strict",
            RuntimeProfiles.SILENCE_GUARD_STRICT,
            tooltip_key="settings.silence_guard.strict_tip",
        )

        self.cmb_language_stability = self._new_combo(base_h)
        self._add_combo_option(
            self.cmb_language_stability,
            "settings.language_stability.fast",
            RuntimeProfiles.LANGUAGE_STABILITY_FAST,
            tooltip_key="settings.language_stability.fast_tip",
        )
        self._add_combo_option(
            self.cmb_language_stability,
            "settings.language_stability.balanced",
            RuntimeProfiles.LANGUAGE_STABILITY_BALANCED,
            tooltip_key="settings.language_stability.balanced_tip",
        )
        self._add_combo_option(
            self.cmb_language_stability,
            "settings.language_stability.strict",
            RuntimeProfiles.LANGUAGE_STABILITY_STRICT,
            tooltip_key="settings.language_stability.strict_tip",
        )

        self.sp_chunk_length_s = self._new_spinbox(base_h, 0, 120, step=5)
        self.sp_stride_length_s = self._new_spinbox(base_h, -1, 30, step=1)
        self.tg_ignore_warning = self._new_toggle()

        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.model"),
                self.cmb_trans_engine,
                tr("settings.help.transcription_engine"),
            ),
            ("model", "transcription_model", "engine_name"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.default_language"),
                self.cmb_default_language,
                tr("settings.help.default_language"),
            ),
            ("transcription", "default_source_language"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.profile_label"),
                self.cmb_transcription_profile,
                tr("settings.help.transcription_profile"),
            ),
            ("model", "transcription_model", "profile"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.context_policy"),
                self.cmb_context_policy,
                tr("settings.help.context_policy"),
                advanced=True,
            ),
            ("model", "transcription_model", "advanced", "context_policy"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.silence_guard"),
                self.cmb_silence_guard,
                tr("settings.help.silence_guard"),
                advanced=True,
            ),
            ("model", "transcription_model", "advanced", "silence_guard"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.language_stability"),
                self.cmb_language_stability,
                tr("settings.help.language_stability"),
                advanced=True,
            ),
            ("model", "transcription_model", "advanced", "language_stability"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.chunk_length_s"),
                self.sp_chunk_length_s,
                tr("settings.help.transcription_chunk_length_s"),
                advanced=True,
            ),
            ("model", "transcription_model", "advanced", "chunk_length_s"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.transcription.stride_length_s"),
                self.sp_stride_length_s,
                tr("settings.help.transcription_stride_length_s"),
                advanced=True,
            ),
            ("model", "transcription_model", "advanced", "stride_length_s"),
        )
        self._add_tracked_row(
            lay,
            self._row_toggle(
                tr("settings.transcription.ignore_warning"),
                self.tg_ignore_warning,
                tr("settings.help.ignore_warning"),
            ),
            ("model", "transcription_model", "ignore_warning"),
        )

        lay.addStretch(1)

        self._connect_mark_dirty(
            self.cmb_trans_engine.currentIndexChanged,
            self.cmb_default_language.currentIndexChanged,
            self.cmb_context_policy.currentIndexChanged,
            self.cmb_silence_guard.currentIndexChanged,
            self.cmb_language_stability.currentIndexChanged,
            self.tg_ignore_warning.changed,
        )
        self._connect_spinbox_mark_dirty(self.sp_chunk_length_s, self.sp_stride_length_s)
        self.cmb_context_policy.currentIndexChanged.connect(self._on_transcription_advanced_changed)
        self.cmb_silence_guard.currentIndexChanged.connect(self._on_transcription_advanced_changed)
        self.cmb_language_stability.currentIndexChanged.connect(self._on_transcription_advanced_changed)
        self.sp_chunk_length_s.valueChanged.connect(self._on_transcription_advanced_changed)
        self.sp_stride_length_s.valueChanged.connect(self._on_transcription_advanced_changed)

    def _build_translation_section(self, base_h: int) -> None:
        lay = self._prepare_section_layout(self.grp_translation, title_key="settings.section.translation")

        self.cmb_tr_engine = self._new_combo(base_h)
        self.cmb_translation_profile = self._new_combo(base_h)
        self._add_combo_option(
            self.cmb_translation_profile,
            "settings.profile.fast",
            RuntimeProfiles.TRANSLATION_PROFILE_FAST,
            tooltip_key="settings.translation.profile.fast_tip",
        )
        self._add_combo_option(
            self.cmb_translation_profile,
            "settings.profile.balanced",
            RuntimeProfiles.TRANSLATION_PROFILE_BALANCED,
            tooltip_key="settings.translation.profile.balanced_tip",
        )
        self._add_combo_option(
            self.cmb_translation_profile,
            "settings.profile.accurate",
            RuntimeProfiles.TRANSLATION_PROFILE_ACCURATE,
            tooltip_key="settings.translation.profile.accurate_tip",
        )
        self._add_combo_option(
            self.cmb_translation_profile,
            "settings.profile.custom",
            RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM,
            tooltip_key="settings.translation.profile.custom_tip",
        )
        self.cmb_tr_engine.addItem(tr("settings.translation.engine.disabled"), "none")

        self.cmb_default_target_language = LanguageCombo(
            special_first=(
                ("lang.special.app_language", LanguagePolicy.DEFAULT_UI),
                ("lang.special.last_used", LanguagePolicy.LAST_USED),
            ),
            codes_provider=translation_language_codes,
        )
        self.cmb_default_target_language.setMinimumHeight(base_h)

        self.cmb_tr_style = self._new_combo(base_h)
        self._add_combo_option(
            self.cmb_tr_style,
            "settings.translation.style.literal",
            RuntimeProfiles.TRANSLATION_STYLE_LITERAL,
            tooltip_key="settings.translation.style.literal_tip",
        )
        self._add_combo_option(
            self.cmb_tr_style,
            "settings.translation.style.balanced",
            RuntimeProfiles.TRANSLATION_STYLE_BALANCED,
            tooltip_key="settings.translation.style.balanced_tip",
        )
        self._add_combo_option(
            self.cmb_tr_style,
            "settings.translation.style.fluent",
            RuntimeProfiles.TRANSLATION_STYLE_FLUENT,
            tooltip_key="settings.translation.style.fluent_tip",
        )

        self.sp_tr_beams = self._new_spinbox(base_h, 0, 12, step=1)
        self.sp_tr_no_repeat = self._new_spinbox(base_h, -1, 8, step=1)
        self.sp_tr_max_tokens = self._new_spinbox(base_h, 16, 8192, step=16)
        self.sp_tr_chunk_chars = self._new_spinbox(base_h, 200, 20000, step=100)

        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.engine.label"),
                self.cmb_tr_engine,
                tr("settings.help.translation_engine"),
            ),
            ("model", "translation_model", "engine_name"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.default_language"),
                self.cmb_default_target_language,
                tr("settings.help.translation_default_language"),
            ),
            ("translation", "default_target_language"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.profile.label"),
                self.cmb_translation_profile,
                tr("settings.help.translation_profile"),
            ),
            ("model", "translation_model", "profile"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.style.label"),
                self.cmb_tr_style,
                tr("settings.help.translation_style"),
                advanced=True,
            ),
            ("model", "translation_model", "advanced", "style"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.num_beams"),
                self.sp_tr_beams,
                tr("settings.help.translation_num_beams"),
                advanced=True,
            ),
            ("model", "translation_model", "advanced", "num_beams"),
        )
        self._add_tracked_row(
            lay,
            self._row(
                tr("settings.translation.no_repeat_ngram_size"),
                self.sp_tr_no_repeat,
                tr("settings.help.translation_no_repeat_ngram_size"),
                advanced=True,
            ),
            ("model", "translation_model", "advanced", "no_repeat_ngram_size"),
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
            self.cmb_tr_engine.currentIndexChanged,
            self.cmb_default_target_language.currentIndexChanged,
            self.cmb_tr_style.currentIndexChanged,
        )
        self._connect_spinbox_mark_dirty(
            self.sp_tr_beams,
            self.sp_tr_no_repeat,
            self.sp_tr_max_tokens,
            self.sp_tr_chunk_chars,
        )
        self.cmb_transcription_profile.currentIndexChanged.connect(self._on_transcription_profile_changed)
        self.cmb_translation_profile.currentIndexChanged.connect(self._on_translation_profile_changed)
        self.cmb_tr_style.currentIndexChanged.connect(self._on_translation_advanced_changed)
        self.sp_tr_beams.valueChanged.connect(self._on_translation_advanced_changed)
        self.sp_tr_no_repeat.valueChanged.connect(self._on_translation_advanced_changed)

    def _build_download_section(self, base_h: int) -> None:
        cfg = self._ui
        lay = self._prepare_section_layout(self.grp_download, title_key="settings.section.download")

        cols = QtWidgets.QHBoxLayout()
        cols.setSpacing(cfg.space_l)
        lay.addLayout(cols)

        self._left_col_host = QtWidgets.QWidget()
        self._right_col_host = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(self._left_col_host)
        right = QtWidgets.QVBoxLayout(self._right_col_host)
        left.setContentsMargins(0, 0, 0, 0)
        right.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(cfg.space_s)
        right.setSpacing(cfg.space_s)
        cols.addWidget(self._left_col_host, 1)
        cols.addWidget(self._right_col_host, 1)

        self.sp_min_height = self._new_spinbox(base_h, 0, 10000)
        self.sp_max_height = self._new_spinbox(base_h, 0, 10000)
        self.sp_retries = self._new_spinbox(base_h, 0, 50)
        self.sp_bandwidth = self._new_spinbox(base_h, 0, 10_000_000, step=100)
        self.sp_fragments = self._new_spinbox(base_h, 1, 64)
        self.sp_timeout = self._new_spinbox(base_h, 1, 600)
        self.cmb_browser_cookies_mode = self._new_combo(base_h)
        self._rebuild_browser_cookies_mode_combo()
        self.cmb_cookie_browser = self._new_combo(base_h)
        self._rebuild_cookie_browser_combo()
        self.ed_cookie_file_path = self._new_line_edit(base_h)
        self.btn_cookie_file_browse = QtWidgets.QPushButton(tr("common.browse"))
        setup_button(self.btn_cookie_file_browse, min_h=base_h)

        self._add_tracked_row(
            left,
            self._row(
                tr("settings.downloader.min_video_height"),
                self.sp_min_height,
                tr("settings.help.min_video_height"),
            ),
            ("downloader", "min_video_height"),
        )
        self._add_tracked_row(
            left,
            self._row(
                tr("settings.downloader.max_video_height"),
                self.sp_max_height,
                tr("settings.help.max_video_height"),
            ),
            ("downloader", "max_video_height"),
        )
        self._add_tracked_row(
            left,
            self._row(
                tr("settings.browser_cookies.mode.label"),
                self.cmb_browser_cookies_mode,
                tr("settings.help.browser_cookies_mode"),
            ),
            ("browser_cookies", "mode"),
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
            ),
            ("network", "http_timeout_s"),
        )
        self._cookie_browser_row = self._add_tracked_row(
            left,
            self._row(
                tr("settings.browser_cookies.browser.label"),
                self.cmb_cookie_browser,
                tr("settings.help.browser_cookies_browser"),
            ),
            ("browser_cookies", "browser"),
        )
        cookie_file_host = self._build_split_control_row(
            cfg,
            self.ed_cookie_file_path,
            self.btn_cookie_file_browse,
        )
        self._cookie_file_row = self._add_tracked_row(
            left,
            self._build_labeled_row(
                label=tr("settings.browser_cookies.file_path.label"),
                control=self.ed_cookie_file_path,
                control_host=cookie_file_host,
                tooltip=tr("settings.help.browser_cookies_file_path"),
                advanced=True,
            ),
            ("browser_cookies", "file_path"),
            value_widget=self.ed_cookie_file_path,
        )
        self._add_tracked_row(
            right,
            self._row(tr("settings.network.retries"), self.sp_retries, tr("settings.help.retries")),
            ("network", "retries"),
        )

        left.addStretch(1)
        right.addStretch(1)

        lay.addStretch(1)

        self._connect_spinbox_mark_dirty(
            self.sp_min_height,
            self.sp_max_height,
            self.sp_retries,
            self.sp_bandwidth,
            self.sp_fragments,
            self.sp_timeout,
        )
        self._connect_mark_dirty(self.cmb_browser_cookies_mode.currentIndexChanged)
        self._connect_mark_dirty(self.ed_cookie_file_path.textChanged)
        self.cmb_cookie_browser.currentIndexChanged.connect(self._on_cookie_browser_changed)
        self.cmb_browser_cookies_mode.currentIndexChanged.connect(self._on_browser_cookies_mode_changed)
        self.ed_cookie_file_path.textChanged.connect(self._on_cookie_file_path_changed)
        self.btn_cookie_file_browse.clicked.connect(self._browse_cookie_file)
        self._sync_browser_cookies_controls()

    def on_settings_loaded(self, snap: SettingsSnapshot) -> None:
        data = snapshot_to_dict(snap)
        self._data = data
        self._loaded_data = copy.deepcopy(data)

        self._blocking_updates = True
        try:
            self._populate_from_data()
            self._refresh_runtime_capabilities()
        finally:
            self._blocking_updates = False
        self._sync_effective_baseline()
        self._refresh_dirty_markers()
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    @staticmethod
    def _merge_effective_payload(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(base)
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = SettingsPanel._merge_effective_payload(
                    cast(dict[str, Any], out.get(key)),
                    cast(dict[str, Any], value),
                )
            else:
                out[key] = copy.deepcopy(value)
        return out

    def _sync_effective_baseline(self) -> None:
        payload = self._collect_payload()
        if isinstance(self._data, dict):
            self._data = self._merge_effective_payload(self._data, payload)
        if isinstance(self._loaded_data, dict):
            self._loaded_data = self._merge_effective_payload(self._loaded_data, payload)

    def on_saved(self, action: str, snap: SettingsSnapshot) -> None:
        data = snapshot_to_dict(snap)
        op = str(action or "save").strip().lower()
        need_restart = self._pending_restart_prompt if op == "save" else False
        if op == "restore_defaults" and self._restore_baseline_data is not None:
            need_restart = self._needs_restart_between(self._restore_baseline_data, data)

        self._data = data
        self._loaded_data = copy.deepcopy(data)
        self._blocking_updates = True
        try:
            self._populate_from_data()
            self._refresh_runtime_capabilities()
        finally:
            self._blocking_updates = False

        self._sync_effective_baseline()
        self._refresh_dirty_markers()
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

        self._pending_restart_prompt = False
        self._restore_baseline_data = None

        if need_restart:
            restart_now = dialogs.ask_restart_required(self)
            if restart_now:
                self._restart_application()
            return

        if op == "save":
            dialogs.show_info(
                self,
                title=tr("dialog.info.title"),
                message=tr("settings.msg.saved"),
                header=tr("dialog.info.header"),
            )

    def on_error(self, key: str, params: dict[str, Any]) -> None:
        dialogs.show_error(self, key, params or {})

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

    def _set_row_control_enabled(
        self,
        row: QtWidgets.QWidget | None,
        enabled: bool,
        *,
        control: QtWidgets.QWidget | None = None,
    ) -> None:
        if row is None:
            return

        target = control if isinstance(control, QtWidgets.QWidget) else self._row_control(row)
        if isinstance(target, QtWidgets.QWidget):
            target.setEnabled(bool(enabled))

        label = self._row_label(row)
        if isinstance(label, QtWidgets.QWidget):
            label.setEnabled(bool(enabled))

    def _find_setting_row(self, control: QtWidgets.QWidget | None) -> QtWidgets.QWidget | None:
        current = control
        while isinstance(current, QtWidgets.QWidget):
            mapped_control = self._row_control(current)
            if mapped_control is control:
                return current
            current = current.parentWidget()
        return None

    def _on_logging_toggle(self) -> None:
        enabled = bool(self.tg_log_enabled.is_first_checked())
        self._set_row_control_enabled(self._log_level_row, enabled)
        self._mark_dirty()

    def _on_bulk_add_warning_toggle(self) -> None:
        enabled = bool(self.tg_bulk_add_warning_enabled.is_first_checked())
        self._set_row_control_enabled(self._bulk_add_threshold_row, enabled, control=self.sp_bulk_add_threshold)
        self._mark_dirty()

    def _apply_advanced_visibility(self, show: bool) -> None:
        for w in self._advanced_rows:
            w.setVisible(bool(show))
        self._rebuild_transcription_profile_combo()
        self._rebuild_translation_profile_combo()
        self._rebuild_browser_cookies_mode_combo()
        self._sync_transcription_profile_controls()
        self._sync_translation_profile_controls()
        self._sync_browser_cookies_controls()


    def _rebuild_transcription_profile_combo(self, selected: str | None = None) -> None:
        current = RuntimeProfiles.normalize_transcription_profile(
            selected
            or self.cmb_transcription_profile.currentData()
            or RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE
        )
        allow_custom = (
            bool(self.chk_show_advanced.isChecked())
            or current == RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM
        )
        self.cmb_transcription_profile.blockSignals(True)
        try:
            self.cmb_transcription_profile.clear()
            self._add_combo_option(
                self.cmb_transcription_profile,
                "settings.profile.fast",
                RuntimeProfiles.TRANSCRIPTION_PROFILE_FAST,
                tooltip_key="settings.profile.fast_tip",
            )
            self._add_combo_option(
                self.cmb_transcription_profile,
                "settings.profile.balanced",
                RuntimeProfiles.TRANSCRIPTION_PROFILE_BALANCED,
                tooltip_key="settings.profile.balanced_tip",
            )
            self._add_combo_option(
                self.cmb_transcription_profile,
                "settings.profile.accurate",
                RuntimeProfiles.TRANSCRIPTION_PROFILE_ACCURATE,
                tooltip_key="settings.profile.accurate_tip",
            )
            self._add_combo_option(
                self.cmb_transcription_profile,
                "settings.profile.guarded",
                RuntimeProfiles.TRANSCRIPTION_PROFILE_GUARDED,
                tooltip_key="settings.profile.guarded_tip",
            )
            if allow_custom:
                self._add_combo_option(
                    self.cmb_transcription_profile,
                    "settings.profile.custom",
                    RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM,
                    tooltip_key="settings.profile.custom_tip",
                )
            set_combo_data(
                self.cmb_transcription_profile,
                current,
                fallback_data=RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE,
            )
        finally:
            self.cmb_transcription_profile.blockSignals(False)

    def _rebuild_translation_profile_combo(self, selected: str | None = None) -> None:
        current = RuntimeProfiles.normalize_translation_profile(
            selected
            or self.cmb_translation_profile.currentData()
            or RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE
        )
        allow_custom = (
            bool(self.chk_show_advanced.isChecked())
            or current == RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM
        )
        self.cmb_translation_profile.blockSignals(True)
        try:
            self.cmb_translation_profile.clear()
            self._add_combo_option(
                self.cmb_translation_profile,
                "settings.profile.fast",
                RuntimeProfiles.TRANSLATION_PROFILE_FAST,
                tooltip_key="settings.translation.profile.fast_tip",
            )
            self._add_combo_option(
                self.cmb_translation_profile,
                "settings.profile.balanced",
                RuntimeProfiles.TRANSLATION_PROFILE_BALANCED,
                tooltip_key="settings.translation.profile.balanced_tip",
            )
            self._add_combo_option(
                self.cmb_translation_profile,
                "settings.profile.accurate",
                RuntimeProfiles.TRANSLATION_PROFILE_ACCURATE,
                tooltip_key="settings.translation.profile.accurate_tip",
            )
            if allow_custom:
                self._add_combo_option(
                    self.cmb_translation_profile,
                    "settings.profile.custom",
                    RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM,
                    tooltip_key="settings.translation.profile.custom_tip",
                )
            set_combo_data(
                self.cmb_translation_profile,
                current,
                fallback_data=RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE,
            )
        finally:
            self.cmb_translation_profile.blockSignals(False)

    def _transcription_custom_cfg(self) -> dict[str, Any]:
        model = self._section_dict(self._data or {}, "model")
        t_model = self._section_dict(model, "transcription_model")
        return dict(self._section_dict(t_model, "advanced"))

    def _translation_custom_cfg(self) -> dict[str, Any]:
        model = self._section_dict(self._data or {}, "model")
        x_model = self._section_dict(model, "translation_model")
        return dict(self._section_dict(x_model, "advanced"))

    def _set_transcription_custom_cfg(self, cfg: dict[str, Any]) -> None:
        model = self._section_dict(self._data, "model")
        t_model = self._section_dict(model, "transcription_model")
        t_model["advanced"] = dict(cfg or {})
        model["transcription_model"] = t_model
        self._data["model"] = model

    def _set_translation_custom_cfg(self, cfg: dict[str, Any]) -> None:
        model = self._section_dict(self._data, "model")
        x_model = self._section_dict(model, "translation_model")
        x_model["advanced"] = dict(cfg or {})
        model["translation_model"] = x_model
        self._data["model"] = model

    def _capture_transcription_controls(self) -> dict[str, Any]:
        return {
            "context_policy": str(self.cmb_context_policy.currentData() or RuntimeProfiles.CONTEXT_POLICY_AUTO),
            "silence_guard": str(self.cmb_silence_guard.currentData() or RuntimeProfiles.SILENCE_GUARD_NORMAL),
            "language_stability": str(
                self.cmb_language_stability.currentData() or RuntimeProfiles.LANGUAGE_STABILITY_BALANCED
            ),
            "chunk_length_s": int(self.sp_chunk_length_s.value()),
            "stride_length_s": max(0, int(self.sp_stride_length_s.value())),
        }

    def _capture_translation_controls(self) -> dict[str, Any]:
        return {
            "style": str(self.cmb_tr_style.currentData() or RuntimeProfiles.TRANSLATION_STYLE_BALANCED),
            "num_beams": max(1, int(self.sp_tr_beams.value())),
            "no_repeat_ngram_size": max(0, int(self.sp_tr_no_repeat.value())),
        }

    def _apply_transcription_runtime_to_controls(self, runtime: dict[str, Any], *, editable: bool) -> None:
        set_combo_data(
            self.cmb_context_policy,
            runtime.get("context_policy"),
            fallback_data=RuntimeProfiles.CONTEXT_POLICY_AUTO,
        )
        set_combo_data(
            self.cmb_silence_guard,
            runtime.get("silence_guard"),
            fallback_data=RuntimeProfiles.SILENCE_GUARD_NORMAL,
        )
        set_combo_data(
            self.cmb_language_stability,
            runtime.get("language_stability"),
            fallback_data=RuntimeProfiles.LANGUAGE_STABILITY_BALANCED,
        )
        self.sp_chunk_length_s.setValue(int(runtime.get("chunk_length_s", 45) or 45))
        self.sp_stride_length_s.setValue(int(runtime.get("stride_length_s", 5) or 5))
        for widget in (
            self.cmb_context_policy,
            self.cmb_silence_guard,
            self.cmb_language_stability,
            self.sp_chunk_length_s,
            self.sp_stride_length_s,
        ):
            row = self._find_setting_row(widget)
            if row is not None:
                self._set_row_control_enabled(row, editable, control=widget)
            else:
                widget.setEnabled(bool(editable))

    def _apply_translation_runtime_to_controls(self, runtime: dict[str, Any], *, editable: bool) -> None:
        set_combo_data(
            self.cmb_tr_style,
            runtime.get("style"),
            fallback_data=RuntimeProfiles.TRANSLATION_STYLE_BALANCED,
        )
        self.sp_tr_beams.setValue(int(runtime.get("num_beams", 3) or 3))
        self.sp_tr_no_repeat.setValue(int(runtime.get("no_repeat_ngram_size", 0) or 0))
        for widget in (self.cmb_tr_style, self.sp_tr_beams, self.sp_tr_no_repeat):
            row = self._find_setting_row(widget)
            if row is not None:
                self._set_row_control_enabled(row, editable, control=widget)
            else:
                widget.setEnabled(bool(editable))

    def _sync_transcription_profile_controls(self) -> None:
        profile = RuntimeProfiles.normalize_transcription_profile(
            self.cmb_transcription_profile.currentData()
            or RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE
        )
        if profile == RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM:
            runtime = RuntimeProfiles.resolve_transcription_runtime(
                profile=profile,
                overrides=self._transcription_custom_cfg(),
            )
            self._apply_transcription_runtime_to_controls(runtime, editable=True)
        else:
            runtime = RuntimeProfiles.resolve_transcription_runtime(profile=profile)
            self._apply_transcription_runtime_to_controls(runtime, editable=False)

    def _sync_translation_profile_controls(self) -> None:
        profile = RuntimeProfiles.normalize_translation_profile(
            self.cmb_translation_profile.currentData()
            or RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE
        )
        if profile == RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM:
            runtime = RuntimeProfiles.resolve_translation_runtime(
                profile=profile,
                overrides=self._translation_custom_cfg(),
            )
            self._apply_translation_runtime_to_controls(runtime, editable=True)
        else:
            runtime = RuntimeProfiles.resolve_translation_runtime(profile=profile)
            self._apply_translation_runtime_to_controls(runtime, editable=False)

    def _on_transcription_profile_changed(self, *_args) -> None:
        profile = RuntimeProfiles.normalize_transcription_profile(
            self.cmb_transcription_profile.currentData()
            or RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE
        )
        if profile == RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM:
            self._set_transcription_custom_cfg(self._capture_transcription_controls())
        self._sync_transcription_profile_controls()
        self._mark_dirty()

    def _on_translation_profile_changed(self, *_args) -> None:
        profile = RuntimeProfiles.normalize_translation_profile(
            self.cmb_translation_profile.currentData()
            or RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE
        )
        if profile == RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM:
            self._set_translation_custom_cfg(self._capture_translation_controls())
        self._sync_translation_profile_controls()
        self._mark_dirty()

    def _on_transcription_advanced_changed(self, *_args) -> None:
        if (
            RuntimeProfiles.normalize_transcription_profile(self.cmb_transcription_profile.currentData())
            == RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM
        ):
            self._set_transcription_custom_cfg(self._capture_transcription_controls())

    def _on_translation_advanced_changed(self, *_args) -> None:
        if (
            RuntimeProfiles.normalize_translation_profile(self.cmb_translation_profile.currentData())
            == RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM
        ):
            self._set_translation_custom_cfg(self._capture_translation_controls())

    def _commit_advanced_payload(self, payload: dict[str, Any]) -> None:
        coord = self.coordinator()
        if coord is None:
            return
        coord.save_ui_state(payload)

    def _build_advanced_payload(self) -> dict[str, Any]:
        return {
            "app": {
                "ui": {
                    "show_advanced_settings": bool(self.chk_show_advanced.isChecked()),
                }
            }
        }

    def _on_undo_clicked(self) -> None:
        if not self._loaded_data:
            return
        self._blocking_updates = True
        try:
            self._data = copy.deepcopy(self._loaded_data)
            self._populate_from_data()
            self._refresh_runtime_capabilities()
        finally:
            self._blocking_updates = False
        self._sync_effective_baseline()
        self._refresh_dirty_markers()
        QtCore.QTimer.singleShot(0, self._sync_column_widths)

    def _on_restore_clicked(self) -> None:
        if not dialogs.ask_restore_defaults(self):
            return
        self._restore_baseline_data = copy.deepcopy(self._data or {})
        coord = self.coordinator()
        if coord is not None:
            coord.restore_defaults()

    def _on_save_clicked(self) -> None:
        if not dialogs.ask_save_settings(self):
            return
        self._restore_baseline_data = None
        payload = self._collect_payload()
        self._pending_restart_prompt = self._needs_restart(payload)
        coord = self.coordinator()
        if coord is not None:
            coord.save(payload)

    @staticmethod
    def _restart_application() -> None:
        os.execl(sys.executable, sys.executable, *sys.argv)

    @staticmethod
    def _open_logs_folder() -> None:
        path = AppConfig.PATHS.LOGS_DIR
        if isinstance(path, Path):
            open_local_path(path)

    def _on_device_changed(self) -> None:
        self._mark_dirty()
        self._refresh_runtime_capabilities()

    def _on_precision_changed(self) -> None:
        self._mark_dirty()
        self._refresh_runtime_capabilities()

    def _populate_from_data(self) -> None:
        d = self._data or {}

        app = self._section_dict(d, "app")
        eng = self._section_dict(d, "engine")
        model = self._section_dict(d, "model")
        transcription = self._section_dict(d, "transcription")
        translation = self._section_dict(d, "translation")
        downloader = self._section_dict(d, "downloader")
        browser_cookies = self._section_dict(d, "browser_cookies")
        network = self._section_dict(d, "network")

        self._populate_app_settings(app)
        self._populate_engine_settings(eng)
        self._populate_model_settings(model)
        self._populate_transcription_settings(transcription)
        self._populate_translation_settings(translation)
        self._populate_download_settings(downloader, browser_cookies, network)

        self._refresh_auto_option_labels()
        self._refresh_dirty_markers()

    def _populate_app_settings(self, app: dict[str, Any]) -> None:
        _populate_combo_fields(
            app,
            (
                ("language", self.cmb_app_language, LanguagePolicy.AUTO),
                ("theme", self.cmb_app_theme, LanguagePolicy.AUTO),
            ),
        )

        ui_cfg = self._section_dict(app, "ui")
        show_adv = bool(ui_cfg.get("show_advanced_settings", False))
        self.chk_show_advanced.blockSignals(True)
        self.chk_show_advanced.setChecked(show_adv)
        self.chk_show_advanced.blockSignals(False)
        self._apply_advanced_visibility(show_adv)

        bulk_cfg = self._section_dict(ui_cfg, "bulk_add_confirmation")
        _populate_toggle_fields(
            bulk_cfg,
            (("enabled", self.tg_bulk_add_warning_enabled, AppConfig.ui_bulk_add_confirmation_enabled()),),
        )
        _populate_spin_fields(
            bulk_cfg,
            (("threshold", self.sp_bulk_add_threshold, AppConfig.ui_bulk_add_confirmation_threshold()),),
        )
        self._on_bulk_add_warning_toggle()

        log_cfg = self._section_dict(app, "logging")
        _populate_toggle_fields(log_cfg, (("enabled", self.tg_log_enabled, True),))
        _populate_combo_fields(log_cfg, (("level", self.cmb_log_level, "warning"),))
        self._on_logging_toggle()

    def _populate_engine_settings(self, eng: dict[str, Any]) -> None:
        _populate_combo_fields(
            eng,
            (
                ("preferred_device", self.cmb_engine_device, LanguagePolicy.AUTO),
                ("precision", self.cmb_engine_precision, LanguagePolicy.AUTO),
            ),
        )
        self.tg_fp32_math_mode.set_first_checked(
            bool(str(eng.get("fp32_math_mode", "ieee") or "ieee").strip().lower() == "tf32")
        )
        _populate_toggle_fields(
            eng,
            (
                ("low_cpu_mem_usage", self.tg_low_cpu_mem, True),
            ),
        )

    def _populate_model_settings(self, model: dict[str, Any]) -> None:
        t_model = self._section_dict(model, "transcription_model")
        x_model = self._section_dict(model, "translation_model")

        self._populate_model_engines()

        trans_engine_name = EngineResolver.resolve_transcription_engine_name(model)
        if trans_engine_name == AppConfig.MISSING_VALUE:
            trans_engine_name = str(t_model.get("engine_name", "none"))
        set_combo_data(self.cmb_trans_engine, trans_engine_name, fallback_data="none")
        _populate_combo_fields(
            t_model,
            (("profile", self.cmb_transcription_profile, RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE),),
        )
        self._rebuild_transcription_profile_combo(
            str(
                self.cmb_transcription_profile.currentData()
                or RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE
            )
        )
        self._sync_transcription_profile_controls()
        _populate_toggle_fields(
            t_model,
            (("ignore_warning", self.tg_ignore_warning, False),),
        )

        tr_engine_name = EngineResolver.resolve_translation_engine_name(model)
        if tr_engine_name == AppConfig.MISSING_VALUE:
            tr_engine_name = str(x_model.get("engine_name", "none"))
        set_combo_data(self.cmb_tr_engine, tr_engine_name, fallback_data="none")
        _populate_combo_fields(
            x_model,
            (("profile", self.cmb_translation_profile, RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE),),
        )
        self._rebuild_translation_profile_combo(
            str(
                self.cmb_translation_profile.currentData()
                or RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE
            )
        )
        self._sync_translation_profile_controls()
        _populate_spin_fields(
            x_model,
            (
                ("max_new_tokens", self.sp_tr_max_tokens, 256),
                ("chunk_max_chars", self.sp_tr_chunk_chars, 1200),
            ),
        )

    def _populate_transcription_settings(self, transcription: dict[str, Any]) -> None:
        _populate_combo_fields(
            transcription,
            (("default_source_language", self.cmb_default_language, LanguagePolicy.AUTO),),
        )

    def _populate_translation_settings(self, translation: dict[str, Any]) -> None:
        _populate_combo_fields(
            translation,
            (("default_target_language", self.cmb_default_target_language, LanguagePolicy.DEFAULT_UI),),
        )

    def _populate_download_settings(
        self,
        downloader: dict[str, Any],
        browser_cookies: dict[str, Any],
        network: dict[str, Any],
    ) -> None:
        _populate_spin_fields(
            downloader,
            (
                ("min_video_height", self.sp_min_height, AppConfig.downloader_min_video_height()),
                ("max_video_height", self.sp_max_height, AppConfig.downloader_max_video_height()),
            ),
        )

        _populate_spin_fields(
            network,
            (
                ("retries", self.sp_retries, AppConfig.network_retries()),
                ("concurrent_fragments", self.sp_fragments, AppConfig.network_concurrent_fragments()),
                ("http_timeout_s", self.sp_timeout, AppConfig.network_http_timeout_s()),
            ),
        )
        bw = network.get("max_bandwidth_kbps", AppConfig.network_max_bandwidth_kbps())
        self.sp_bandwidth.setValue(int(bw or 0))
        selected_mode = str(browser_cookies.get("mode", AppConfig.browser_cookies_mode()) or "none").strip().lower()
        selected_browser = str(
            browser_cookies.get("browser", AppConfig.browser_cookie_browser_policy()) or LanguagePolicy.AUTO
        ).strip().lower()
        self._rebuild_browser_cookies_mode_combo(selected_mode)
        self._rebuild_cookie_browser_combo(selected_browser)
        _populate_combo_fields(
            browser_cookies,
            (
                ("mode", self.cmb_browser_cookies_mode, AppConfig.browser_cookies_mode()),
                ("browser", self.cmb_cookie_browser, AppConfig.browser_cookie_browser_policy()),
            ),
        )
        self.ed_cookie_file_path.setText(str(browser_cookies.get("file_path") or AppConfig.browser_cookie_file_path()))
        self._sync_browser_cookies_controls()

    def _collect_payload(self) -> dict[str, Any]:
        return {
            "app": self._collect_app_payload(),
            "engine": self._collect_engine_payload(),
            "model": self._collect_model_payload(),
            "transcription": self._collect_transcription_payload(),
            "translation": self._collect_translation_payload(),
            "downloader": self._collect_downloader_payload(),
            "browser_cookies": self._collect_browser_cookies_payload(),
            "network": self._collect_network_payload(),
        }

    def _collect_app_payload(self) -> dict[str, Any]:
        payload = _collect_combo_fields(
            (
                ("language", self.cmb_app_language, LanguagePolicy.AUTO),
                ("theme", self.cmb_app_theme, LanguagePolicy.AUTO),
            ),
        )
        payload.update({
            "ui": {
                "show_advanced_settings": bool(self.chk_show_advanced.isChecked()),
                "bulk_add_confirmation": {
                    "enabled": bool(self.tg_bulk_add_warning_enabled.is_first_checked()),
                    "threshold": int(self.sp_bulk_add_threshold.value()),
                },
            },
        })
        payload["logging"] = {
            **_collect_toggle_fields((("enabled", self.tg_log_enabled),)),
            **_collect_combo_fields((("level", self.cmb_log_level, "warning"),)),
        }
        return payload

    def _collect_engine_payload(self) -> dict[str, Any]:
        payload = _collect_combo_fields(
            (
                ("preferred_device", self.cmb_engine_device, LanguagePolicy.AUTO),
                ("precision", self.cmb_engine_precision, LanguagePolicy.AUTO),
            ),
        )
        payload["fp32_math_mode"] = "tf32" if self.tg_fp32_math_mode.is_first_checked() else "ieee"
        payload.update(
            _collect_toggle_fields(
                (
                    ("low_cpu_mem_usage", self.tg_low_cpu_mem),
                ),
            )
        )
        return payload

    def _collect_model_payload(self) -> dict[str, Any]:
        transcription_profile = RuntimeProfiles.normalize_transcription_profile(
            self.cmb_transcription_profile.currentData() or RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE
        )
        transcription_advanced = (
            self._capture_transcription_controls()
            if transcription_profile == RuntimeProfiles.TRANSCRIPTION_PROFILE_CUSTOM
            else self._transcription_custom_cfg()
        )
        translation_profile = RuntimeProfiles.normalize_translation_profile(
            self.cmb_translation_profile.currentData() or RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE
        )
        translation_advanced = (
            self._capture_translation_controls()
            if translation_profile == RuntimeProfiles.TRANSLATION_PROFILE_CUSTOM
            else self._translation_custom_cfg()
        )
        transcription_model = {
            **_collect_combo_fields(
                (
                    ("engine_name", self.cmb_trans_engine, "none"),
                    ("profile", self.cmb_transcription_profile, RuntimeProfiles.TRANSCRIPTION_DEFAULT_PROFILE),
                ),
            ),
            **_collect_toggle_fields(
                (("ignore_warning", self.tg_ignore_warning),),
            ),
            "advanced": transcription_advanced,
        }
        return {
            "transcription_model": transcription_model,
            "translation_model": {
                **_collect_combo_fields(
                    (
                        ("engine_name", self.cmb_tr_engine, "none"),
                        ("profile", self.cmb_translation_profile, RuntimeProfiles.TRANSLATION_DEFAULT_PROFILE),
                    ),
                ),
                **cast(
                    dict[str, Any],
                    _collect_spin_fields(
                        (
                            ("max_new_tokens", self.sp_tr_max_tokens),
                            ("chunk_max_chars", self.sp_tr_chunk_chars),
                        ),
                    ),
                ),
                "advanced": translation_advanced,
            },
        }

    def _collect_transcription_payload(self) -> dict[str, Any]:
        code = str(self.cmb_default_language.currentData() or LanguagePolicy.AUTO).strip().lower()
        return {
            "default_source_language": LanguagePolicy.normalize_default_source_language_policy(code),
        }

    def _collect_translation_payload(self) -> dict[str, Any]:
        code = str(self.cmb_default_target_language.currentData() or LanguagePolicy.DEFAULT_UI).strip().lower()
        return {
            "default_target_language": LanguagePolicy.normalize_default_target_language_policy(code),
        }

    def _collect_downloader_payload(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _collect_spin_fields(
                (
                    ("min_video_height", self.sp_min_height),
                    ("max_video_height", self.sp_max_height),
                ),
            ),
        )

    def _collect_browser_cookies_payload(self) -> dict[str, Any]:
        payload = _collect_combo_fields(
            (
                ("mode", self.cmb_browser_cookies_mode, AppConfig.browser_cookies_mode()),
                ("browser", self.cmb_cookie_browser, AppConfig.browser_cookie_browser_policy()),
            ),
        )
        payload["file_path"] = str(self.ed_cookie_file_path.text() or "").strip()
        return payload

    def _collect_network_payload(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _collect_spin_fields(
                (
                    ("retries", self.sp_retries),
                    ("max_bandwidth_kbps", self.sp_bandwidth),
                    ("concurrent_fragments", self.sp_fragments),
                    ("http_timeout_s", self.sp_timeout),
                ),
                none_if_non_positive={"max_bandwidth_kbps"},
            ),
        )

    def _needs_restart(self, payload: dict[str, Any]) -> bool:
        return self._needs_restart_between(self._data or {}, payload)

    def _needs_restart_between(self, current: dict[str, Any], updated: dict[str, Any]) -> bool:
        for path in self._RESTART_SENSITIVE_KEYS:
            if self._get_nested(current, path) != self._get_nested(updated, path):
                return True
        return False

    def _sync_browser_cookies_controls(self) -> None:
        mode = str(self.cmb_browser_cookies_mode.currentData() or "none").strip().lower()
        browser_enabled = bool(mode == "from_browser")
        file_enabled = bool(mode == "from_file")
        if self._cookie_browser_row is not None:
            self._set_row_control_enabled(self._cookie_browser_row, browser_enabled, control=self.cmb_cookie_browser)
        if self._cookie_file_row is not None:
            self._set_row_control_enabled(self._cookie_file_row, file_enabled, control=self.ed_cookie_file_path)
        self.btn_cookie_file_browse.setEnabled(file_enabled)

    def _on_browser_cookies_mode_changed(self) -> None:
        self._sync_browser_cookies_controls()
        self._mark_dirty()

    def _on_cookie_browser_changed(self) -> None:
        self._sync_browser_cookies_controls()
        self._mark_dirty()

    def _on_cookie_file_path_changed(self) -> None:
        self._sync_browser_cookies_controls()
        self._mark_dirty()

    def _browse_cookie_file(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            tr("settings.browser_cookies.file_path.dialog_title"),
            str(Path(self.ed_cookie_file_path.text()).parent) if self.ed_cookie_file_path.text().strip() else "",
            tr("settings.browser_cookies.file_path.dialog_filter"),
        )
        selected_path = str(file_path or "").strip()
        if not selected_path:
            return
        self.ed_cookie_file_path.setText(selected_path)

    def _rebuild_cookie_browser_combo(self, selected_browser: str | None = None) -> None:
        current_browser = str(
            selected_browser or self.cmb_cookie_browser.currentData() or LanguagePolicy.AUTO
        ).strip().lower() or LanguagePolicy.AUTO
        detected_browsers = {str(browser or "").strip().lower() for browser in available_cookie_browsers()}
        browsers: list[str] = []
        for browser in DownloadPolicy.COOKIE_BROWSERS:
            if browser in detected_browsers:
                browsers.append(browser)
                continue
            if browser == current_browser and DownloadPolicy.is_supported_cookie_browser(current_browser):
                browsers.append(browser)

        self.cmb_cookie_browser.blockSignals(True)
        try:
            self.cmb_cookie_browser.clear()
            self._add_combo_option(self.cmb_cookie_browser, "common.auto", LanguagePolicy.AUTO)
            for browser in browsers:
                self._add_combo_option(
                    self.cmb_cookie_browser,
                    f"settings.browser_cookies.browser.{browser}",
                    browser,
                )
            set_combo_data(self.cmb_cookie_browser, current_browser, fallback_data=LanguagePolicy.AUTO)
        finally:
            self.cmb_cookie_browser.blockSignals(False)

    @staticmethod
    def _get_nested(d: dict[str, Any], path: tuple[str, ...]) -> Any:
        cur: Any = d
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    @staticmethod
    def _short_label(text: str) -> str:
        s = str(text or "").strip()
        if "(" in s:
            s = s.split("(", 1)[0].strip()
        return s or str(text or "").strip()

    def _refresh_auto_option_labels(self) -> None:
        sys_hint = QtCore.QLocale.system().name().split("_", 1)[0].lower()
        available: dict[str, str] = {}
        for i in range(self.cmb_app_language.count()):
            code = str(self.cmb_app_language.itemData(i) or "").strip().lower()
            if code and code != LanguagePolicy.AUTO:
                available[code] = self.cmb_app_language.itemText(i)
        resolved_lang = available.get(sys_hint) or available.get("en") or next(iter(available.values()), sys_hint or "")
        idx_auto = self.cmb_app_language.findData(LanguagePolicy.AUTO)
        if idx_auto >= 0:
            self.cmb_app_language.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_lang})')

        app_obj = QtWidgets.QApplication.instance()
        app = app_obj if isinstance(app_obj, QtWidgets.QApplication) else None
        theme = system_theme_key(app)
        resolved_theme = tr("settings.app.theme.dark") if theme == "dark" else tr("settings.app.theme.light")
        idx_auto = self.cmb_app_theme.findData(LanguagePolicy.AUTO)
        if idx_auto >= 0:
            self.cmb_app_theme.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_theme})')

        auto_dev = AppConfig.auto_device_key()
        resolved_dev = tr("settings.engine.device.gpu") if auto_dev == "cuda" else tr("settings.engine.device.cpu")
        idx_auto = self.cmb_engine_device.findData(LanguagePolicy.AUTO)
        if idx_auto >= 0:
            self.cmb_engine_device.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_dev})')

        resolved_browser = resolve_effective_cookie_browser(DownloadPolicy.COOKIE_BROWSER_AUTO)
        resolved_browser_label = ""
        if resolved_browser:
            resolved_browser_key = f"settings.browser_cookies.browser.{resolved_browser}"
            resolved_browser_label = tr(resolved_browser_key)
            if resolved_browser_label == resolved_browser_key:
                resolved_browser_label = str(resolved_browser or "").strip().title()
        auto_label = tr("common.auto")
        if auto_label == "common.auto":
            auto_label = "Auto"
        idx_auto = self.cmb_cookie_browser.findData(LanguagePolicy.AUTO)
        if idx_auto >= 0:
            label = auto_label
            if resolved_browser_label:
                label = f"{auto_label} ({resolved_browser_label})"
            self.cmb_cookie_browser.setItemText(idx_auto, label)

        try:
            auto_precision = AppConfig.auto_precision_key()
            if auto_precision == "bfloat16":
                resolved_precision_text = tr("settings.engine.precision.bfloat16")
            elif auto_precision == "float16":
                resolved_precision_text = tr("settings.engine.precision.float16")
            else:
                resolved_precision_text = tr("settings.engine.precision.float32")
            resolved_precision = self._short_label(resolved_precision_text)
            idx_auto = self.cmb_engine_precision.findData(LanguagePolicy.AUTO)
            if idx_auto >= 0:
                self.cmb_engine_precision.setItemText(idx_auto, f'{tr("common.auto")} ({resolved_precision})')
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

    def _populate_model_engines(self) -> None:
        trans_names = EngineResolver.local_model_names_for_task("transcription")
        tr_names = EngineResolver.local_model_names_for_task("translation")

        self.cmb_trans_engine.blockSignals(True)
        try:
            current = str(self.cmb_trans_engine.currentData() or "none")
            self.cmb_trans_engine.clear()
            self.cmb_trans_engine.addItem(tr("settings.translation.engine.disabled"), "none")

            for name in trans_names:
                self.cmb_trans_engine.addItem(name, name)

            set_combo_data(self.cmb_trans_engine, current, fallback_data="none")
        finally:
            self.cmb_trans_engine.blockSignals(False)

        self.cmb_tr_engine.blockSignals(True)
        try:
            current_tr = str(self.cmb_tr_engine.currentData() or "none")
            self.cmb_tr_engine.clear()
            self.cmb_tr_engine.addItem(tr("settings.translation.engine.disabled"), "none")

            for name in tr_names:
                self.cmb_tr_engine.addItem(name, name)

            set_combo_data(self.cmb_tr_engine, current_tr, fallback_data="none")
        finally:
            self.cmb_tr_engine.blockSignals(False)

    def _refresh_runtime_capabilities(self) -> None:
        caps = AppConfig.runtime_capabilities()
        has_cuda = bool(caps.get("has_cuda", False))
        bf16_supported = bool(caps.get("bf16_supported", False))

        idx_cuda = self.cmb_engine_device.findData("cuda")
        if idx_cuda >= 0:
            model = self.cmb_engine_device.model()
            if isinstance(model, QtGui.QStandardItemModel):
                item = model.item(idx_cuda)
                if item is not None:
                    item.setEnabled(has_cuda)

        if not has_cuda and str(self.cmb_engine_device.currentData() or LanguagePolicy.AUTO) == "cuda":
            set_combo_data(self.cmb_engine_device, LanguagePolicy.AUTO, fallback_data=LanguagePolicy.AUTO)

        prec_model = self.cmb_engine_precision.model()
        if isinstance(prec_model, QtGui.QStandardItemModel):
            idx_f16 = self.cmb_engine_precision.findData("float16")
            if idx_f16 >= 0:
                item = prec_model.item(idx_f16)
                if item is not None:
                    item.setEnabled(has_cuda)

            idx_bf16 = self.cmb_engine_precision.findData("bfloat16")
            if idx_bf16 >= 0:
                item = prec_model.item(idx_bf16)
                if item is not None:
                    item.setEnabled(has_cuda and bf16_supported)

        cur_prec = str(self.cmb_engine_precision.currentData() or LanguagePolicy.AUTO)
        if cur_prec == "float16" and not has_cuda:
            set_combo_data(self.cmb_engine_precision, LanguagePolicy.AUTO, fallback_data=LanguagePolicy.AUTO)
        if cur_prec == "bfloat16" and not (has_cuda and bf16_supported):
            set_combo_data(self.cmb_engine_precision, LanguagePolicy.AUTO, fallback_data=LanguagePolicy.AUTO)

        cur_dev = str(self.cmb_engine_device.currentData() or LanguagePolicy.AUTO)
        fp32_mode_allowed = AppConfig.is_fp32_math_mode_applicable(cur_dev, cur_prec)
        self._set_row_control_enabled(
            self._row_fp32_math_mode,
            fp32_mode_allowed,
            control=self.tg_fp32_math_mode,
        )

        self._refresh_auto_option_labels()
