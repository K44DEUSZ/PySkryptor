# view/views/live_panel.py
from __future__ import annotations

from typing import Optional, List

from PyQt5 import QtWidgets, QtCore

from view.utils.translating import tr, Translator
from controller.tasks.model_loader_task import ModelLoadWorker
from controller.platform.microphone import list_input_device_names
from view.views.dialogs import show_no_microphone_dialog
from controller.tasks.live_transcription_task import LiveTranscriptionWorker
from view.widgets.audio_spectrum_widget import AudioSpectrumWidget
from view.widgets.language_combo import LanguageCombo

class LivePanel(QtWidgets.QWidget):
    """Live tab: capture audio input and run streaming ASR/translation."""

    STATE_STOPPED = "stopped"
    STATE_LISTENING = "listening"
    STATE_PAUSED = "paused"

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("LivePanel")

        self.pipe = None

        self._model_thread: Optional[QtCore.QThread] = None
        self._model_worker: Optional[ModelLoadWorker] = None
        self._pending_start_after_model: bool = False

        self._live_thread: Optional[QtCore.QThread] = None
        self._live_worker: Optional[LiveTranscriptionWorker] = None

        self._state: str = self.STATE_STOPPED
        self._has_audio_devices: bool = False

        self._first_shown: bool = False
        self._warned_no_devices_for_tab: bool = False

        self._last_source: str = ""
        self._last_target: str = ""

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # =========================
        # Settings section
        # =========================
        settings_box = QtWidgets.QGroupBox(tr("live.group.settings"))
        s_lay = QtWidgets.QGridLayout(settings_box)
        s_lay.setHorizontalSpacing(12)
        s_lay.setVerticalSpacing(8)
        root.addWidget(settings_box)

        row = 0

        self.cmb_device = QtWidgets.QComboBox()
        self.cmb_device.setEditable(False)

        self.btn_refresh_devices = QtWidgets.QPushButton(tr("live.ctrl.refresh_devices"))
        self.btn_refresh_devices.clicked.connect(self._refresh_devices_clicked)

        s_lay.addWidget(QtWidgets.QLabel(tr("live.device")), row, 0)
        s_lay.addWidget(self.cmb_device, row, 1, 1, 2)
        s_lay.addWidget(self.btn_refresh_devices, row, 3)

        self.lbl_status = QtWidgets.QLabel(tr("status.idle"))
        self.lbl_status.setMinimumWidth(160)
        s_lay.addWidget(self.lbl_status, row, 4)
        row += 1

        self.mode_transcribe = QtWidgets.QRadioButton(tr("live.mode.transcribe"))
        self.mode_translate = QtWidgets.QRadioButton(tr("live.mode.translate"))
        self.mode_transcribe.setChecked(True)
        self.mode_transcribe.toggled.connect(self._update_mode_ui)

        mode_box = QtWidgets.QWidget()
        mode_lay = QtWidgets.QHBoxLayout(mode_box)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.addWidget(self.mode_transcribe)
        mode_lay.addWidget(self.mode_translate)
        mode_lay.addStretch(1)

        s_lay.addWidget(QtWidgets.QLabel(tr("live.mode")), row, 0)
        s_lay.addWidget(mode_box, row, 1, 1, 4)
        row += 1

        self.cmb_src_lang = LanguageCombo(special_first=("lang.auto_detect", ""))
        self.cmb_tgt_lang = LanguageCombo(special_first=("lang.default_ui", "auto"))
        self.cmb_tgt_lang.set_code("auto")

        self.chk_show_source = QtWidgets.QCheckBox(tr("live.show_source"))
        self.chk_show_source.setChecked(True)
        self.chk_show_source.toggled.connect(self._render_output)

        s_lay.addWidget(QtWidgets.QLabel(tr("live.source_language")), row, 0)
        s_lay.addWidget(self.cmb_src_lang, row, 1)
        s_lay.addWidget(QtWidgets.QLabel(tr("live.target_language")), row, 2)
        s_lay.addWidget(self.cmb_tgt_lang, row, 3)
        s_lay.addWidget(self.chk_show_source, row, 4)
        row += 1

        # =========================
        # Controls + Spectrum section (one common block)
        # =========================
        controls_box = QtWidgets.QGroupBox(tr("live.group.controls"))
        c_lay = QtWidgets.QVBoxLayout(controls_box)
        c_lay.setContentsMargins(12, 12, 12, 12)
        c_lay.setSpacing(10)
        root.addWidget(controls_box)

        # Spectrum row
        spectrum_row = QtWidgets.QWidget()
        sp_lay = QtWidgets.QHBoxLayout(spectrum_row)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        sp_lay.setSpacing(10)

        sp_lay.addWidget(QtWidgets.QLabel(tr("live.meter.input")))
        self.spectrum = AudioSpectrumWidget(bars=24)
        sp_lay.addWidget(self.spectrum, 1)

        c_lay.addWidget(spectrum_row)

        # Big control row: Start / Pause / Stop (equal, bigger)
        btn_big_row = QtWidgets.QWidget()
        big_lay = QtWidgets.QHBoxLayout(btn_big_row)
        big_lay.setContentsMargins(0, 0, 0, 0)
        big_lay.setSpacing(10)

        self.btn_start = QtWidgets.QPushButton(tr("live.ctrl.start"))
        self.btn_pause = QtWidgets.QPushButton(tr("live.ctrl.pause"))
        self.btn_stop = QtWidgets.QPushButton(tr("live.ctrl.stop"))

        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumHeight(46)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        self.btn_stop.clicked.connect(self._on_stop_clicked)

        big_lay.addWidget(self.btn_start)
        big_lay.addWidget(self.btn_pause)
        big_lay.addWidget(self.btn_stop)
        c_lay.addWidget(btn_big_row)

        # Save / Clear row: 2/3 width, height like refresh button
        save_clear_outer = QtWidgets.QWidget()
        out_lay = QtWidgets.QHBoxLayout(save_clear_outer)
        out_lay.setContentsMargins(0, 0, 0, 0)
        out_lay.setSpacing(0)

        save_clear_inner = QtWidgets.QWidget()
        in_lay = QtWidgets.QHBoxLayout(save_clear_inner)
        in_lay.setContentsMargins(0, 0, 0, 0)
        in_lay.setSpacing(10)

        self.btn_save = QtWidgets.QPushButton(tr("live.ctrl.save_transcript"))
        self.btn_clear = QtWidgets.QPushButton(tr("live.ctrl.clear"))

        # (1) height same as "classic" buttons like refresh_devices
        h = int(self.btn_refresh_devices.sizeHint().height())
        if h <= 0:
            h = 30
        for b in (self.btn_save, self.btn_clear):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumHeight(h)
            b.setMaximumHeight(h)

        self.btn_save.clicked.connect(self._save_transcript)
        self.btn_clear.clicked.connect(self._clear_text)

        in_lay.addWidget(self.btn_save)
        in_lay.addWidget(self.btn_clear)

        # 2/3 width using stretch factors 1 : 4 : 1
        out_lay.addStretch(1)
        out_lay.addWidget(save_clear_inner, 4)
        out_lay.addStretch(1)

        c_lay.addWidget(save_clear_outer)

        # =========================
        # Output
        # =========================
        self.txt_out = QtWidgets.QTextEdit()
        self.txt_out.setReadOnly(True)
        self.txt_out.setPlaceholderText(tr("live.placeholder.source"))
        root.addWidget(self.txt_out, 1)

        # init: do NOT show no-mic dialog here
        self._refresh_devices(show_dialog=False)
        self._update_mode_ui()
        self._update_buttons()

    # ----- Show hook (first entry into Live tab) -----

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._first_shown:
            self._first_shown = True
            # (4) show dialog only upon first entering this tab
            self._refresh_devices(show_dialog=True)

    # ----- Device management -----

    def _refresh_devices_clicked(self) -> None:
        self._refresh_devices(show_dialog=True)

    def _refresh_devices(self, *, show_dialog: bool) -> None:
        self.cmb_device.clear()

        names = list_input_device_names()
        self._has_audio_devices = bool(names)

        if not names:
            self.cmb_device.addItem(tr("live.device.none"), "")

            # Fix status when no devices
            self._set_status(tr("status.no_devices"))

            # (4) show dialog only when entering tab / user refresh / pressing start
            if show_dialog and not self._warned_no_devices_for_tab:
                self._warned_no_devices_for_tab = True
                show_no_microphone_dialog(self)

            self._update_buttons()
            return

        # devices present
        self._warned_no_devices_for_tab = False
        for name in names:
            self.cmb_device.addItem(name, name)

        if self._state == self.STATE_STOPPED and self._live_thread is None:
            self._set_status(tr("status.idle"))

        self._update_buttons()

    # ----- Model loading (auto) -----

    def _start_model_load(self) -> None:
        if self._model_thread is not None:
            return

        self._set_status(tr("live.model.loading"))

        self._model_thread = QtCore.QThread(self)
        self._model_worker = ModelLoadWorker()
        self._model_worker.moveToThread(self._model_thread)

        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.model_ready.connect(self._on_model_ready)
        self._model_worker.model_error.connect(self._on_model_error)

        self._model_worker.finished.connect(self._model_thread.quit)
        self._model_worker.finished.connect(self._model_worker.deleteLater)
        self._model_thread.finished.connect(self._model_thread.deleteLater)
        self._model_thread.finished.connect(self._on_model_thread_finished)

        self._model_thread.start()
        self._update_buttons()

    def _on_model_ready(self, pipe) -> None:
        self.pipe = pipe

        if self._pending_start_after_model:
            self._pending_start_after_model = False
            self._start_live_new_session()
            return

        if self._has_audio_devices and self._state == self.STATE_STOPPED:
            self._set_status(tr("status.idle"))

        self._update_buttons()

    def _on_model_error(self, msg: str) -> None:
        self.pipe = None
        self._pending_start_after_model = False
        self._set_status(tr("status.error"))
        self.txt_out.setPlainText(tr("log.model.error", msg=msg))
        self._update_buttons()

    def _on_model_thread_finished(self) -> None:
        self._model_thread = None
        self._model_worker = None
        self._update_buttons()

    # ----- Live controls -----

    def _on_start_clicked(self) -> None:
        if not self._has_audio_devices:
            # show dialog only on user intent
            show_no_microphone_dialog(self)
            self._set_status(tr("status.no_devices"))
            return

        if self._state == self.STATE_STOPPED:
            self._start_live_new_session()
            return

        if self._state == self.STATE_PAUSED and self._live_worker is not None:
            try:
                self._live_worker.resume()
            except Exception:
                pass
            self._state = self.STATE_LISTENING
            self._set_status(tr("status.listening"))
            self._update_buttons()

    def _on_pause_clicked(self) -> None:
        if self._state != self.STATE_LISTENING:
            return
        if self._live_worker is None:
            return

        try:
            self._live_worker.pause()
        except Exception:
            pass

        self._state = self.STATE_PAUSED
        self._set_status(tr("status.paused"))
        self._update_buttons()

    def _on_stop_clicked(self) -> None:
        self._stop_live()
        self._state = self.STATE_STOPPED
        self._set_status(tr("status.stopped"))
        self._update_buttons()

    def _start_live_new_session(self) -> None:
        if not self._has_audio_devices:
            self._set_status(tr("status.no_devices"))
            show_no_microphone_dialog(self)
            self._update_buttons()
            return

        if self._live_thread is not None:
            return

        # New session clears output
        self._clear_text()

        # Ensure model
        if self.pipe is None:
            self._pending_start_after_model = True
            self._start_model_load()
            return

        self._start_live_worker()
        self._state = self.STATE_LISTENING
        self._set_status(tr("status.listening"))
        self._update_buttons()

    def _start_live_worker(self) -> None:
        device_name = str(self.cmb_device.currentData() or "").strip()

        src_lang = self.cmb_src_lang.code() or ""
        tgt_lang = self.cmb_tgt_lang.code() or "auto"
        if tgt_lang in ("auto", "ui", "app", "default", ""):
            ui = str(Translator.current_language() or "en").split("-", 1)[0].lower().strip()
            tgt_lang = ui or "en"

        mode = "translate" if self.mode_translate.isChecked() else "transcribe"
        include_source = bool(self.chk_show_source.isChecked())

        self._live_thread = QtCore.QThread(self)
        self._live_worker = LiveTranscriptionWorker(
            pipe=self.pipe,
            device_name=device_name,
            mode=mode,
            source_language=src_lang,
            target_language=tgt_lang,
            include_source_in_translate=include_source,
        )
        self._live_worker.moveToThread(self._live_thread)

        self._live_thread.started.connect(self._live_worker.run)

        self._live_worker.log.connect(self._on_worker_log)
        self._live_worker.status.connect(self._on_status)
        self._live_worker.detected_language.connect(self._on_detected_language)
        self._live_worker.source_text.connect(self._on_source_text)
        self._live_worker.target_text.connect(self._on_target_text)
        self._live_worker.spectrum.connect(self.spectrum.set_spectrum)

        self._live_worker.finished.connect(self._live_thread.quit)
        self._live_worker.finished.connect(self._live_worker.deleteLater)
        self._live_thread.finished.connect(self._live_thread.deleteLater)
        self._live_thread.finished.connect(self._on_live_thread_finished)

        self._live_thread.start()

    def _stop_live(self) -> None:
        self._pending_start_after_model = False

        if self._live_worker is not None:
            try:
                self._live_worker.cancel()
            except Exception:
                pass
        if self._live_thread is not None:
            try:
                self._live_thread.requestInterruption()
            except Exception:
                pass

    def _on_live_thread_finished(self) -> None:
        self._live_thread = None
        self._live_worker = None

        if not self._has_audio_devices:
            self._set_status(tr("status.no_devices"))
        elif self._state == self.STATE_STOPPED:
            self._set_status(tr("status.stopped"))
        else:
            self._state = self.STATE_STOPPED
            self._set_status(tr("status.stopped"))

        self._update_buttons()

    def _clear_text(self) -> None:
        self._last_source = ""
        self._last_target = ""
        self.txt_out.clear()
        self._update_buttons()

    # ----- Rendering / saving -----

    def _render_output(self) -> None:
        is_translate = bool(self.mode_translate.isChecked())
        show_source = bool(self.chk_show_source.isChecked())

        if not is_translate:
            self.txt_out.setPlaceholderText(tr("live.placeholder.source"))
            self.txt_out.setPlainText(self._last_source or "")
            return

        self.txt_out.setPlaceholderText(tr("live.placeholder.target"))
        if show_source:
            parts: List[str] = []
            if (self._last_source or "").strip():
                parts.append("--- SOURCE ---")
                parts.append((self._last_source or "").strip())
            if (self._last_target or "").strip():
                if parts:
                    parts.append("")
                parts.append("--- TARGET ---")
                parts.append((self._last_target or "").strip())
            self.txt_out.setPlainText(("\n".join(parts)).strip() if parts else "")
        else:
            self.txt_out.setPlainText(self._last_target or "")

    def _save_transcript(self) -> None:
        if not (self._state == self.STATE_STOPPED and self._live_thread is None):
            return

        is_translate = bool(self.mode_translate.isChecked())
        show_source = bool(self.chk_show_source.isChecked())

        if not is_translate:
            content = (self._last_source or "").strip()
        else:
            if show_source:
                parts: List[str] = []
                if (self._last_source or "").strip():
                    parts.append("--- SOURCE ---")
                    parts.append((self._last_source or "").strip())
                if (self._last_target or "").strip():
                    if parts:
                        parts.append("")
                    parts.append("--- TARGET ---")
                    parts.append((self._last_target or "").strip())
                content = "\n".join(parts).strip()
            else:
                content = (self._last_target or "").strip()

        if not content:
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            tr("live.ctrl.save_transcript"),
            "transcript.txt",
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            self._set_status(tr("status.error"))
            self.txt_out.setPlainText(tr("error.generic", detail=str(e)))

    # ----- UI state -----

    def _update_mode_ui(self) -> None:
        is_translate = bool(self.mode_translate.isChecked())
        self.cmb_tgt_lang.setEnabled(is_translate and self._can_change_settings())
        self.chk_show_source.setEnabled(is_translate and self._can_change_settings())
        self._render_output()
        self._update_buttons()

    def _set_status(self, msg: str) -> None:
        self.lbl_status.setText(msg)

    def _can_change_settings(self) -> bool:
        return self._live_thread is None and self._state == self.STATE_STOPPED

    def _update_buttons(self) -> None:
        running = self._live_thread is not None

        if not self._has_audio_devices:
            self.btn_refresh_devices.setEnabled(True)

            for w in (
                self.cmb_device,
                self.mode_transcribe,
                self.mode_translate,
                self.cmb_src_lang,
                self.cmb_tgt_lang,
                self.chk_show_source,
                self.btn_start,
                self.btn_pause,
                self.btn_stop,
                self.btn_save,
                self.btn_clear,
            ):
                w.setEnabled(False)

            self._set_status(tr("status.no_devices"))
            return

        can_config = self._can_change_settings()

        self.cmb_device.setEnabled(can_config)
        self.btn_refresh_devices.setEnabled(can_config)

        self.mode_transcribe.setEnabled(can_config)
        self.mode_translate.setEnabled(can_config)

        self.cmb_src_lang.setEnabled(can_config)
        self.cmb_tgt_lang.setEnabled(can_config and self.mode_translate.isChecked())
        self.chk_show_source.setEnabled(can_config and self.mode_translate.isChecked())

        if self._state == self.STATE_STOPPED:
            self.btn_start.setEnabled(not running and self._model_thread is None)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
        elif self._state == self.STATE_LISTENING:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.btn_stop.setEnabled(True)
        else:
            self.btn_start.setEnabled(True)   # resume
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(True)

        stopped_and_ended = (self._state == self.STATE_STOPPED and not running)
        has_text = bool((self._last_source or "").strip() or (self._last_target or "").strip())

        self.btn_clear.setEnabled(stopped_and_ended)
        self.btn_save.setEnabled(stopped_and_ended and has_text)

    # ----- Worker signals -----

    def _on_worker_log(self, msg: str) -> None:
        if self._state == self.STATE_LISTENING and msg and len(msg) <= 80:
            self._set_status(msg)

    def _on_status(self, msg: str) -> None:
        if not self._has_audio_devices:
            self._set_status(tr("status.no_devices"))
            return
        if self._state == self.STATE_PAUSED:
            self._set_status(tr("status.paused"))
            return
        if self._state == self.STATE_STOPPED and (msg == tr("status.idle")):
            return
        self._set_status(msg)

    def _on_detected_language(self, lang: str) -> None:
        if lang and self._state == self.STATE_LISTENING:
            self._set_status(tr("live.detected_language", lang=lang))

    def _on_source_text(self, text: str) -> None:
        self._last_source = text or ""
        self._render_output()

    def _on_target_text(self, text: str) -> None:
        self._last_target = text or ""
        self._render_output()
