# ui/views/settings_panel.py
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt5 import QtWidgets, QtCore

from ui.utils.translating import tr
from ui.workers.settings_worker import SettingsWorker


class SettingsPanel(QtWidgets.QWidget):
    """
    Settings tab: simple form bound to settings.json via SettingsWorker.
    Edits only runtime sections: app, engine, model, transcription, downloader, network.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self._data: Dict[str, Any] = {}
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[SettingsWorker] = None

        root = QtWidgets.QVBoxLayout(self)

        # ---- App section ----
        grp_app = QtWidgets.QGroupBox(tr("settings.section.app"))
        lay_app = QtWidgets.QFormLayout(grp_app)

        self.ed_app_language = QtWidgets.QLineEdit()
        self.cb_app_theme = QtWidgets.QComboBox()
        self._theme_values = ["auto", "light", "dark"]
        self.cb_app_theme.addItem(tr("settings.app.theme.auto"))
        self.cb_app_theme.addItem(tr("settings.app.theme.light"))
        self.cb_app_theme.addItem(tr("settings.app.theme.dark"))

        lay_app.addRow(tr("settings.app.language.label"), self.ed_app_language)
        lay_app.addRow(tr("settings.app.theme.label"), self.cb_app_theme)

        root.addWidget(grp_app)

        # ---- Engine section ----
        grp_eng = QtWidgets.QGroupBox(tr("settings.section.engine"))
        lay_eng = QtWidgets.QFormLayout(grp_eng)

        self.cb_engine_device = QtWidgets.QComboBox()
        self._device_values = ["auto", "cpu", "gpu"]
        self.cb_engine_device.addItem(tr("settings.engine.device.auto"))
        self.cb_engine_device.addItem(tr("settings.engine.device.cpu"))
        self.cb_engine_device.addItem(tr("settings.engine.device.gpu"))

        self.cb_engine_precision = QtWidgets.QComboBox()
        self._precision_values = ["auto", "float32", "float16", "bfloat16"]
        self.cb_engine_precision.addItem(tr("settings.engine.precision.auto"))
        self.cb_engine_precision.addItem(tr("settings.engine.precision.float32"))
        self.cb_engine_precision.addItem(tr("settings.engine.precision.float16"))
        self.cb_engine_precision.addItem(tr("settings.engine.precision.bfloat16"))

        self.chk_engine_tf32 = QtWidgets.QCheckBox(tr("settings.engine.allow_tf32.label"))

        lay_eng.addRow(tr("settings.engine.device.label"), self.cb_engine_device)
        lay_eng.addRow(tr("settings.engine.precision.label"), self.cb_engine_precision)
        lay_eng.addRow("", self.chk_engine_tf32)

        root.addWidget(grp_eng)

        # ---- Model section ----
        grp_model = QtWidgets.QGroupBox(tr("settings.section.model"))
        lay_model = QtWidgets.QFormLayout(grp_model)

        self.ed_model_engine_name = QtWidgets.QLineEdit()
        self.chk_model_local_only = QtWidgets.QCheckBox(tr("settings.model.local_models_only.label"))
        self.ed_model_default_lang = QtWidgets.QLineEdit()

        self.spin_model_chunk = QtWidgets.QSpinBox()
        self.spin_model_chunk.setRange(5, 600)
        self.spin_model_stride = QtWidgets.QSpinBox()
        self.spin_model_stride.setRange(0, 120)

        self.chk_model_return_ts = QtWidgets.QCheckBox(tr("settings.model.return_timestamps.label"))
        self.chk_model_ignore_warn = QtWidgets.QCheckBox(tr("settings.model.ignore_warning.label"))
        self.chk_model_low_cpu_mem = QtWidgets.QCheckBox(tr("settings.model.low_cpu_mem_usage.label"))

        lay_model.addRow(tr("settings.model.ai_engine_name.label"), self.ed_model_engine_name)
        lay_model.addRow("", self.chk_model_local_only)
        lay_model.addRow(tr("settings.model.default_language.label"), self.ed_model_default_lang)
        lay_model.addRow(tr("settings.model.chunk_length_s.label"), self.spin_model_chunk)
        lay_model.addRow(tr("settings.model.stride_length_s.label"), self.spin_model_stride)
        lay_model.addRow("", self.chk_model_return_ts)
        lay_model.addRow("", self.chk_model_ignore_warn)
        lay_model.addRow("", self.chk_model_low_cpu_mem)

        root.addWidget(grp_model)

        # ---- Transcription section ----
        grp_tr = QtWidgets.QGroupBox(tr("settings.section.transcription"))
        lay_tr = QtWidgets.QFormLayout(grp_tr)

        self.chk_tr_ts_output = QtWidgets.QCheckBox(tr("settings.transcription.timestamps_output.label"))
        self.chk_tr_keep_downloaded = QtWidgets.QCheckBox(tr("settings.transcription.keep_downloaded_files.label"))
        self.chk_tr_keep_wav = QtWidgets.QCheckBox(tr("settings.transcription.keep_wav_temp.label"))

        lay_tr.addRow("", self.chk_tr_ts_output)
        lay_tr.addRow("", self.chk_tr_keep_downloaded)
        lay_tr.addRow("", self.chk_tr_keep_wav)

        root.addWidget(grp_tr)

        # ---- Downloader section ----
        grp_down = QtWidgets.QGroupBox(tr("settings.section.downloader"))
        lay_down = QtWidgets.QFormLayout(grp_down)

        self.spin_down_min_h = QtWidgets.QSpinBox()
        self.spin_down_min_h.setRange(1, 4320)
        self.spin_down_max_h = QtWidgets.QSpinBox()
        self.spin_down_max_h.setRange(1, 4320)

        lay_down.addRow(tr("settings.downloader.min_video_height.label"), self.spin_down_min_h)
        lay_down.addRow(tr("settings.downloader.max_video_height.label"), self.spin_down_max_h)

        root.addWidget(grp_down)

        # ---- Network section ----
        grp_net = QtWidgets.QGroupBox(tr("settings.section.network"))
        lay_net = QtWidgets.QFormLayout(grp_net)

        self.spin_net_bw = QtWidgets.QSpinBox()
        self.spin_net_bw.setRange(0, 10_000_000)

        self.spin_net_retries = QtWidgets.QSpinBox()
        self.spin_net_retries.setRange(0, 50)

        self.spin_net_frag = QtWidgets.QSpinBox()
        self.spin_net_frag.setRange(1, 32)

        self.spin_net_timeout = QtWidgets.QSpinBox()
        self.spin_net_timeout.setRange(1, 600)

        self.ed_net_proxy = QtWidgets.QLineEdit()
        self.spin_net_throttle = QtWidgets.QSpinBox()
        self.spin_net_throttle.setRange(0, 600)

        lay_net.addRow(tr("settings.network.max_bandwidth_kbps.label"), self.spin_net_bw)
        lay_net.addRow(tr("settings.network.retries.label"), self.spin_net_retries)
        lay_net.addRow(tr("settings.network.concurrent_fragments.label"), self.spin_net_frag)
        lay_net.addRow(tr("settings.network.http_timeout_s.label"), self.spin_net_timeout)
        lay_net.addRow(tr("settings.network.proxy.label"), self.ed_net_proxy)
        lay_net.addRow(tr("settings.network.throttle_startup_s.label"), self.spin_net_throttle)

        root.addWidget(grp_net)

        # ---- Bottom: info + buttons ----
        info_lbl = QtWidgets.QLabel(tr("settings.info.restart_required"))
        info_lbl.setWordWrap(True)
        root.addWidget(info_lbl)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_reload = QtWidgets.QPushButton(tr("settings.buttons.reload"))
        self.btn_save = QtWidgets.QPushButton(tr("settings.buttons.save"))
        btn_row.addWidget(self.btn_reload)
        btn_row.addWidget(self.btn_save)
        root.addLayout(btn_row)

        root.addStretch(1)

        # Store groups for enable/disable
        self._groups = [grp_app, grp_eng, grp_model, grp_tr, grp_down, grp_net]

        # Signals
        self.btn_reload.clicked.connect(self._on_reload_clicked)
        self.btn_save.clicked.connect(self._on_save_clicked)

        # Initial state
        self._set_enabled(False)
        self._start_worker(action="load")

    # ----- Worker management -----

    def _start_worker(self, *, action: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self._thread is not None:
            return

        self._thread = QtCore.QThread(self)
        self._worker = SettingsWorker(action=action, payload=payload or {})
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.settings_loaded.connect(self._on_settings_loaded)
        self._worker.saved.connect(self._on_saved)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)

        self._set_enabled(False)
        self._thread.start()

    @QtCore.pyqtSlot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_enabled(True)

    # ----- Slots from worker -----

    @QtCore.pyqtSlot(object)
    def _on_settings_loaded(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        self._data = data
        self._populate_from_data()

    @QtCore.pyqtSlot(object)
    def _on_saved(self, data: object) -> None:
        if isinstance(data, dict):
            self._data = data
            self._populate_from_data()
        QtWidgets.QMessageBox.information(self, tr("app.title"), tr("settings.msg.saved"))

    @QtCore.pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        QtWidgets.QMessageBox.critical(self, tr("app.title"), msg)

    # ----- UI helpers -----

    def _set_enabled(self, enabled: bool) -> None:
        for g in self._groups:
            g.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)
        self.btn_reload.setEnabled(enabled)

    def _populate_from_data(self) -> None:
        app = self._data.get("app", {})
        eng = self._data.get("engine", {})
        model = self._data.get("model", {})
        trc = self._data.get("transcription", {})
        down = self._data.get("downloader", {})
        net = self._data.get("network", {})

        # app
        self.ed_app_language.setText(str(app.get("language", "auto")))
        theme_val = str(app.get("theme", "auto"))
        self.cb_app_theme.setCurrentIndex(self._safe_index(self._theme_values, theme_val))

        # engine
        dev_val = str(eng.get("preferred_device", "auto"))
        self.cb_engine_device.setCurrentIndex(self._safe_index(self._device_values, dev_val))

        prec_val = str(eng.get("precision", "auto"))
        self.cb_engine_precision.setCurrentIndex(self._safe_index(self._precision_values, prec_val))

        self.chk_engine_tf32.setChecked(bool(eng.get("allow_tf32", True)))

        # model
        self.ed_model_engine_name.setText(str(model.get("ai_engine_name", "")))
        self.chk_model_local_only.setChecked(bool(model.get("local_models_only", True)))
        self.ed_model_default_lang.setText("" if model.get("default_language") is None else str(model.get("default_language")))

        self.spin_model_chunk.setValue(int(model.get("chunk_length_s", 60)))
        self.spin_model_stride.setValue(int(model.get("stride_length_s", 5)))

        self.chk_model_return_ts.setChecked(bool(model.get("return_timestamps", False)))
        self.chk_model_ignore_warn.setChecked(bool(model.get("ignore_warning", True)))
        self.chk_model_low_cpu_mem.setChecked(bool(model.get("low_cpu_mem_usage", True)))

        # transcription
        self.chk_tr_ts_output.setChecked(bool(trc.get("timestamps_output", False)))
        self.chk_tr_keep_downloaded.setChecked(bool(trc.get("keep_downloaded_files", True)))
        self.chk_tr_keep_wav.setChecked(bool(trc.get("keep_wav_temp", False)))

        # downloader
        self.spin_down_min_h.setValue(int(down.get("min_video_height", 144)))
        self.spin_down_max_h.setValue(int(down.get("max_video_height", 4320)))

        # network
        bw = net.get("max_bandwidth_kbps")
        self.spin_net_bw.setValue(int(bw) if isinstance(bw, int) and bw >= 0 else 0)
        self.spin_net_retries.setValue(int(net.get("retries", 3)))
        self.spin_net_frag.setValue(int(net.get("concurrent_fragments", 4)))
        self.spin_net_timeout.setValue(int(net.get("http_timeout_s", 30)))
        proxy = net.get("proxy", None)
        self.ed_net_proxy.setText("" if proxy is None else str(proxy))
        self.spin_net_throttle.setValue(int(net.get("throttle_startup_s", 0)))

    @staticmethod
    def _safe_index(options: list[str], value: str) -> int:
        try:
            return options.index(value)
        except ValueError:
            return 0

    # ----- Collect & actions -----

    def _collect_payload(self) -> Dict[str, Any]:
        app = {
            "language": self.ed_app_language.text().strip() or "auto",
            "theme": self._theme_values[self.cb_app_theme.currentIndex()],
        }

        eng = {
            "preferred_device": self._device_values[self.cb_engine_device.currentIndex()],
            "precision": self._precision_values[self.cb_engine_precision.currentIndex()],
            "allow_tf32": bool(self.chk_engine_tf32.isChecked()),
        }

        model = {
            "ai_engine_name": self.ed_model_engine_name.text().strip() or "whisper-turbo",
            "local_models_only": bool(self.chk_model_local_only.isChecked()),
            "chunk_length_s": int(self.spin_model_chunk.value()),
            "stride_length_s": int(self.spin_model_stride.value()),
            "pipeline_task": str(self._data.get("model", {}).get("pipeline_task", "transcribe")),
            "ignore_warning": bool(self.chk_model_ignore_warn.isChecked()),
            "default_language": (self.ed_model_default_lang.text().strip() or None),
            "return_timestamps": bool(self.chk_model_return_ts.isChecked()),
            "use_safetensors": bool(self._data.get("model", {}).get("use_safetensors", True)),
            "low_cpu_mem_usage": bool(self.chk_model_low_cpu_mem.isChecked()),
        }

        trc = {
            "timestamps_output": bool(self.chk_tr_ts_output.isChecked()),
            "keep_downloaded_files": bool(self.chk_tr_keep_downloaded.isChecked()),
            "keep_wav_temp": bool(self.chk_tr_keep_wav.isChecked()),
        }

        down = {
            "min_video_height": int(self.spin_down_min_h.value()),
            "max_video_height": int(self.spin_down_max_h.value()),
        }

        bw_val = int(self.spin_net_bw.value())
        net = {
            "max_bandwidth_kbps": None if bw_val <= 0 else bw_val,
            "retries": int(self.spin_net_retries.value()),
            "concurrent_fragments": int(self.spin_net_frag.value()),
            "http_timeout_s": int(self.spin_net_timeout.value()),
            "proxy": (self.ed_net_proxy.text().strip() or None),
            "throttle_startup_s": int(self.spin_net_throttle.value()),
        }

        return {
            "app": app,
            "engine": eng,
            "model": model,
            "transcription": trc,
            "downloader": down,
            "network": net,
        }

    @QtCore.pyqtSlot()
    def _on_reload_clicked(self) -> None:
        self._start_worker(action="load")

    @QtCore.pyqtSlot()
    def _on_save_clicked(self) -> None:
        payload = self._collect_payload()
        self._start_worker(action="save", payload=payload)

    # ----- Cleanup API for MainWindow -----

    def on_parent_close(self) -> None:
        """
        Settings operations are short; we just let worker finish.
        Nothing special to clean up here.
        """
        pass
