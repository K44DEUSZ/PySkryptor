# ui/views/settings_panel.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

import torch
from PyQt5 import QtCore, QtWidgets

from core.config.app_config import AppConfig as Config
from ui.utils.translating import tr
from ui.views import dialogs
from ui.workers.settings_worker import SettingsWorker


class _InfoButton(QtWidgets.QToolButton):
    """Small info icon used for tooltips."""

    def __init__(self, tooltip: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsPanel")
        self.setText("ⓘ")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAutoRaise(True)
        self.setFixedSize(18, 18)


class SettingsPanel(QtWidgets.QWidget):
    """
    Settings tab: form bound to settings.json via SettingsWorker.
    """

    CONTROL_HEIGHT = 24

    _RESTART_SENSITIVE_KEYS = {
        ("app", "language"),
        ("app", "theme"),
        ("engine", "preferred_device"),
        ("engine", "precision"),
        ("engine", "allow_tf32"),
        ("model", "ai_engine_name"),
    }

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self.setObjectName("SettingsPanel")

        self._data: Dict[str, Any] = {}
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[SettingsWorker] = None

        self._dirty = False
        self._blocking_updates = False

        self._pending_restart_prompt = False
        self._restore_base_snapshot: Optional[Dict[str, Any]] = None

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)

        base_h = self.CONTROL_HEIGHT

        # ----- App -----
        grp_app = QtWidgets.QGroupBox(tr("settings.section.app"))
        lay_app = QtWidgets.QFormLayout(grp_app)
        self._tune_form_layout(lay_app)

        self.cb_app_language = QtWidgets.QComboBox()
        self.cb_app_language.setMinimumHeight(base_h)

        self.cb_app_theme = QtWidgets.QComboBox()
        self.cb_app_theme.setMinimumHeight(base_h)
        self.cb_app_theme.addItem(tr("settings.app.theme.auto"), "auto")
        self.cb_app_theme.addItem(tr("settings.app.theme.light"), "light")
        self.cb_app_theme.addItem(tr("settings.app.theme.dark"), "dark")

        lay_app.addRow(
            tr("settings.app.language"),
            self._hrow(self.cb_app_language, _InfoButton(tr("settings.help.ui_language"))),
        )
        lay_app.addRow(
            tr("settings.app.theme"),
            self._hrow(self.cb_app_theme, _InfoButton(tr("settings.help.theme"))),
        )

        # ----- Engine -----
        grp_eng = QtWidgets.QGroupBox(tr("settings.section.engine"))
        lay_eng = QtWidgets.QFormLayout(grp_eng)
        self._tune_form_layout(lay_eng)

        self.cb_engine_device = QtWidgets.QComboBox()
        self.cb_engine_device.setMinimumHeight(base_h)
        self.cb_engine_device.addItem(tr("settings.engine.device.auto"), "auto")
        self.cb_engine_device.addItem(tr("settings.engine.device.cpu"), "cpu")
        self.cb_engine_device.addItem(tr("settings.engine.device.gpu"), "gpu")

        self.cb_engine_precision = QtWidgets.QComboBox()
        self.cb_engine_precision.setMinimumHeight(base_h)
        self.cb_engine_precision.addItem(tr("settings.engine.precision.auto"), "auto")
        self.cb_engine_precision.addItem(tr("settings.engine.precision.float32"), "float32")
        self.cb_engine_precision.addItem(tr("settings.engine.precision.float16"), "float16")
        self.cb_engine_precision.addItem(tr("settings.engine.precision.bfloat16"), "bfloat16")

        self._set_combo_tooltip(self.cb_engine_precision, 0, tr("settings.help.precision.auto"))
        self._set_combo_tooltip(self.cb_engine_precision, 1, tr("settings.help.precision.float32"))
        self._set_combo_tooltip(self.cb_engine_precision, 2, tr("settings.help.precision.float16"))
        self._set_combo_tooltip(self.cb_engine_precision, 3, tr("settings.help.precision.bfloat16"))

        self.chk_engine_tf32 = QtWidgets.QCheckBox(tr("settings.engine.allow_tf32"))
        self.chk_engine_tf32.setToolTip(tr("settings.help.tf32"))
        self.chk_engine_tf32.setMinimumHeight(base_h)

        lay_eng.addRow(
            tr("settings.engine.device"),
            self._hrow(self.cb_engine_device, _InfoButton(tr("settings.help.device"))),
        )
        lay_eng.addRow(
            tr("settings.engine.precision"),
            self._hrow(self.cb_engine_precision, _InfoButton(tr("settings.help.precision_hint"))),
        )
        lay_eng.addRow("", self.chk_engine_tf32)

        # ----- Model -----
        grp_model = QtWidgets.QGroupBox(tr("settings.section.model"))
        lay_model = QtWidgets.QFormLayout(grp_model)
        self._tune_form_layout(lay_model)

        self.cb_model_engine = QtWidgets.QComboBox()
        self.cb_model_engine.setMinimumHeight(base_h)

        self.spin_model_chunk = QtWidgets.QSpinBox()
        self.spin_model_chunk.setRange(5, 600)
        self.spin_model_chunk.setMinimumHeight(base_h)

        self.spin_model_stride = QtWidgets.QSpinBox()
        self.spin_model_stride.setRange(0, 120)
        self.spin_model_stride.setMinimumHeight(base_h)

        self.chk_model_ignore_warn = QtWidgets.QCheckBox(tr("settings.model.ignore_warning"))
        self.chk_model_ignore_warn.setToolTip(tr("settings.help.ignore_warning"))
        self.chk_model_ignore_warn.setMinimumHeight(base_h)

        self.ed_model_default_lang = QtWidgets.QLineEdit()
        self.ed_model_default_lang.setMinimumHeight(base_h)
        self.ed_model_default_lang.setPlaceholderText("auto")

        self.chk_model_low_cpu_mem = QtWidgets.QCheckBox(tr("settings.model.low_cpu_mem_usage"))
        self.chk_model_low_cpu_mem.setToolTip(tr("settings.help.low_cpu_mem_usage"))
        self.chk_model_low_cpu_mem.setMinimumHeight(base_h)

        lay_model.addRow(
            tr("settings.model.ai_engine_name"),
            self._hrow(self.cb_model_engine, _InfoButton(tr("settings.help.model_name"))),
        )
        lay_model.addRow(
            tr("settings.model.default_language"),
            self._hrow(self.ed_model_default_lang, _InfoButton(tr("settings.help.default_language"))),
        )
        lay_model.addRow(
            tr("settings.model.chunk_length_s"),
            self._hrow(self.spin_model_chunk, _InfoButton(tr("settings.help.chunk_length"))),
        )
        lay_model.addRow(
            tr("settings.model.stride_length_s"),
            self._hrow(self.spin_model_stride, _InfoButton(tr("settings.help.stride_length"))),
        )
        lay_model.addRow("", self.chk_model_ignore_warn)
        lay_model.addRow("", self.chk_model_low_cpu_mem)

        # ----- Transcription -----
        grp_tr = QtWidgets.QGroupBox(tr("settings.section.transcription"))
        lay_tr = QtWidgets.QFormLayout(grp_tr)
        self._tune_form_layout(lay_tr)

        self.cb_tr_output_format = QtWidgets.QComboBox()
        self.cb_tr_output_format.setMinimumHeight(base_h)
        self._output_formats = [
            ("plain_txt", tr("settings.transcription.output.plain_txt")),
            ("txt_timestamps", tr("settings.transcription.output.txt_timestamps")),
            ("srt", tr("settings.transcription.output.srt")),
        ]
        for key, label in self._output_formats:
            self.cb_tr_output_format.addItem(label, key)

        lay_tr.addRow(
            tr("settings.transcription.output_format"),
            self._hrow(self.cb_tr_output_format, _InfoButton(tr("settings.help.output_format"))),
        )

        self.chk_tr_keep_wav = QtWidgets.QCheckBox(tr("settings.transcription.keep_wav_temp"))
        self.chk_tr_keep_wav.setToolTip(tr("settings.help.keep_wav_temp"))
        self.chk_tr_keep_wav.setMinimumHeight(base_h)
        lay_tr.addRow("", self.chk_tr_keep_wav)

        self.chk_tr_audio_only = QtWidgets.QCheckBox(tr("settings.transcription.download_audio_only"))
        self.chk_tr_audio_only.setToolTip(tr("settings.help.download_audio_only"))
        self.chk_tr_audio_only.setMinimumHeight(base_h)
        lay_tr.addRow("", self.chk_tr_audio_only)

        # ----- Downloader -----
        grp_down = QtWidgets.QGroupBox(tr("settings.section.downloader"))
        lay_down = QtWidgets.QFormLayout(grp_down)
        self._tune_form_layout(lay_down)

        self.spin_down_min_h = QtWidgets.QSpinBox()
        self.spin_down_min_h.setRange(1, 4320)
        self.spin_down_min_h.setMinimumHeight(base_h)

        self.spin_down_max_h = QtWidgets.QSpinBox()
        self.spin_down_max_h.setRange(1, 4320)
        self.spin_down_max_h.setMinimumHeight(base_h)

        lay_down.addRow(
            tr("settings.downloader.min_video_height"),
            self._hrow(self.spin_down_min_h, _InfoButton(tr("settings.help.min_video_height"))),
        )
        lay_down.addRow(
            tr("settings.downloader.max_video_height"),
            self._hrow(self.spin_down_max_h, _InfoButton(tr("settings.help.max_video_height"))),
        )

        # ----- Network -----
        grp_net = QtWidgets.QGroupBox(tr("settings.section.network"))
        lay_net = QtWidgets.QFormLayout(grp_net)
        self._tune_form_layout(lay_net)

        self.spin_net_bw = QtWidgets.QSpinBox()
        self.spin_net_bw.setRange(0, 10_000_000)
        self.spin_net_bw.setMinimumHeight(base_h)

        self.spin_net_retries = QtWidgets.QSpinBox()
        self.spin_net_retries.setRange(0, 50)
        self.spin_net_retries.setMinimumHeight(base_h)

        self.spin_net_frag = QtWidgets.QSpinBox()
        self.spin_net_frag.setRange(1, 32)
        self.spin_net_frag.setMinimumHeight(base_h)

        self.spin_net_timeout = QtWidgets.QSpinBox()
        self.spin_net_timeout.setRange(1, 600)
        self.spin_net_timeout.setMinimumHeight(base_h)

        lay_net.addRow(
            tr("settings.network.max_bandwidth_kbps"),
            self._hrow(self.spin_net_bw, _InfoButton(tr("settings.help.max_bandwidth_kbps"))),
        )
        lay_net.addRow(
            tr("settings.network.retries"),
            self._hrow(self.spin_net_retries, _InfoButton(tr("settings.help.retries"))),
        )
        lay_net.addRow(
            tr("settings.network.concurrent_fragments"),
            self._hrow(self.spin_net_frag, _InfoButton(tr("settings.help.concurrent_fragments"))),
        )
        lay_net.addRow(
            tr("settings.network.http_timeout_s"),
            self._hrow(self.spin_net_timeout, _InfoButton(tr("settings.help.http_timeout_s"))),
        )

        # ----- Two-column grid -----
        grid_wrap = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(grid_wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        grid.addWidget(grp_app, 0, 0)
        grid.addWidget(grp_eng, 0, 1)
        grid.addWidget(grp_model, 1, 0)
        grid.addWidget(grp_tr, 1, 1)
        grid.addWidget(grp_down, 2, 0)
        grid.addWidget(grp_net, 2, 1)

        root.addWidget(grid_wrap, 0)

        # ----- Buttons -----
        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.setSpacing(8)
        bottom_bar.addStretch(1)

        self.btn_restore = QtWidgets.QPushButton(tr("settings.buttons.restore_defaults"))
        self.btn_save = QtWidgets.QPushButton(tr("settings.buttons.save"))

        for b in (self.btn_restore, self.btn_save):
            b.setMinimumHeight(base_h)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        right_btn_box = QtWidgets.QHBoxLayout()
        right_btn_box.setSpacing(6)
        right_btn_box.addWidget(self.btn_restore, 1)
        right_btn_box.addWidget(self.btn_save, 1)

        bottom_bar.addLayout(right_btn_box, 0)
        root.addLayout(bottom_bar)
        root.addStretch(1)

        self._groups = [grp_app, grp_eng, grp_model, grp_tr, grp_down, grp_net]

        # Signals
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        self.btn_save.clicked.connect(self._on_save_clicked)
        self.cb_engine_device.currentIndexChanged.connect(self._refresh_runtime_capabilities)

        self._install_dirty_signals()

        self._set_enabled(False)
        self._set_dirty(False)

        self._populate_model_engines()
        self._rebuild_language_list()
        self._refresh_runtime_capabilities()

        self._start_worker(action="load")

    # ----- Layout helpers -----

    @staticmethod
    def _tune_form_layout(f: QtWidgets.QFormLayout) -> None:
        f.setHorizontalSpacing(8)
        f.setVerticalSpacing(6)

    @staticmethod
    def _hrow(*widgets: QtWidgets.QWidget) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for x in widgets:
            lay.addWidget(x)
        lay.addStretch(1)
        return w

    @staticmethod
    def _set_combo_tooltip(cb: QtWidgets.QComboBox, idx: int, tooltip: str) -> None:
        cb.setItemData(idx, tooltip, QtCore.Qt.ToolTipRole)

    # ----- Dirty tracking -----

    def _install_dirty_signals(self) -> None:
        def mark() -> None:
            self._on_any_changed()

        self.cb_app_language.currentIndexChanged.connect(mark)
        self.cb_app_theme.currentIndexChanged.connect(mark)
        self.cb_engine_device.currentIndexChanged.connect(mark)
        self.cb_engine_precision.currentIndexChanged.connect(mark)
        self.cb_model_engine.currentIndexChanged.connect(mark)
        self.cb_tr_output_format.currentIndexChanged.connect(mark)

        self.ed_model_default_lang.textChanged.connect(mark)

        self.spin_model_chunk.valueChanged.connect(mark)
        self.spin_model_stride.valueChanged.connect(mark)
        self.spin_down_min_h.valueChanged.connect(mark)
        self.spin_down_max_h.valueChanged.connect(mark)
        self.spin_net_bw.valueChanged.connect(mark)
        self.spin_net_retries.valueChanged.connect(mark)
        self.spin_net_frag.valueChanged.connect(mark)
        self.spin_net_timeout.valueChanged.connect(mark)

        self.chk_engine_tf32.toggled.connect(mark)
        self.chk_model_ignore_warn.toggled.connect(mark)
        self.chk_model_low_cpu_mem.toggled.connect(mark)
        self.chk_tr_keep_wav.toggled.connect(mark)
        self.chk_tr_audio_only.toggled.connect(mark)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._refresh_save_button()

    def _on_any_changed(self) -> None:
        if self._blocking_updates:
            return
        self._set_dirty(True)

    def _refresh_save_button(self) -> None:
        enabled = self._thread is None
        self.btn_save.setEnabled(enabled and self._dirty and self._all_groups_enabled())
        self.btn_restore.setEnabled(enabled and self._all_groups_enabled())

    def _all_groups_enabled(self) -> bool:
        return all(g.isEnabled() for g in self._groups)

    # ----- Models / locales -----

    def _populate_model_engines(self) -> None:
        """
        Original methodology: list available local models by scanning MODELS_DIR.
        Avoids depending on non-existent AppConfig APIs.
        """
        self.cb_model_engine.clear()

        models_dir = getattr(Config, "MODELS_DIR", None)
        names: List[str] = []

        try:
            if isinstance(models_dir, Path) and models_dir.exists() and models_dir.is_dir():
                dirs = [d for d in models_dir.iterdir() if d.is_dir()]
                dirs = [d for d in dirs if any(d.iterdir())]
                names = [d.name for d in sorted(dirs, key=lambda x: x.name.lower())]
        except Exception:
            names = []

        if not names:
            names = ["whisper-turbo"]

        for name in names:
            self.cb_model_engine.addItem(name, name)

    def _load_locale_meta(self, path: Path) -> Tuple[str, str]:
        code = path.stem
        display = code
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = data.get("meta", {}) if isinstance(data, dict) else {}
            code = str(meta.get("language_code") or code).strip() or code
            name = str(meta.get("native_name") or meta.get("language_name") or "").strip()
            display = f"{name} ({code})" if name else code
        except Exception:
            display = code
        return code, display

    def _rebuild_language_list(self) -> None:
        self.cb_app_language.clear()
        self.cb_app_language.addItem(tr("settings.app.language.auto"), "auto")

        locales_dir = Config.LOCALES_DIR
        if not locales_dir.exists():
            return

        items: List[Tuple[str, str]] = []
        for p in sorted(locales_dir.glob("*.json")):
            code, display = self._load_locale_meta(p)
            if code.lower() == "auto":
                continue
            items.append((code, display))

        for code, display in sorted(items, key=lambda x: x[1].lower()):
            self.cb_app_language.addItem(display, code)

    # ----- Runtime capability -----

    def _refresh_runtime_capabilities(self) -> None:
        has_cuda = bool(torch.cuda.is_available())
        self._set_combo_item_enabled(self.cb_engine_device, 2, has_cuda)

        current_val = str(self.cb_engine_device.currentData() or "auto")
        if current_val == "gpu" and not has_cuda:
            self.cb_engine_device.setCurrentIndex(0)

        tf32_supported = False
        if has_cuda:
            try:
                major, _minor = torch.cuda.get_device_capability(0)
                tf32_supported = major >= 8
            except Exception:
                tf32_supported = False

        self.chk_engine_tf32.setEnabled(tf32_supported)
        if not tf32_supported:
            self.chk_engine_tf32.setChecked(False)

    @staticmethod
    def _set_combo_item_enabled(cb: QtWidgets.QComboBox, index: int, enabled: bool) -> None:
        model = cb.model()
        try:
            item = model.item(index)
            if item is not None:
                item.setEnabled(enabled)
        except Exception:
            pass

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
        self._refresh_save_button()
        self._set_enabled(True)

    @QtCore.pyqtSlot(object)
    def _on_settings_loaded(self, data: object) -> None:
        if isinstance(data, dict):
            self._data = data
            self._populate_from_data()
        self._set_dirty(False)

    @QtCore.pyqtSlot(object)
    def _on_saved(self, data: object) -> None:
        if isinstance(data, dict):
            self._data = data
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

    @QtCore.pyqtSlot(str)
    def _on_error(self, msg: str) -> None:
        QtWidgets.QMessageBox.critical(self, tr("app.title"), msg)

    # ----- UI state -----

    def _set_enabled(self, enabled: bool) -> None:
        for g in self._groups:
            g.setEnabled(enabled)
        self._refresh_save_button()

    def _populate_from_data(self) -> None:
        self._blocking_updates = True
        try:
            app = self._data.get("app", {})
            eng = self._data.get("engine", {})
            model = self._data.get("model", {})
            trc = self._data.get("transcription", {})
            down = self._data.get("downloader", {})
            net = self._data.get("network", {})

            self._select_combo_by_data(self.cb_app_language, str(app.get("language", "auto")), fallback="auto")
            self._select_combo_by_data(self.cb_app_theme, str(app.get("theme", "auto")), fallback="auto")

            self._select_combo_by_data(self.cb_engine_device, str(eng.get("preferred_device", "auto")), fallback="auto")
            self._select_combo_by_data(self.cb_engine_precision, str(eng.get("precision", "auto")), fallback="auto")
            self.chk_engine_tf32.setChecked(bool(eng.get("allow_tf32", True)))

            model_name = str(model.get("ai_engine_name", "")).strip()
            if model_name:
                self._select_combo_by_data(self.cb_model_engine, model_name, fallback=model_name)

            default_lang = model.get("default_language", None)
            self.ed_model_default_lang.setText("" if default_lang is None else str(default_lang))

            self.spin_model_chunk.setValue(int(model.get("chunk_length_s", 60)))
            self.spin_model_stride.setValue(int(model.get("stride_length_s", 5)))
            self.chk_model_ignore_warn.setChecked(bool(model.get("ignore_warning", True)))
            self.chk_model_low_cpu_mem.setChecked(bool(model.get("low_cpu_mem_usage", True)))

            timestamps_output = bool(trc.get("timestamps_output", False))
            out_ext = str(trc.get("output_ext", "txt")).lower().strip().lstrip(".") or "txt"

            fmt_key = "plain_txt"
            if out_ext == "srt":
                fmt_key = "srt"
            elif out_ext == "txt" and timestamps_output:
                fmt_key = "txt_timestamps"

            self._select_combo_by_data(self.cb_tr_output_format, fmt_key, fallback="plain_txt")
            self.chk_tr_keep_wav.setChecked(bool(trc.get("keep_wav_temp", False)))
            self.chk_tr_audio_only.setChecked(bool(trc.get("download_audio_only", True)))

            self.spin_down_min_h.setValue(int(down.get("min_video_height", 144)))
            self.spin_down_max_h.setValue(int(down.get("max_video_height", 4320)))

            bw = net.get("max_bandwidth_kbps")
            self.spin_net_bw.setValue(int(bw) if isinstance(bw, int) and bw >= 0 else 0)
            self.spin_net_retries.setValue(int(net.get("retries", 3)))
            self.spin_net_frag.setValue(int(net.get("concurrent_fragments", 4)))
            self.spin_net_timeout.setValue(int(net.get("http_timeout_s", 30)))

            self._refresh_runtime_capabilities()
        finally:
            self._blocking_updates = False

    @staticmethod
    def _select_combo_by_data(cb: QtWidgets.QComboBox, value: str, *, fallback: str) -> None:
        value = (value or "").strip() or fallback
        for i in range(cb.count()):
            if str(cb.itemData(i)) == value:
                cb.setCurrentIndex(i)
                return
        for i in range(cb.count()):
            if str(cb.itemData(i)) == fallback:
                cb.setCurrentIndex(i)
                return
        cb.setCurrentIndex(0)

    # ----- Collect & actions -----

    def _collect_payload(self) -> Dict[str, Any]:
        app = {
            "language": str(self.cb_app_language.currentData() or "auto"),
            "theme": str(self.cb_app_theme.currentData() or "auto"),
        }

        eng = {
            "preferred_device": str(self.cb_engine_device.currentData() or "auto"),
            "precision": str(self.cb_engine_precision.currentData() or "auto"),
            "allow_tf32": bool(self.chk_engine_tf32.isChecked()),
        }

        default_lang = self.ed_model_default_lang.text().strip()
        default_lang_val = None if (not default_lang or default_lang.lower() == "auto") else default_lang

        model = {
            "ai_engine_name": str(self.cb_model_engine.currentData() or "whisper-turbo"),
            "chunk_length_s": int(self.spin_model_chunk.value()),
            "stride_length_s": int(self.spin_model_stride.value()),
            "ignore_warning": bool(self.chk_model_ignore_warn.isChecked()),
            "default_language": default_lang_val,
            "low_cpu_mem_usage": bool(self.chk_model_low_cpu_mem.isChecked()),
        }

        fmt_key = str(self.cb_tr_output_format.currentData() or "plain_txt")
        timestamps_output = False
        out_ext = "txt"
        if fmt_key == "txt_timestamps":
            timestamps_output = True
            out_ext = "txt"
        elif fmt_key == "srt":
            timestamps_output = True
            out_ext = "srt"

        trc = {
            "timestamps_output": timestamps_output,
            "keep_wav_temp": bool(self.chk_tr_keep_wav.isChecked()),
            "download_audio_only": bool(self.chk_tr_audio_only.isChecked()),
            "output_ext": out_ext,
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
    def _on_restore_clicked(self) -> None:
        if not dialogs.ask_restore_defaults(self):
            return
        self._start_worker(action="restore_defaults")

    @QtCore.pyqtSlot()
    def _on_save_clicked(self) -> None:
        if not dialogs.ask_save_settings(self):
            return
        payload = self._collect_payload()
        self._pending_restart_prompt = self._compute_restart_needed_for_save(payload)
        self._start_worker(action="save", payload=payload)

    def _restart_application(self) -> None:
        try:
            import sys

            QtCore.QProcess.startDetached(sys.executable, sys.argv)
            QtWidgets.QApplication.quit()
        except Exception as ex:
            QtWidgets.QMessageBox.critical(
                self,
                tr("app.title"),
                tr("settings.msg.restart_failed", detail=str(ex)),
            )

    def _compute_restart_needed_for_save(self, payload: Dict[str, Any]) -> bool:
        current = self._data if isinstance(self._data, dict) else {}
        for section, key in self._RESTART_SENSITIVE_KEYS:
            old = (current.get(section, {}) or {}).get(key)
            new = (payload.get(section, {}) or {}).get(key)
            if old != new:
                return True
        return False
