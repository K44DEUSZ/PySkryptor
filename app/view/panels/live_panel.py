# app/view/panels/live_panel.py
from __future__ import annotations

import logging
from typing import Any, cast

from PyQt5 import QtCore, QtGui, QtWidgets

from app.view.components import dialogs
from app.view.components.popup_combo import LanguageCombo, PopupComboBox
from app.controller.platform.microphone import list_input_device_names
from app.controller.support.localization import tr, Translator, language_display_name
from app.controller.support.runtime_resolver import (
    compute_translation_runtime,
    build_live_quick_options_payload,
    translation_language_codes,
    transcription_language_codes,
    translation_runtime_available,
)
from app.controller.tasks.live_transcription_task import LiveTranscriptionWorker
from app.model.config.app_config import AppConfig as Config
from app.model.services.ai_models_service import current_translation_model_cfg
from app.model.io.transcript_writer import TranscriptWriter
from app.model.services.settings_service import SettingsSnapshot

from app.view.components.audio_spectrum import AudioSpectrumWidget
from app.view.components.choice_toggle import ChoiceToggle
from app.view.components.section_group import SectionGroup
from app.controller.support.task_thread_runner import TaskThreadRunner
from app.controller.support.options_autosave_controller import OptionsAutosaveController
from app.view.support.widget_effects import enable_styled_background
from app.view.support.widget_setup import (
    build_field_stack,
    build_layout_host,
    setup_button,
    setup_combo,
    setup_layout,
    setup_text_editor,
)
from app.view.ui_config import ui

BootContext = dict[str, Any]
_LOG = logging.getLogger(__name__)


class LivePanel(QtWidgets.QWidget):
    """Live tab: capture audio input and run streaming ASR/translation."""

    STATE_STOPPED = "stopped"
    STATE_LISTENING = "listening"
    STATE_PAUSED = "paused"

    OUTPUT_MODE_STREAM = "stream"
    OUTPUT_MODE_CUMULATIVE = "cumulative"

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        boot_ctx: BootContext | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LivePanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)
        self._first_shown = False

        self._init_runtime_state(boot_ctx)
        self._load_saved_options()
        self._build_ui()
        self._wire_signals()
        self._restore_initial_state()

    # ----- Build -----

    def _init_runtime_state(self, boot_ctx: BootContext | None) -> None:
        self._boot_ctx: BootContext | None = boot_ctx
        self._status_key = ""
        self._last_availability_debug_key: tuple | None = None

        self.pipe = (boot_ctx or {}).get("transcription_pipeline")
        self._live_runner = TaskThreadRunner(self)

        self._state: str = self.STATE_STOPPED
        self._has_audio_devices: bool = False
        self._first_shown: bool = False
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

        self._opt_autosave = OptionsAutosaveController(
            self,
            build_payload=self._build_quick_options_payload,
            apply_snapshot=self._on_quick_options_saved_snapshot,
            on_error=self._on_quick_options_save_error,
            is_busy=lambda: self._live_runner.is_running(),
            interval_ms=1200,
            pending_delay_ms=300,
            retry_delay_ms=600,
        )
        self._opt_autosave.set_blocked(True)

    def _load_saved_options(self) -> None:
        self._saved_device_name = Config.live_ui_device_name()
        self._saved_preset = Config.live_ui_preset()
        self._saved_mode = Config.live_ui_mode()
        self._saved_output_mode = Config.live_ui_output_mode()
        self._saved_show_source = Config.live_ui_show_source()

        self._session_source_language = Config.translation_source_language()
        self._session_target_language = Config.translation_target_language()

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

        self.btn_refresh_devices = QtWidgets.QPushButton(tr("live.ctrl.refresh"))
        self.btn_refresh_devices.setToolTip(tr("live.ctrl.refresh"))
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
        output_mode_host, _ = build_field_stack(self, tr("live.output_mode.label"), self.tg_output_mode, buddy=self.tg_output_mode)

        self.tg_mode = ChoiceToggle(
            first_text=tr("files.options.mode.transcribe"),
            second_text=tr("files.options.mode.transcribe_translate"),
            height=base_h,
        )
        self.tg_mode.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        mode_host, _ = build_field_stack(self, tr("common.field.mode"), self.tg_mode, buddy=self.tg_mode)

        self.cmb_preset = PopupComboBox()
        setup_combo(self.cmb_preset, min_h=base_h)
        preset_options = (
            ("low_latency", tr("live.preset.low_latency"), tr("live.preset.help.low_latency")),
            ("balanced", tr("live.preset.balanced"), tr("live.preset.help.balanced")),
            ("high_context", tr("live.preset.high_context"), tr("live.preset.help.high_context")),
        )
        for value, label, tooltip in preset_options:
            self.cmb_preset.addItem(label, value)
            idx = self.cmb_preset.count() - 1
            self.cmb_preset.setItemData(idx, tooltip, QtCore.Qt.ItemDataRole.ToolTipRole)
        preset_host, _ = build_field_stack(self, tr("live.preset.label"), self.cmb_preset, buddy=self.cmb_preset)

        self.cmb_src_lang = LanguageCombo(
            special_first=("lang.special.auto_detect", Config.LANGUAGE_AUTO_VALUE),
            codes_provider=transcription_language_codes,
        )
        self.cmb_src_lang.setMinimumHeight(base_h)

        self.cmb_tgt_lang = LanguageCombo(
            special_first=("lang.special.default_ui", Config.LANGUAGE_DEFAULT_UI_VALUE),
            codes_provider=translation_language_codes,
        )
        self.cmb_tgt_lang.setMinimumHeight(base_h)

        src_lang_host, _ = build_field_stack(self, tr("common.field.source_language"), self.cmb_src_lang, buddy=self.cmb_src_lang)
        tgt_lang_host, _ = build_field_stack(self, tr("common.field.target_language"), self.cmb_tgt_lang, buddy=self.cmb_tgt_lang)

        s_lay.addWidget(device_host, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(output_mode_host, 0, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(mode_host, 1, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(preset_host, 1, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(src_lang_host, 2, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        s_lay.addWidget(tgt_lang_host, 2, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)

    def _build_controls_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        controls_box = SectionGroup(self, object_name="LiveControlsGroup", role="panelGroup", layout="hbox")
        c_lay = cast(QtWidgets.QHBoxLayout, controls_box.root)
        setup_layout(c_lay, cfg=cfg, margins=(cfg.margin, cfg.margin, cfg.margin, cfg.margin), spacing=cfg.spacing)
        root.addWidget(controls_box)

        controls_left, left_lay = build_layout_host(parent=self, layout="vbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        btn_big_row, big_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)

        self.btn_start = QtWidgets.QPushButton(tr("live.ctrl.start"))
        self.btn_pause = QtWidgets.QPushButton(tr("live.ctrl.pause"))
        self.btn_stop = QtWidgets.QPushButton(tr("live.ctrl.stop"))
        for button in (self.btn_start, self.btn_pause, self.btn_stop):
            setup_button(button, min_h=cfg.button_big_h, min_w=cfg.control_min_w)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            big_lay.addWidget(button)
        left_lay.addWidget(btn_big_row)

        save_clear_outer, out_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=0)
        save_clear_inner, in_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)

        self.btn_save = QtWidgets.QPushButton(tr("live.ctrl.save_transcript"))
        self.btn_clear = QtWidgets.QPushButton(tr("live.ctrl.clear"))
        for button in (self.btn_save, self.btn_clear):
            setup_button(button, min_h=base_h, min_w=cfg.control_min_w)
            in_lay.addWidget(button)

        out_lay.addStretch(1)
        out_lay.addWidget(save_clear_inner, 4)
        out_lay.addStretch(1)
        left_lay.addWidget(save_clear_outer)

        self.lbl_status_title = QtWidgets.QLabel(f"<b>{tr('live.status_label')}</b>")
        self.lbl_status_title.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.lbl_status_value = QtWidgets.QLabel(tr("status.idle"))
        self.lbl_status_value.setProperty("uiRole", "hint")
        self.lbl_status_value.setWordWrap(True)

        self.lbl_detected_title = QtWidgets.QLabel(f"<b>{tr('live.detected_language_label')}</b>")
        self.lbl_detected_title.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.lbl_detected_value = QtWidgets.QLabel("—")
        self.lbl_detected_value.setProperty("uiRole", "hint")
        self.lbl_detected_value.setWordWrap(True)

        status_row, status_row_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.space_s)
        status_row_lay.addWidget(self.lbl_status_title, 0)
        status_row_lay.addWidget(self.lbl_status_value, 1)

        detected_row, detected_row_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.space_s)
        detected_row_lay.addWidget(self.lbl_detected_title, 0)
        detected_row_lay.addWidget(self.lbl_detected_value, 1)

        info_row, info_row_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        info_row_lay.addWidget(status_row, 1)
        info_row_lay.addWidget(detected_row, 1)

        self.spectrum = AudioSpectrumWidget(bars=18)
        meter_host, meter_lay = build_layout_host(parent=self, layout="vbox", margins=(0, 0, 0, 0), spacing=0)
        meter_host.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        meter_lay.addWidget(info_row, 0)
        meter_lay.addSpacing(cfg.space_s)
        meter_lay.addWidget(self.spectrum, 1)

        c_lay.addWidget(controls_left, 1)
        c_lay.addWidget(meter_host, 1)

    def _build_output_section(self, root: QtWidgets.QVBoxLayout) -> None:
        cfg = self._ui
        self.txt_src = QtWidgets.QTextEdit()
        setup_text_editor(self.txt_src, placeholder=tr("live.placeholder.source"))
        self.txt_src.setReadOnly(True)

        self.txt_tgt = QtWidgets.QTextEdit()
        setup_text_editor(self.txt_tgt, placeholder=tr("live.placeholder.target"))
        self.txt_tgt.setReadOnly(True)

        out_text_host, out_text_lay = build_layout_host(parent=self, layout="hbox", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        self.src_text_host, src_text_lay = build_layout_host(parent=self, layout="vbox", margins=(0, 0, 0, 0), spacing=0)
        self.tgt_text_host, tgt_text_lay = build_layout_host(parent=self, layout="vbox", margins=(0, 0, 0, 0), spacing=0)

        src_text_lay.addWidget(self.txt_src, 1)
        tgt_text_lay.addWidget(self.txt_tgt, 1)
        out_text_lay.addWidget(self.src_text_host, 1)
        out_text_lay.addWidget(self.tgt_text_host, 1)
        root.addWidget(out_text_host, 1)

    # ----- Wiring -----

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
        self.cmb_preset.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_src_lang.currentIndexChanged.connect(self._on_source_language_changed)
        self.cmb_tgt_lang.currentIndexChanged.connect(self._on_target_language_changed)

    # ----- Restore / bootstrap -----

    def _restore_initial_state(self) -> None:
        self._apply_saved_options_to_ui()
        self._refresh_devices(show_dialog=False)
        self._sync_options_ui()
        self._apply_render_output(force=True)
        self._opt_autosave.set_blocked(False)

    # ----- Lifecycle -----

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._first_shown:
            self._first_shown = True
            self._refresh_devices(show_dialog=True)

    # ----- Quick options (autosave) -----

    def _apply_saved_options_to_ui(self) -> None:
        try:
            self.cmb_src_lang.set_code(self._session_source_language)
        except Exception:
            pass
        try:
            self.cmb_tgt_lang.set_code(self._session_target_language)
        except Exception:
            pass

        want_preset = str(self._saved_preset or "balanced").strip().lower()
        if want_preset not in ("low_latency", "balanced", "high_context"):
            want_preset = "balanced"
        for i in range(self.cmb_preset.count()):
            if str(self.cmb_preset.itemData(i) or "").strip().lower() == want_preset:
                self.cmb_preset.setCurrentIndex(i)
                break

        want_output_mode = str(self._saved_output_mode or self.OUTPUT_MODE_CUMULATIVE).strip().lower()
        if want_output_mode == self.OUTPUT_MODE_CUMULATIVE:
            self.tg_output_mode.set_second_checked(True)
        else:
            self.tg_output_mode.set_first_checked(True)

        tr_ready = self._translation_engine_ready()
        self.tg_mode.set_second_enabled(bool(tr_ready))

        want_mode = str(self._saved_mode or "transcribe").strip().lower()
        if want_mode == "transcribe_translate" and tr_ready:
            self.tg_mode.set_second_checked(True)
        else:
            self.tg_mode.set_first_checked(True)

    def _trigger_quick_options_autosave(self, *, sync_ui: bool = True) -> None:
        if sync_ui:
            self._sync_options_ui()
        self._opt_autosave.trigger()

    def _on_quick_option_changed(self, *_args) -> None:
        self._trigger_quick_options_autosave()

    def _on_device_changed(self, *_args) -> None:
        try:
            self._saved_device_name = str(self.cmb_device.currentData() or "").strip()
        except Exception:
            self._saved_device_name = ""

        self._trigger_quick_options_autosave(sync_ui=False)

    def _on_source_language_changed(self, *_args) -> None:
        self._session_source_language = self.cmb_src_lang.code()
        self._trigger_quick_options_autosave()

    def _on_target_language_changed(self, *_args) -> None:
        self._session_target_language = self.cmb_tgt_lang.code()
        self._trigger_quick_options_autosave()

    def _build_quick_options_payload(self) -> dict[str, Any]:
        mode = "transcribe_translate" if self._is_translate_mode_checked() else "transcribe"
        preset = str(self.cmb_preset.currentData() or "balanced").strip().lower() or "balanced"
        output_mode = self._current_output_mode()
        device_name = str(self.cmb_device.currentData() or "").strip()
        show_source = bool(self._saved_show_source)

        return build_live_quick_options_payload(
            mode=mode,
            preset=preset,
            output_mode=output_mode,
            device_name=device_name,
            show_source=show_source,
            source_language=self._session_source_language,
            target_language=self._session_target_language,
        )

    def _on_quick_options_saved_snapshot(self, snap: object) -> None:
        try:
            Config.update_from_snapshot(cast(SettingsSnapshot, snap), sections=("app", "translation"))
        except Exception:
            pass

        self._saved_device_name = Config.live_ui_device_name()
        self._saved_preset = Config.live_ui_preset()
        self._saved_mode = Config.live_ui_mode()
        self._saved_output_mode = Config.live_ui_output_mode()
        self._saved_show_source = Config.live_ui_show_source()
        self._session_source_language = Config.translation_source_language()
        self._session_target_language = Config.translation_target_language()

    def _on_quick_options_save_error(self, key: str, params: dict[str, Any]) -> None:
        dialogs.show_error(self, key=key, params=params or {})

    def _refresh_devices_clicked(self) -> None:
        self._refresh_devices(show_dialog=True)

    def _refresh_devices(self, *, show_dialog: bool) -> None:
        self.cmb_device.clear()

        saved_device_name = str(self._saved_device_name or "").strip()

        names = list_input_device_names()
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

        if self._state == self.STATE_STOPPED and not self._live_runner.is_running():
            self._set_status("status.idle")

        self._sync_options_ui()

    # ----- Model readiness (boot) -----

    def _ensure_model_ready(self) -> bool:
        """Return True if ASR pipeline is available."""
        if self.pipe is not None:
            return True
        self._set_status(tr("live.model.unavailable"))
        self._update_buttons()
        return False

    # ----- Actions -----

    def _on_start_clicked(self) -> None:
        if not self._has_audio_devices:
            _LOG.debug("Live start blocked. reason=no_microphones")
            dialogs.show_no_microphone_dialog(self)
            self._set_status("status.no_devices")
            return

        if self._state == self.STATE_STOPPED:
            self._start_live_new_session()
            return

        wk = self._live_runner.worker
        if self._state == self.STATE_PAUSED and isinstance(wk, LiveTranscriptionWorker):
            try:
                wk.resume()
            except Exception:
                pass
            _LOG.debug("Live session resumed.")
            self._set_panel_state(self.STATE_LISTENING, status="status.listening")

    def _on_pause_clicked(self) -> None:
        if self._state != self.STATE_LISTENING:
            return
        wk = self._live_runner.worker
        if not isinstance(wk, LiveTranscriptionWorker):
            return

        try:
            wk.pause()
        except Exception:
            pass

        _LOG.debug("Live session paused.")
        self._set_panel_state(self.STATE_PAUSED, status="status.paused")

    def _on_stop_clicked(self) -> None:
        _LOG.debug("Live session stop requested.")
        self._stop_live()
        self.spectrum.clear()
        self._set_panel_state(self.STATE_STOPPED, status="status.stopped")

    def _start_live_new_session(self) -> None:
        if not self._has_audio_devices:
            _LOG.debug("Live session start blocked. reason=no_microphones")
            self._set_status("status.no_devices")
            dialogs.show_no_microphone_dialog(self)
            self._update_buttons()
            return

        if self._live_runner.is_running():
            return

        self._clear_text()

        if self.pipe is None and not self._ensure_model_ready():
            _LOG.debug("Live session start blocked. reason=asr_unavailable")
            return

        self._start_live_worker()
        _LOG.debug("Live session started.")
        self._set_panel_state(self.STATE_LISTENING, status="status.listening")

    def _start_live_worker(self) -> None:
        device_name = str(self.cmb_device.currentData() or "").strip()
        src_lang = self.cmb_src_lang.code()

        tr_ready = self._translation_engine_ready()
        translate_requested = self._is_translate_mode_checked() and tr_ready

        tr_rt = self._translation_runtime(
            requested_enabled=translate_requested,
            target_code=self.cmb_tgt_lang.code(),
        )
        translate_enabled = tr_rt.enabled
        tgt_lang = tr_rt.target_language

        preset = Config.normalize_live_preset(self.cmb_preset.currentData() or Config.LIVE_DEFAULT_PRESET)
        output_mode = self._current_output_mode()
        self._session_output_mode = output_mode
        live_profile = Config.live_runtime_profile(output_mode=output_mode, preset=preset)

        _LOG.debug(
            "Live worker prepared. device=%s preset=%s output_mode=%s translate_requested=%s translate_enabled=%s source_language=%s target_language=%s chunk_length_s=%s stride_length_s=%s",
            device_name,
            preset,
            output_mode,
            bool(translate_requested),
            bool(translate_enabled),
            src_lang,
            tgt_lang,
            int(live_profile.get("chunk_length_s", 0)),
            int(live_profile.get("stride_length_s", 0)),
        )
        wk = LiveTranscriptionWorker(
            pipe=self.pipe,
            device_name=device_name,
            source_language=src_lang,
            target_language=tgt_lang,
            translate_enabled=translate_enabled,
            preset_id=preset,
            output_mode=output_mode,
        )

        def _connect(worker: LiveTranscriptionWorker) -> None:
            worker.status.connect(self._on_status)
            worker.error.connect(self._on_worker_error)
            worker.detected_language.connect(self._on_detected_language)
            worker.source_text.connect(self._on_source_text)
            worker.target_text.connect(self._on_target_text)
            worker.archive_source_text.connect(self._on_archive_source_text)
            worker.archive_target_text.connect(self._on_archive_target_text)
            worker.spectrum.connect(self.spectrum.set_spectrum)

        self._live_runner.start(wk, connect=_connect, on_finished=self._on_live_thread_finished)

    def _stop_live(self) -> None:
        self._live_runner.stop()

    def _on_live_thread_finished(self) -> None:
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

    # ----- Rendering / saving -----

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
                except Exception:
                    pass

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
                except Exception:
                    pass

        widget.setPlainText(value)

    def _render_output(self) -> None:
        if self._render_timer.isActive():
            return
        self._render_timer.start()

    def _apply_render_output(self, *, force: bool = False) -> None:
        is_translate = self._is_translate_mode_effective()

        if force or self.tgt_text_host.isVisible() != is_translate:
            self.tgt_text_host.setVisible(is_translate)

        self.txt_src.setPlaceholderText(tr("live.placeholder.source"))
        source_text = self._current_render_source_text()
        if force or source_text != self._applied_source:
            self._set_text_edit_text(self.txt_src, source_text, force=force)
            self._applied_source = source_text

        if not is_translate:
            if force or self._applied_target:
                self._set_text_edit_text(self.txt_tgt, "", force=force)
                self._applied_target = ""
            return

        self.txt_tgt.setPlaceholderText(tr("live.placeholder.target"))
        target_text = self._current_render_target_text()
        if force or target_text != self._applied_target:
            self._set_text_edit_text(self.txt_tgt, target_text, force=force)
            self._applied_target = target_text

    def _save_transcript(self) -> None:
        if not (self._state == self.STATE_STOPPED and (not self._live_runner.is_running())):
            return

        src_text, tgt_text = self._current_session_texts()

        translate_requested = bool(tgt_text) or self._is_translate_mode_effective()
        if not (src_text or tgt_text):
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            tr("live.ctrl.save_transcript"),
            Config.transcript_filename("txt"),
            tr("live.save.file_filter"),
        )
        if not path:
            return

        try:
            TranscriptWriter.save_live_transcript(
                target_path=path,
                source_text=src_text,
                target_text=tgt_text,
                write_source_companion=bool(translate_requested),
            )
        except Exception as e:
            self._set_status("status.error")
            self.txt_src.setPlainText(tr("error.generic", detail=str(e)))

    # ----- UI state -----

    def _translation_engine_ready(self) -> bool:
        return translation_runtime_available(
            boot_ctx=self._boot_ctx,
            model_cfg=current_translation_model_cfg(),
        )

    def _translation_runtime(
        self,
        *,
        requested_enabled: bool | None = None,
        target_code: str | None = None,
    ) -> Any:
        enabled = self._translation_engine_ready() if requested_enabled is None else bool(requested_enabled)
        target = self.cmb_tgt_lang.code() if target_code is None else str(target_code or "")
        return compute_translation_runtime(
            requested_enabled=enabled,
            target_code=target,
            ui_language=Translator.current_language(),
            cfg_target=Config.translation_target_language(),
            supported=translation_language_codes(),
        )

    def _panel_control_widgets(self) -> tuple[QtWidgets.QWidget, ...]:
        return (
            self.cmb_device,
            self.tg_output_mode,
            self.tg_mode,
            self.cmb_preset,
            self.cmb_src_lang,
            self.cmb_tgt_lang,
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
        if self._live_runner.is_running():
            return Config.normalize_live_output_mode(self._session_output_mode)
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
            return "—"
        label = language_display_name(code, ui_lang=Translator.current_language())
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
            bool(self.pipe),
            bool(self._has_audio_devices),
            self._state,
            bool(self._live_runner.is_running()),
            bool(self._translation_engine_ready()),
            bool(self._is_translate_mode_effective()),
        )
        if state == self._last_availability_debug_key:
            return
        self._last_availability_debug_key = state
        _LOG.debug(
            "Live availability changed. reason=%s panel=live asr_ready=%s microphones=%s state=%s running=%s translation_available=%s translate_effective=%s",
            reason,
            bool(self.pipe),
            bool(self._has_audio_devices),
            self._state,
            bool(self._live_runner.is_running()),
            bool(self._translation_engine_ready()),
            bool(self._is_translate_mode_effective()),
        )

    def _sync_options_ui(self) -> None:
        tr_ready = self._translation_engine_ready()
        try:
            self.tg_mode.set_second_enabled(bool(tr_ready) and self._can_change_settings())
        except Exception:
            pass
        if not tr_ready and self.tg_mode.is_second_checked():
            try:
                self.tg_mode.set_first_checked(True)
            except Exception:
                pass

        self._render_output()
        self._update_buttons()
        self._log_availability_state(reason="options_synced")

    def _set_status(self, msg: str) -> None:
        key = str(msg or "").strip()
        self._status_key = key if key.startswith("status.") else ""
        if key.startswith("status."):
            self.lbl_status_value.setText(tr(key))
        else:
            self.lbl_status_value.setText(key)

    def _can_change_settings(self) -> bool:
        return (not self._live_runner.is_running()) and self._state == self.STATE_STOPPED

    def _update_save_clear_buttons(self, *, running: bool | None = None) -> None:
        if running is None:
            running = self._live_runner.is_running()

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

        if (not self._has_audio_devices) or self.pipe is None:
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
        running = self._live_runner.is_running()

        if not self._has_audio_devices:
            self.btn_refresh_devices.setEnabled(True)
            self._set_panel_controls_enabled(False)

            self._set_status("status.no_devices")
            self._sync_spectrum_state()
            return

        if self.pipe is None:
            self.btn_refresh_devices.setEnabled(True)
            self._set_panel_controls_enabled(False)

            self._set_status(tr("live.model.unavailable"))
            self._sync_spectrum_state()
            return

        can_config = self._can_change_settings()

        self.cmb_device.setEnabled(can_config)
        self.btn_refresh_devices.setEnabled(can_config)
        self.tg_output_mode.setEnabled(can_config)
        self.tg_mode.setEnabled(can_config)
        self.cmb_preset.setEnabled(can_config)

        self.cmb_src_lang.setEnabled(can_config)

        tr_ready = self._translation_engine_ready()
        if can_config:
            try:
                self.tg_mode.set_second_enabled(bool(tr_ready))
            except Exception:
                pass

        wants_translate = self._is_translate_mode_checked() and tr_ready
        self.cmb_tgt_lang.setEnabled(can_config and wants_translate)

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

        self._update_save_clear_buttons(running=running)
        self._sync_spectrum_state()
        self._log_availability_state(reason="buttons_updated")

    # ----- Worker events -----

    def _on_worker_error(self, key: str, params: dict[str, Any]) -> None:
        self._set_panel_state(self.STATE_STOPPED, status="status.error", update_buttons=False)
        _LOG.debug("Live worker error received. key=%s params=%s", key, params)
        self.spectrum.clear()
        self._sync_spectrum_state()
        dialogs.show_error(self, key=key, params=params)
        self._stop_live()
        self._update_buttons()

    def _on_status(self, msg: str) -> None:
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

    def _on_detected_language(self, lang: str) -> None:
        if not lang:
            return
        self._set_detected_language(lang)

    def _on_source_text(self, text: str) -> None:
        self._set_live_text("_display_source", text, render_mode=self.OUTPUT_MODE_STREAM)

    def _on_target_text(self, text: str) -> None:
        self._set_live_text("_display_target", text, render_mode=self.OUTPUT_MODE_STREAM)

    def _on_archive_source_text(self, text: str) -> None:
        self._set_live_text(
            "_archive_source",
            text,
            render_mode=self.OUTPUT_MODE_CUMULATIVE,
            update_save_clear=True,
        )

    def _on_archive_target_text(self, text: str) -> None:
        self._set_live_text(
            "_archive_target",
            text,
            render_mode=self.OUTPUT_MODE_CUMULATIVE,
            update_save_clear=True,
        )

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
