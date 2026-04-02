# app/view/panels/live_panel.py
from __future__ import annotations

import logging
from typing import Any, cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.controller.panel_protocols import LiveCoordinatorProtocol
from app.model.core.config.config import AppConfig
from app.model.core.config.policy import LanguagePolicy
from app.model.core.config.profiles import RuntimeProfiles
from app.model.core.domain.entities import SettingsSnapshot
from app.model.core.domain.state import AppRuntimeState
from app.model.core.runtime.localization import current_language, language_display_name, tr
from app.model.engines.resolution import EngineCatalog
from app.model.engines.types import EngineRuntimeState
from app.model.settings.resolution import (
    build_live_quick_options_payload,
    compute_translation_runtime,
    translation_runtime_available,
)
from app.model.transcription.policy import TranscriptionOutputPolicy
from app.view import dialogs
from app.view.components.audio_spectrum import AudioSpectrumWidget
from app.view.components.choice_toggle import ChoiceToggle
from app.view.components.popup_combo import LanguageCombo, PopupComboBox, combo_current_code, rebuild_code_combo
from app.view.components.section_group import SectionGroup
from app.view.support.language_options import (
    build_source_language_items,
    build_target_language_items,
    effective_source_language_code,
    resolve_source_language_selection,
    resolve_target_language_selection,
    supported_source_language_codes,
    supported_target_language_codes,
)
from app.view.support.options_autosave import OptionsAutosave
from app.view.support.status_presenter import RuntimePresentation, build_runtime_presentation
from app.view.support.widget_effects import enable_styled_background
from app.view.support.widget_setup import (
    build_field_stack,
    build_layout_host,
    set_passive_cursor,
    setup_button,
    setup_combo,
    setup_layout,
    setup_text_editor,
)
from app.view.ui_config import ui

_LOG = logging.getLogger(__name__)


class LivePanel(QtWidgets.QWidget):
    """Live tab: capture audio input and run streaming ASR/translation."""

    STATE_STOPPED = "stopped"
    STATE_LISTENING = "listening"
    STATE_PAUSED = "paused"

    OUTPUT_MODE_STREAM = RuntimeProfiles.LIVE_OUTPUT_MODE_STREAM
    OUTPUT_MODE_CUMULATIVE = RuntimeProfiles.LIVE_OUTPUT_MODE_CUMULATIVE

    _transcription_ready: bool
    _transcription_error_key: str | None
    _transcription_error_params: dict[str, Any]
    _translation_ready: bool
    _translation_error_key: str | None
    _translation_error_params: dict[str, Any]
    _first_shown: bool
    _saved_device_name: str
    _saved_profile: str
    _saved_mode: str
    _saved_output_mode: str

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LivePanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)
        set_passive_cursor(self)
        self._panel_coordinator: LiveCoordinatorProtocol | None = None
        self._first_shown = False

        self._init_state()
        self._build_ui()
        self._wire_signals()
        self._restore_initial_state()

    def bind_coordinator(self, coordinator: LiveCoordinatorProtocol) -> None:
        self._panel_coordinator = coordinator
        self._refresh_devices(show_dialog=False)

    def coordinator(self) -> LiveCoordinatorProtocol | None:
        return self._panel_coordinator

    def has_audio_devices(self) -> bool:
        """Return whether the panel currently sees any available input device."""
        return bool(self._has_audio_devices)

    def on_runtime_state_changed(self, state: AppRuntimeState) -> None:
        self._transcription_ready = bool(state.transcription.ready)
        self._transcription_error_key = str(state.transcription.error_key or "").strip() or None
        self._transcription_error_params = dict(state.transcription.error_params or {})
        self._translation_ready = bool(state.translation.ready)
        self._translation_error_key = str(state.translation.error_key or "").strip() or None
        self._translation_error_params = dict(state.translation.error_params or {})
        self._sync_options_ui()

    def _coordinator_is_running(self) -> bool:
        coord = self.coordinator()
        return bool(coord is not None and coord.is_running())

    def _coordinator_is_options_save_running(self) -> bool:
        coord = self.coordinator()
        return bool(coord is not None and coord.is_options_save_running())

    def _quick_options_save_is_busy(self) -> bool:
        return self._coordinator_is_running() or self._coordinator_is_options_save_running()

    def _init_state(self) -> None:
        self._status_key = ""
        self._status_text = ""
        self._base_status_key = ""
        self._runtime_status_presentation: RuntimePresentation | None = None
        self._last_availability_debug_key: tuple | None = None

        self._transcription_ready: bool = False
        self._translation_ready: bool = False
        self._transcription_error_key: str | None = None
        self._transcription_error_params: dict[str, Any] = {}
        self._translation_error_key: str | None = None
        self._translation_error_params: dict[str, Any] = {}

        self._state: str = self.STATE_STOPPED
        self._has_audio_devices: bool = False
        self._first_shown = False
        self._warned_no_devices_for_tab: bool = False

        self._display_source: str = ""
        self._display_target: str = ""
        self._archive_source: str = ""
        self._archive_target: str = ""
        self._session_output_mode: str = self.OUTPUT_MODE_CUMULATIVE
        self._applied_source: str = ""
        self._applied_target: str = ""
        self._detected_language_code: str = ""

        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(int(self._ui.live_render_interval_ms))
        self._render_timer.timeout.connect(self._apply_render_output)

        self._opt_autosave = OptionsAutosave(
            self,
            build_payload=self._build_quick_options_payload,
            commit=self._commit_quick_options_payload,
            is_busy=self._quick_options_save_is_busy,
            interval_ms=1200,
            pending_delay_ms=300,
        )
        self._opt_autosave.set_blocked(True)
        self._saved_device_name = AppConfig.live_ui_device_name()
        self._saved_profile = AppConfig.live_ui_profile()
        self._saved_mode = AppConfig.live_ui_mode()
        self._saved_output_mode = AppConfig.live_ui_output_mode()

        self._session_source_language = LanguagePolicy.PREFERRED
        self._session_target_language = LanguagePolicy.PREFERRED

    def _build_ui(self) -> None:
        cfg = self._ui
        root = QtWidgets.QVBoxLayout(self)
        setup_layout(root, cfg=cfg, margins=(0, 0, 0, 0), spacing=cfg.spacing)

        base_h = cfg.control_min_h
        self._build_settings_section(root, base_h)
        self._build_controls_section(root, base_h)
        self._build_output_section(root)

    def _build_settings_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        settings_box = SectionGroup(self, object_name="LiveSettingsGroup", role="panelGroup", layout="grid")
        s_lay = cast(QtWidgets.QGridLayout, settings_box.root)
        setup_layout(
            s_lay,
            cfg=cfg,
            margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin),
            spacing=cfg.spacing,
            hspacing=cfg.space_l,
            vspacing=cfg.space_s,
            column_stretches={0: 1, 1: 1},
        )
        root.addWidget(settings_box)

        self.cmb_device = PopupComboBox()
        self.cmb_device.setEditable(False)
        setup_combo(self.cmb_device, min_h=base_h)

        self.btn_refresh_devices = QtWidgets.QPushButton(tr("live.controls.refresh"))
        self.btn_refresh_devices.setToolTip(tr("live.controls.refresh"))
        setup_button(self.btn_refresh_devices, min_h=base_h, min_w=cfg.control_min_w)

        dev_row, dev_row_lay = build_layout_host(
            parent=self,
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
        )
        dev_row_lay.addWidget(self.cmb_device, 3)
        dev_row_lay.addWidget(self.btn_refresh_devices, 1)
        device_host, _ = build_field_stack(self, tr("live.device.label"), dev_row, buddy=self.cmb_device)

        self.tg_output_mode = ChoiceToggle(
            first_text=tr("live.output_mode.stream"),
            second_text=tr("live.output_mode.cumulative"),
            height=base_h,
        )
        self.tg_output_mode.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        output_mode_host, _ = build_field_stack(
            self,
            tr("live.output_mode.label"),
            self.tg_output_mode,
            buddy=self.tg_output_mode,
        )

        self.tg_mode = ChoiceToggle(
            first_text=tr("files.options.mode.transcribe"),
            second_text=tr("files.options.mode.transcribe_translate"),
            height=base_h,
        )
        self.tg_mode.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        mode_host, _ = build_field_stack(self, tr("common.field.mode"), self.tg_mode, buddy=self.tg_mode)

        self.cmb_profile = PopupComboBox()
        setup_combo(self.cmb_profile, min_h=base_h)
        profile_options = (
            (
                RuntimeProfiles.LIVE_PROFILE_LOW_LATENCY,
                tr("live.profile.low_latency"),
                tr("live.profile.help.low_latency"),
            ),
            (RuntimeProfiles.LIVE_PROFILE_BALANCED, tr("live.profile.balanced"), tr("live.profile.help.balanced")),
            (
                RuntimeProfiles.LIVE_PROFILE_HIGH_CONTEXT,
                tr("live.profile.high_context"),
                tr("live.profile.help.high_context"),
            ),
        )
        for value, label, tooltip in profile_options:
            self.cmb_profile.addItem(label, value)
            idx = self.cmb_profile.count() - 1
            self.cmb_profile.setItemData(idx, tooltip, QtCore.Qt.ItemDataRole.ToolTipRole)
        profile_host, _ = build_field_stack(self, tr("live.profile.label"), self.cmb_profile, buddy=self.cmb_profile)


        self.cmb_source_language = LanguageCombo(codes_provider=supported_source_language_codes)
        self.cmb_source_language.setMinimumHeight(base_h)

        self.cmb_target_language = LanguageCombo(codes_provider=supported_target_language_codes)
        self.cmb_target_language.setMinimumHeight(base_h)

        src_lang_host, _ = build_field_stack(
            self,
            tr("common.field.source_language"),
            self.cmb_source_language,
            buddy=self.cmb_source_language,
        )
        tgt_lang_host, _ = build_field_stack(
            self,
            tr("common.field.target_language"),
            self.cmb_target_language,
            buddy=self.cmb_target_language,
        )

        s_lay.addWidget(device_host, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(output_mode_host, 0, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(mode_host, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(profile_host, 1, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(src_lang_host, 2, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(tgt_lang_host, 2, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)

    def _build_controls_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        controls_box = SectionGroup(self, object_name="LiveControlsGroup", role="panelGroup", layout="hbox")
        c_lay = cast(QtWidgets.QHBoxLayout, controls_box.root)
        setup_layout(c_lay, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.spacing)
        root.addWidget(controls_box)

        controls_left, left_lay = build_layout_host(
            parent=self,
            layout="vbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
        )
        btn_big_row, big_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)

        self.btn_start = QtWidgets.QPushButton(tr("live.controls.start"))
        self.btn_pause = QtWidgets.QPushButton(tr("live.controls.pause"))
        self.btn_stop = QtWidgets.QPushButton(tr("live.controls.stop"))
        for button in (self.btn_start, self.btn_pause, self.btn_stop):
            setup_button(button, min_h=cfg.button_big_h, min_w=cfg.control_min_w)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            big_lay.addWidget(button)
        left_lay.addWidget(btn_big_row)

        save_clear_outer, out_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=0)
        save_clear_inner, in_lay = build_layout_host(
            parent=self,
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
        )

        self.btn_save = QtWidgets.QPushButton(tr("live.controls.save_transcript"))
        self.btn_clear = QtWidgets.QPushButton(tr("live.controls.clear"))
        for button in (self.btn_save, self.btn_clear):
            setup_button(button, min_h=base_h, min_w=cfg.control_min_w)
            in_lay.addWidget(button)

        out_lay.addStretch(1)
        out_lay.addWidget(save_clear_inner, 4)
        out_lay.addStretch(1)
        left_lay.addWidget(save_clear_outer)

        self.lbl_status_title = QtWidgets.QLabel(tr("live.status_label"))
        self.lbl_status_title.setProperty("role", "fieldLabel")
        self.lbl_status_value = QtWidgets.QLabel(tr("status.idle"))
        self.lbl_status_value.setWordWrap(True)

        self.lbl_detected_title = QtWidgets.QLabel(tr("live.detected_language_label"))
        self.lbl_detected_title.setProperty("role", "fieldLabel")
        self.lbl_detected_value = QtWidgets.QLabel(tr("common.na"))
        self.lbl_detected_value.setWordWrap(True)

        status_row, status_row_lay = build_layout_host(
            parent=self,
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.space_s,
        )
        status_row_lay.addWidget(self.lbl_status_title, 0)
        status_row_lay.addWidget(self.lbl_status_value, 1)

        detected_row, detected_row_lay = build_layout_host(
            parent=self,
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.space_s,
        )
        detected_row_lay.addWidget(self.lbl_detected_title, 0)
        detected_row_lay.addWidget(self.lbl_detected_value, 1)

        info_row, info_row_lay = build_layout_host(
            parent=self,
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
        )
        info_row_lay.addWidget(status_row, 1)
        info_row_lay.addWidget(detected_row, 1)

        self.spectrum = AudioSpectrumWidget()
        meter_host, meter_lay = build_layout_host(parent=self, layout="vbox", margins=(0, 0, 0, 0), spacing=0)
        meter_host.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        meter_lay.addWidget(info_row, 0)
        meter_lay.addSpacing(cfg.space_s)
        meter_lay.addWidget(self.spectrum, 1)

        c_lay.addWidget(controls_left, 1)
        c_lay.addWidget(meter_host, 1)

    def _build_output_section(self, root: QtWidgets.QVBoxLayout) -> None:
        cfg = self._ui
        self.txt_source = QtWidgets.QTextEdit()
        setup_text_editor(self.txt_source, placeholder=tr("live.placeholder.source"))
        self.txt_source.setReadOnly(True)

        self.txt_target = QtWidgets.QTextEdit()
        setup_text_editor(self.txt_target, placeholder=tr("live.placeholder.target"))
        self.txt_target.setReadOnly(True)

        out_text_host, out_text_lay = build_layout_host(
            parent=self,
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.spacing,
        )
        self.source_text_host, src_text_lay = build_layout_host(
            parent=self,
            layout="vbox",
            margins=(0, 0, 0, 0),
            spacing=0,
        )
        self.target_text_host, tgt_text_lay = build_layout_host(
            parent=self,
            layout="vbox",
            margins=(0, 0, 0, 0),
            spacing=0,
        )

        src_text_lay.addWidget(self.txt_source, 1)
        tgt_text_lay.addWidget(self.txt_target, 1)
        out_text_lay.addWidget(self.source_text_host, 1)
        out_text_lay.addWidget(self.target_text_host, 1)
        root.addWidget(out_text_host, 1)

    def _wire_signals(self) -> None:
        self.btn_refresh_devices.clicked.connect(self._refresh_devices_clicked)
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_save.clicked.connect(self._save_transcript)
        self.btn_clear.clicked.connect(self._clear_text)

        self.cmb_device.currentIndexChanged.connect(self._on_device_changed)
        self.tg_output_mode.changed.connect(self._on_quick_option_changed)
        self.tg_mode.changed.connect(self._on_quick_option_changed)
        self.cmb_profile.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_source_language.currentIndexChanged.connect(self._on_source_language_changed)
        self.cmb_target_language.currentIndexChanged.connect(self._on_target_language_changed)

    def _restore_initial_state(self) -> None:
        self._apply_saved_options_to_ui()
        self._refresh_devices(show_dialog=False)
        self._sync_options_ui()
        self._apply_render_output(force=True)
        self._opt_autosave.set_blocked(False)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._first_shown:
            self._first_shown = True
            self._refresh_devices(show_dialog=True)

    def _apply_saved_options_to_ui(self) -> None:
        self._refresh_language_combos()

        want_profile = RuntimeProfiles.normalize_live_profile(self._saved_profile or AppConfig.live_ui_profile())
        if want_profile not in RuntimeProfiles.LIVE_PROFILE_IDS:
            want_profile = RuntimeProfiles.normalize_live_profile(None)
        for i in range(self.cmb_profile.count()):
            if str(self.cmb_profile.itemData(i) or "").strip().lower() == want_profile:
                self.cmb_profile.setCurrentIndex(i)
                break

        want_output_mode = str(self._saved_output_mode or self.OUTPUT_MODE_CUMULATIVE).strip().lower()
        if want_output_mode == self.OUTPUT_MODE_CUMULATIVE:
            self.tg_output_mode.set_second_checked(True)
        else:
            self.tg_output_mode.set_first_checked(True)

        translation_available = self._translation_runtime_available()
        self.tg_mode.set_second_enabled(bool(translation_available))

        want_mode = str(self._saved_mode or RuntimeProfiles.LIVE_UI_DEFAULT_MODE).strip().lower()
        if want_mode == RuntimeProfiles.LIVE_UI_MODE_TRANSCRIBE_TRANSLATE and translation_available:
            self.tg_mode.set_second_checked(True)
        else:
            self.tg_mode.set_first_checked(True)

    @staticmethod
    def _resolve_source_language_selection(selection: str | None) -> str:
        return resolve_source_language_selection(
            selection,
            supported=supported_source_language_codes(),
        )

    @staticmethod
    def _resolve_target_language_selection(selection: str | None) -> str:
        return resolve_target_language_selection(
            selection,
            supported=supported_target_language_codes(),
        )

    def refresh_defaults_from_settings(self) -> None:
        self._refresh_language_combos()

    def _refresh_language_combos(self) -> None:
        src_codes = supported_source_language_codes()
        src_items = build_source_language_items(
            "live",
            supported=src_codes,
            ui_language=current_language(),
        )
        rebuild_code_combo(
            self.cmb_source_language,
            src_items,
            desired_code=self._resolve_source_language_selection(self._session_source_language),
            fallback_code=LanguagePolicy.PREFERRED,
        )
        self._session_source_language = combo_current_code(self.cmb_source_language, default=LanguagePolicy.PREFERRED)

        tgt_codes = supported_target_language_codes()
        tgt_items = build_target_language_items(
            "live",
            supported=tgt_codes,
            ui_language=current_language(),
        )
        rebuild_code_combo(
            self.cmb_target_language,
            tgt_items,
            desired_code=self._resolve_target_language_selection(self._session_target_language),
            fallback_code=LanguagePolicy.PREFERRED,
        )
        self._session_target_language = combo_current_code(self.cmb_target_language, default=LanguagePolicy.PREFERRED)

    @staticmethod
    def _effective_source_language_code(selection: str) -> str:
        return effective_source_language_code(
            "live",
            selection,
            supported=supported_source_language_codes(),
        )

    def _trigger_quick_options_autosave(self, *, sync_ui: bool = True) -> None:
        if sync_ui:
            self._sync_options_ui()
        self._opt_autosave.trigger()

    def _on_quick_option_changed(self, *_args) -> None:
        self._trigger_quick_options_autosave()

    def _on_device_changed(self, *_args) -> None:
        try:
            self._saved_device_name = str(self.cmb_device.currentData() or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._saved_device_name = ""

        self._trigger_quick_options_autosave(sync_ui=False)

    def _on_source_language_changed(self, *_args) -> None:
        self._session_source_language = str(
            self.cmb_source_language.code()
            or self._session_source_language
            or LanguagePolicy.PREFERRED
        )
        self._trigger_quick_options_autosave(sync_ui=False)

    def _on_target_language_changed(self, *_args) -> None:
        self._session_target_language = str(
            self.cmb_target_language.code()
            or self._session_target_language
            or LanguagePolicy.PREFERRED
        )
        self._trigger_quick_options_autosave(sync_ui=False)

    def _build_quick_options_payload(self) -> dict[str, Any]:
        mode = (
            RuntimeProfiles.LIVE_UI_MODE_TRANSCRIBE_TRANSLATE
            if self._is_translate_mode_checked()
            else RuntimeProfiles.LIVE_UI_MODE_TRANSCRIBE
        )
        profile = RuntimeProfiles.normalize_live_profile(self.cmb_profile.currentData() or AppConfig.live_ui_profile())
        output_mode = self._current_output_mode()
        device_name = str(self.cmb_device.currentData() or "").strip()
        return build_live_quick_options_payload(
            mode=mode,
            profile=profile,
            output_mode=output_mode,
            device_name=device_name,
            source_language_selection=self._session_source_language,
            target_language_selection=self._session_target_language,
        )

    def _commit_quick_options_payload(self, payload: dict[str, Any]) -> None:
        coord = self.coordinator()
        if coord is None:
            return
        coord.save_quick_options(payload)

    def on_quick_options_saved(self, _snap: SettingsSnapshot) -> None:
        self._saved_device_name = AppConfig.live_ui_device_name()
        self._saved_profile = AppConfig.live_ui_profile()
        self._saved_mode = AppConfig.live_ui_mode()
        self._saved_output_mode = AppConfig.live_ui_output_mode()

    def on_quick_options_save_error(self, key: str, params: dict[str, Any]) -> None:
        dialogs.show_error(self, key=key, params=params or {})

    def _refresh_devices_clicked(self) -> None:
        self._refresh_devices(show_dialog=True)

    def _refresh_devices(self, *, show_dialog: bool) -> None:
        self.cmb_device.clear()

        saved_device_name = str(self._saved_device_name or "").strip()

        coord = self.coordinator()
        names = coord.list_input_devices() if coord is not None else []
        self._has_audio_devices = bool(names)

        if not names:
            self.cmb_device.addItem(tr("live.device.none"), "")

            self._set_status("status.no_devices")
            _LOG.debug("Live devices refreshed. count=0 show_dialog=%s", bool(show_dialog))

            if show_dialog and not self._warned_no_devices_for_tab:
                self._warned_no_devices_for_tab = True
                dialogs.show_no_microphone_dialog(self)

            self._sync_options_ui()
            return

        self._warned_no_devices_for_tab = False
        for name in names:
            self.cmb_device.addItem(name, name)
        _LOG.debug("Live devices refreshed. count=%s selected=%s", len(names), saved_device_name or names[0])

        if saved_device_name:
            for i in range(self.cmb_device.count()):
                item_name = str(self.cmb_device.itemData(i) or "").strip()
                if item_name == saved_device_name:
                    self.cmb_device.setCurrentIndex(i)
                    break

        if self._state == self.STATE_STOPPED and not self._coordinator_is_running():
            self._set_status("status.idle")

        self._sync_options_ui()

    def _ensure_model_ready(self) -> bool:
        """Return True if ASR pipeline is available."""
        transcription_presentation = self._build_transcription_runtime_presentation()
        if transcription_presentation.state == "ready":
            return True
        self._set_runtime_status_presentation(transcription_presentation)
        self._update_buttons()
        return False

    def _on_start_clicked(self) -> None:
        if not self._has_audio_devices:
            _LOG.debug("Live start blocked. reason=no_microphones")
            dialogs.show_no_microphone_dialog(self)
            self._set_status("status.no_devices")
            return

        if self._state == self.STATE_STOPPED:
            self._start_live_new_session()
            return

        coord = self.coordinator()
        if self._state == self.STATE_PAUSED and coord is not None:
            try:
                coord.resume()
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug("Live resume request skipped. detail=%s", ex)
            _LOG.info("Live session resumed.")
            self._set_panel_state(self.STATE_LISTENING, status="status.listening")

    def _on_pause_clicked(self) -> None:
        if self._state != self.STATE_LISTENING:
            return
        coord = self.coordinator()
        if coord is None:
            return

        try:
            coord.pause()
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Live pause request skipped. detail=%s", ex)

        _LOG.info("Live session paused.")
        self._set_panel_state(self.STATE_PAUSED, status="status.paused")

    def _on_stop_clicked(self) -> None:
        _LOG.info("Live session stop requested.")
        self._stop_live_session()
        self.spectrum.clear()
        self._set_panel_state(self.STATE_STOPPED, status="status.stopped")

    def _start_live_new_session(self) -> None:
        if not self._has_audio_devices:
            _LOG.debug("Live session start blocked. reason=no_microphones")
            self._set_status("status.no_devices")
            dialogs.show_no_microphone_dialog(self)
            self._update_buttons()
            return

        if self._coordinator_is_running():
            return

        self._clear_text()

        if not self._ensure_model_ready():
            _LOG.debug("Live session start blocked. reason=asr_unavailable")
            return

        self._start_live_session()
        _LOG.info("Live session started.")
        self._set_panel_state(self.STATE_LISTENING, status="status.listening")

    def _build_live_session_request(self) -> dict[str, Any]:
        device_name = str(self.cmb_device.currentData() or "").strip()
        source_language = self._effective_source_language_code(
            str(self.cmb_source_language.code() or self._session_source_language or LanguagePolicy.PREFERRED)
        )

        translation_available = self._translation_runtime_available()
        translate_requested = self._is_translate_mode_checked() and translation_available
        translation_runtime = self._translation_runtime(
            requested_enabled=translate_requested,
            target_code=self.cmb_target_language.code(),
        )

        profile = RuntimeProfiles.normalize_live_profile(
            self.cmb_profile.currentData() or RuntimeProfiles.LIVE_DEFAULT_PROFILE
        )
        output_mode = self._current_output_mode()
        self._session_output_mode = output_mode
        runtime_profile = RuntimeProfiles.resolve_live_runtime(
            output_mode=output_mode,
            profile=profile,
        )

        return {
            "device_name": device_name,
            "source_language": source_language,
            "target_language": translation_runtime.target_language,
            "translate_enabled": translation_runtime.enabled,
            "translate_requested": bool(translate_requested),
            "profile": profile,
            "output_mode": output_mode,
            "runtime_profile": runtime_profile,
        }

    def _start_live_session(self) -> None:
        coord = self.coordinator()
        if coord is None:
            return

        session_request = self._build_live_session_request()
        live_profile = cast(dict[str, Any], session_request.get("runtime_profile") or {})
        translate_requested = bool(session_request.pop("translate_requested", False))

        _LOG.debug(
            (
                "Live session prepared. device=%s profile=%s output_mode=%s "
                "translate_requested=%s translate_enabled=%s source_language=%s "
                "target_language=%s chunk_length_s=%s stride_length_s=%s"
            ),
            session_request.get("device_name", ""),
            session_request.get("profile", ""),
            session_request.get("output_mode", ""),
            bool(translate_requested),
            bool(session_request.get("translate_enabled")),
            session_request.get("source_language", ""),
            session_request.get("target_language", ""),
            int(live_profile.get("chunk_length_s", 0)),
            int(live_profile.get("stride_length_s", 0)),
        )
        coord.start_session(**session_request)

    def _stop_live_session(self) -> None:
        coord = self.coordinator()
        if coord is not None:
            coord.stop()

    def on_live_finished(self) -> None:
        if not self._has_audio_devices:
            self._set_status("status.no_devices")
        elif self._state == self.STATE_STOPPED:
            self._set_status("status.stopped")
        else:
            self._set_panel_state(self.STATE_STOPPED, status="status.stopped", update_buttons=False)

        self.spectrum.clear()
        _LOG.debug("Live worker finished. has_audio_devices=%s state=%s", bool(self._has_audio_devices), self._state)
        self._update_buttons()

    def _clear_text(self) -> None:
        self._display_source = ""
        self._display_target = ""
        self._archive_source = ""
        self._archive_target = ""
        self._applied_source = ""
        self._applied_target = ""
        self._set_detected_language("")
        self._render_timer.stop()
        self.spectrum.clear()
        self._apply_render_output(force=True)
        self._update_buttons()

    @staticmethod
    def _shared_prefix_length(left: str, right: str) -> int:
        limit = min(len(left), len(right))
        idx = 0
        while idx < limit and left[idx] == right[idx]:
            idx += 1
        return idx

    @staticmethod
    def _set_text_edit_text(widget: QtWidgets.QTextEdit, text: str, *, force: bool = False) -> None:
        value = str(text or "")
        current = widget.toPlainText()
        if not force and current == value:
            return

        if not force and current and value.startswith(current):
            suffix = value[len(current):]
            if suffix:
                try:
                    cursor = widget.textCursor()
                    cursor.movePosition(QtGui.QTextCursor.End)
                    cursor.insertText(suffix)
                    widget.setTextCursor(cursor)
                    return
                except (AttributeError, RuntimeError, TypeError):
                    widget.setPlainText(value)
                    return

        if not force and current and value:
            shared_prefix = LivePanel._shared_prefix_length(current, value)
            min_len = min(len(current), len(value))
            if shared_prefix >= 48 and shared_prefix >= int(min_len * 0.75):
                try:
                    cursor = widget.textCursor()
                    cursor.beginEditBlock()
                    cursor.setPosition(shared_prefix)
                    cursor.movePosition(QtGui.QTextCursor.End, QtGui.QTextCursor.KeepAnchor)
                    cursor.removeSelectedText()
                    tail = value[shared_prefix:]
                    if tail:
                        cursor.insertText(tail)
                    cursor.endEditBlock()
                    widget.setTextCursor(cursor)
                    return
                except (AttributeError, RuntimeError, TypeError):
                    widget.setPlainText(value)
                    return

        widget.setPlainText(value)

    def _render_output(self) -> None:
        if self._render_timer.isActive():
            return
        self._render_timer.start()

    def _apply_render_output(self, *, force: bool = False) -> None:
        is_translate = self._is_translate_mode_effective()

        if force or self.target_text_host.isVisible() != is_translate:
            self.target_text_host.setVisible(is_translate)

        self.txt_source.setPlaceholderText(tr("live.placeholder.source"))
        source_text = self._current_render_source_text()
        if force or source_text != self._applied_source:
            self._set_text_edit_text(self.txt_source, source_text, force=force)
            self._applied_source = source_text

        if not is_translate:
            if force or self._applied_target:
                self._set_text_edit_text(self.txt_target, "", force=force)
                self._applied_target = ""
            return

        self.txt_target.setPlaceholderText(tr("live.placeholder.target"))
        target_text = self._current_render_target_text()
        if force or target_text != self._applied_target:
            self._set_text_edit_text(self.txt_target, target_text, force=force)
            self._applied_target = target_text

    def _save_transcript(self) -> None:
        if not (self._state == self.STATE_STOPPED and (not self._coordinator_is_running())):
            return

        src_text, tgt_text = self._current_session_texts()

        translate_requested = bool(tgt_text) or self._is_translate_mode_effective()
        if not (src_text or tgt_text):
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            tr("live.controls.save_transcript"),
            TranscriptionOutputPolicy.transcript_filename("txt"),
            tr("live.save.file_filter"),
        )
        if not path:
            return

        coordinator = self.coordinator()
        if coordinator is None:
            return

        try:
            coordinator.save_transcript(
                target_path=path,
                source_text=src_text,
                target_text=tgt_text,
                write_source_companion=bool(translate_requested),
            )
        except Exception as e:
            self._set_status("status.error")
            dialogs.show_error(self, key="error.generic", params={"detail": str(e)})


    def _translation_runtime_available(self) -> bool:
        return bool(self._translation_ready) and translation_runtime_available(
            translation_state=EngineRuntimeState(
                ready=self._translation_ready,
                error_key=self._translation_error_key,
                error_params=self._translation_error_params,
            ),
            model_cfg=EngineCatalog.current_model_cfg("translation"),
        )

    def _build_transcription_runtime_presentation(self) -> RuntimePresentation:
        return build_runtime_presentation(
            ready=self._transcription_ready,
            disabled=EngineCatalog.current_model_disabled("transcription"),
            ready_text=tr("status.idle"),
            disabled_text=tr("files.runtime.status_disabled"),
            missing_text=tr("live.model.unavailable"),
            error_key=self._transcription_error_key,
            error_params=self._transcription_error_params,
            error_status_key="status.error",
        )

    def _build_translation_runtime_presentation(self) -> RuntimePresentation:
        return build_runtime_presentation(
            ready=self._translation_runtime_available(),
            disabled=EngineCatalog.current_model_disabled("translation"),
            ready_text=tr("status.idle"),
            disabled_text=tr("files.runtime.status_disabled"),
            missing_text=tr("live.model.unavailable"),
            error_key=self._translation_error_key,
            error_params=self._translation_error_params,
            disabled_status_key="",
            missing_status_key="",
            error_status_key="",
        )

    def _apply_translation_runtime_tooltips(self, presentation: RuntimePresentation) -> None:
        tooltip = "" if presentation.state == "ready" else str(presentation.tooltip or presentation.text or "")
        self.tg_mode.setToolTip(tooltip)
        self.cmb_target_language.setToolTip(tooltip)

    def _should_show_translation_runtime_feedback(self) -> bool:
        if self._coordinator_is_running() or self._state != self.STATE_STOPPED:
            return False
        return self._base_status_key in ("", "status.idle", "status.stopped")

    def _translation_runtime(
        self,
        *,
        requested_enabled: bool | None = None,
        target_code: str | None = None,
    ) -> Any:
        enabled = self._translation_runtime_available() if requested_enabled is None else bool(requested_enabled)
        target = self.cmb_target_language.code() if target_code is None else str(target_code or "")
        return compute_translation_runtime(
            requested_enabled=enabled,
            target_code=target,
            ui_language=current_language(),
            tab_name="live",
            supported=supported_target_language_codes(),
        )

    def _panel_control_widgets(self) -> tuple[QtWidgets.QWidget, ...]:
        return (
            self.cmb_device,
            self.tg_output_mode,
            self.tg_mode,
            self.cmb_profile,
            self.cmb_source_language,
            self.cmb_target_language,
            self.btn_start,
            self.btn_pause,
            self.btn_stop,
            self.btn_save,
            self.btn_clear,
        )

    def _set_panel_controls_enabled(self, enabled: bool) -> None:
        for widget in self._panel_control_widgets():
            widget.setEnabled(bool(enabled))

    def _set_panel_state(self, state: str, *, status: str | None = None, update_buttons: bool = True) -> None:
        self._state = str(state or self.STATE_STOPPED)
        if status is not None:
            self._set_status(status)
        if update_buttons:
            self._update_buttons()

    def _current_output_mode(self) -> str:
        if self._coordinator_is_running():
            return RuntimeProfiles.normalize_live_output_mode(self._session_output_mode)
        if bool(self.tg_output_mode.is_second_checked()):
            return self.OUTPUT_MODE_CUMULATIVE
        return self.OUTPUT_MODE_STREAM

    def _current_session_texts(self) -> tuple[str, str]:
        if self._current_output_mode() == self.OUTPUT_MODE_STREAM:
            return (
                str(self._display_source or "").strip(),
                str(self._display_target or "").strip(),
            )
        return (
            str(self._archive_source or "").strip(),
            str(self._archive_target or "").strip(),
        )

    def _current_render_source_text(self) -> str:
        if self._current_output_mode() == self.OUTPUT_MODE_CUMULATIVE:
            return self._archive_source or ""
        return self._display_source or ""

    def _current_render_target_text(self) -> str:
        if self._current_output_mode() == self.OUTPUT_MODE_CUMULATIVE:
            return self._archive_target or ""
        return self._display_target or ""

    @staticmethod
    def _format_detected_language(lang_code: str) -> str:
        code = str(lang_code or "").strip().lower()
        if not code:
            return tr("common.na")
        label = language_display_name(code, ui_lang=current_language())
        return str(label or code)

    def _set_detected_language(self, lang_code: str) -> None:
        code = str(lang_code or "").strip().lower()
        self._detected_language_code = code
        self.lbl_detected_value.setText(self._format_detected_language(code))

    def _is_translate_mode_checked(self) -> bool:
        return bool(self.tg_mode.is_second_checked())

    def _is_translate_mode_effective(self) -> bool:
        if not self._is_translate_mode_checked():
            return False

        tr_rt = self._translation_runtime()
        return bool(tr_rt.enabled)

    def _log_availability_state(self, *, reason: str) -> None:
        state = (
            bool(self._transcription_ready),
            bool(self._has_audio_devices),
            self._state,
            bool(self._coordinator_is_running()),
            bool(self._translation_runtime_available()),
            bool(self._is_translate_mode_effective()),
        )
        if state == self._last_availability_debug_key:
            return
        self._last_availability_debug_key = state
        _LOG.debug(
            (
                "Live availability changed. reason=%s panel=live asr_ready=%s "
                "microphones=%s state=%s running=%s translation_available=%s "
                "translate_effective=%s"
            ),
            reason,
            bool(self._transcription_ready),
            bool(self._has_audio_devices),
            self._state,
            bool(self._coordinator_is_running()),
            bool(self._translation_runtime_available()),
            bool(self._is_translate_mode_effective()),
        )

    def _sync_options_ui(self) -> None:
        translation_presentation = self._build_translation_runtime_presentation()
        translation_available = translation_presentation.state == "ready"
        try:
            self.tg_mode.set_second_enabled(bool(translation_available) and self._can_change_settings())
        except (AttributeError, RuntimeError, TypeError):
            self.tg_mode.setEnabled(False)
        if not translation_available and self.tg_mode.is_second_checked():
            try:
                self.tg_mode.set_first_checked(True)
            except (AttributeError, RuntimeError, TypeError):
                self.tg_mode.setEnabled(False)

        self._apply_translation_runtime_tooltips(translation_presentation)
        self._render_output()
        self._update_buttons()
        self._log_availability_state(reason="options_synced")

    def _apply_status_label(self, *, text: str, status_key: str = "", tooltip: str = "") -> None:
        value = str(text or "").strip()
        display_text = tr(value) if value.startswith("status.") else value
        tip = str(tooltip or display_text or "").strip()
        key = str(status_key or "").strip()
        self._status_key = key if key.startswith("status.") else ""
        self.lbl_status_value.setText(display_text)
        self.lbl_status_value.setToolTip(tip)

    def _refresh_status_label(self) -> None:
        presentation = self._runtime_status_presentation
        if presentation is not None:
            self._apply_status_label(
                text=presentation.text,
                status_key=presentation.status_key,
                tooltip=presentation.tooltip,
            )
            return
        self._apply_status_label(
            text=self._status_text,
            status_key=self._base_status_key,
        )

    def _set_runtime_status_presentation(self, presentation: RuntimePresentation | None) -> None:
        self._runtime_status_presentation = presentation
        self._refresh_status_label()

    def _set_status(self, msg: str) -> None:
        text = str(msg or "").strip()
        self._status_text = text
        self._base_status_key = text if text.startswith("status.") else ""
        self._refresh_status_label()

    def _can_change_settings(self) -> bool:
        return (not self._coordinator_is_running()) and self._state == self.STATE_STOPPED

    def _update_save_clear_buttons(self, *, running: bool | None = None) -> None:
        if running is None:
            running = self._coordinator_is_running()

        has_archive = bool(str(self._archive_source or "").strip() or str(self._archive_target or "").strip())
        has_display = bool(str(self._display_source or "").strip() or str(self._display_target or "").strip())
        idle_ready = self._state == self.STATE_STOPPED and (not running)
        stream_mode = self._current_output_mode() == self.OUTPUT_MODE_STREAM

        can_save = idle_ready and (has_display if stream_mode else has_archive)
        can_clear = idle_ready and (has_display or has_archive)
        self.btn_save.setEnabled(bool(can_save))
        self.btn_clear.setEnabled(bool(can_clear))

    def _sync_spectrum_state(self) -> None:
        if self._status_key == "status.error":
            self.spectrum.set_visual_state(AudioSpectrumWidget.STATE_ERROR)
            return

        if (not self._has_audio_devices) or (not self._transcription_ready):
            self.spectrum.clear()
            self.spectrum.set_visual_state(AudioSpectrumWidget.STATE_DISABLED)
            return

        if self._state == self.STATE_LISTENING:
            self.spectrum.set_visual_state(AudioSpectrumWidget.STATE_ACTIVE)
            return

        if self._state == self.STATE_PAUSED:
            self.spectrum.set_visual_state(AudioSpectrumWidget.STATE_PAUSED)
            return

        self.spectrum.set_visual_state(AudioSpectrumWidget.STATE_IDLE)

    def _update_buttons(self) -> None:
        running = self._coordinator_is_running()

        if not self._has_audio_devices:
            self.btn_refresh_devices.setEnabled(True)
            self._set_panel_controls_enabled(False)

            self._set_runtime_status_presentation(None)
            self._set_status("status.no_devices")
            self._sync_spectrum_state()
            return

        transcription_presentation = self._build_transcription_runtime_presentation()
        if transcription_presentation.state != "ready":
            self.btn_refresh_devices.setEnabled(True)
            self._set_panel_controls_enabled(False)

            self._set_runtime_status_presentation(transcription_presentation)
            self._sync_spectrum_state()
            return

        can_config = self._can_change_settings()
        translation_presentation = self._build_translation_runtime_presentation()
        translation_available = translation_presentation.state == "ready"

        self.cmb_device.setEnabled(can_config)
        self.btn_refresh_devices.setEnabled(can_config)
        self.tg_output_mode.setEnabled(can_config)
        self.tg_mode.setEnabled(can_config)
        self.cmb_profile.setEnabled(can_config)

        self.cmb_source_language.setEnabled(can_config)

        self._apply_translation_runtime_tooltips(translation_presentation)
        if can_config:
            try:
                self.tg_mode.set_second_enabled(bool(translation_available))
            except (AttributeError, RuntimeError, TypeError):
                self.tg_mode.setEnabled(False)

        wants_translate = self._is_translate_mode_checked() and translation_available
        self.cmb_target_language.setEnabled(can_config and wants_translate)

        if self._state == self.STATE_STOPPED:
            self.btn_start.setEnabled(not running)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
        elif self._state == self.STATE_LISTENING:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.btn_stop.setEnabled(True)
        else:
            self.btn_start.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(True)

        if translation_available or not self._should_show_translation_runtime_feedback():
            self._set_runtime_status_presentation(None)
        else:
            self._set_runtime_status_presentation(translation_presentation)

        self._update_save_clear_buttons(running=running)
        self._sync_spectrum_state()
        self._log_availability_state(reason="buttons_updated")

    def on_worker_failed(self, key: str, params: dict[str, Any]) -> None:
        self._set_panel_state(self.STATE_STOPPED, status="status.error", update_buttons=False)
        _LOG.debug(
            "Live worker failure received. detail=%s path=%s",
            str((params or {}).get("detail") or ""),
            str((params or {}).get("path") or ""),
        )
        self.spectrum.clear()
        self._sync_spectrum_state()
        dialogs.show_error(self, key=key, params=params)
        self._stop_live_session()
        self._update_buttons()

    def on_status(self, msg: str) -> None:
        msg = str(msg or "").strip()
        if not self._has_audio_devices:
            self._set_status("status.no_devices")
            return
        if self._state == self.STATE_PAUSED:
            self._set_status("status.paused")
            return
        if self._state == self.STATE_STOPPED and msg == "status.idle":
            return
        self._set_status(msg)

    def on_detected_language(self, lang: str) -> None:
        if not lang:
            return
        self._set_detected_language(lang)

    def on_source_text(self, text: str) -> None:
        self._set_live_text("_display_source", text, render_mode=self.OUTPUT_MODE_STREAM)

    def on_target_text(self, text: str) -> None:
        self._set_live_text("_display_target", text, render_mode=self.OUTPUT_MODE_STREAM)

    def on_archive_source_text(self, text: str) -> None:
        self._set_live_text(
            "_archive_source",
            text,
            render_mode=self.OUTPUT_MODE_CUMULATIVE,
            update_save_clear=True,
        )

    def on_archive_target_text(self, text: str) -> None:
        self._set_live_text(
            "_archive_target",
            text,
            render_mode=self.OUTPUT_MODE_CUMULATIVE,
            update_save_clear=True,
        )

    def on_spectrum(self, values: object) -> None:
        self.spectrum.set_spectrum(values)

    def _set_live_text(
        self,
        attr_name: str,
        text: str,
        *,
        render_mode: str,
        update_save_clear: bool = False,
    ) -> None:
        value = str(text or "")
        if value == getattr(self, attr_name, ""):
            return
        setattr(self, attr_name, value)
        if update_save_clear:
            self._update_save_clear_buttons()
        if self._current_output_mode() == render_mode:
            self._render_output()
