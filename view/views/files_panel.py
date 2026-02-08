# view/views/files_panel.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt5 import QtCore, QtGui, QtWidgets

from model.config.app_config import AppConfig as Config
from model.services.settings_service import SettingsCatalog
from model.io.text import is_url
from view.utils.translating import tr, Translator
from view.utils.gui_log import QtHtmlLogSink
from view.widgets.language_combo import LanguageCombo
from view.views.dialogs import ask_cancel, ask_conflict, ask_open_transcripts_folder
from controller.tasks.metadata_task import MetadataWorker
from controller.tasks.transcription_task import TranscriptionWorker
from controller.tasks.model_loader_task import TranscriptionLoadWorker
from controller.tasks.settings_task import SettingsWorker


def _get_transcription_settings() -> Dict[str, Any]:
    snap = getattr(Config, "SETTINGS", None)
    return dict(getattr(snap, "transcription", {}) or {}) if snap is not None else {}


def _get_model_settings() -> Dict[str, Any]:
    snap = getattr(Config, "SETTINGS", None)
    return dict(getattr(snap, "model", {}) or {}) if snap is not None else {}


def _fmt_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "-"
    try:
        sec = float(sec)
    except Exception:
        return "-"
    if sec <= 0:
        return "0.0s"
    if sec < 60:
        return f"{sec:.1f}s"
    m = int(sec // 60)
    s = int(sec % 60)
    if m < 60:
        return f"{m}m {s}s"
    h = int(m // 60)
    m = int(m % 60)
    return f"{h}h {m}m {s}s"


class DropTableWidget(QtWidgets.QTableWidget):
    pathsDropped = QtCore.pyqtSignal(list)
    deletePressed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FilesPanel")
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        paths: List[str] = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                paths.append(p)
        out = [str(p) for p in dict.fromkeys(paths)]
        if out:
            self.pathsDropped.emit(out)
        e.acceptProposedAction()

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        if e.key() == QtCore.Qt.Key_Delete:
            self.deletePressed.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class FilesPanel(QtWidgets.QWidget):
    COL_CHECK = 0
    COL_NO = 1
    COL_TITLE = 2
    COL_DUR = 3
    COL_SRC = 4
    COL_LANG = 5
    COL_PATH = 6
    COL_STATUS = 7

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FilesPanel")

        self._transcribe_thread: Optional[QtCore.QThread] = None
        self._transcribe_worker: Optional[TranscriptionWorker] = None

        self._meta_thread: Optional[QtCore.QThread] = None
        self._meta_worker: Optional[MetadataWorker] = None

        self._model_thread: Optional[QtCore.QThread] = None
        self._model_worker: Optional[TranscriptionLoadWorker] = None
        self._was_cancelled: bool = False
        self._conflict_apply_all_action: Optional[str] = None
        self._conflict_apply_all_new_base: Optional[str] = None

        root = QtWidgets.QVBoxLayout(self)
        base_h = 24

        top_grid = QtWidgets.QGridLayout()
        top_grid.setHorizontalSpacing(8)
        top_grid.setVerticalSpacing(6)
        for c in range(4):
            top_grid.setColumnStretch(c, 1)

        self.src_edit = QtWidgets.QLineEdit()
        self.src_edit.setPlaceholderText(tr("files.placeholder"))
        self.src_edit.setMinimumHeight(base_h)

        self.btn_src_add = QtWidgets.QPushButton(tr("files.add"))
        self.btn_open_output = QtWidgets.QPushButton(tr("files.open_output"))
        for b in (self.btn_src_add, self.btn_open_output):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumHeight(base_h)

        top_btn_box = QtWidgets.QHBoxLayout()
        top_btn_box.setSpacing(6)
        top_btn_box.addWidget(self.btn_src_add, 1)
        top_btn_box.addWidget(self.btn_open_output, 3)

        top_grid.addWidget(self.src_edit, 0, 0, 1, 3)
        top_grid.addLayout(top_btn_box, 0, 3, 1, 1)

        self.btn_add_files = QtWidgets.QPushButton(tr("files.add_files"))
        self.btn_add_folder = QtWidgets.QPushButton(tr("files.add_folder"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))

        for b in (self.btn_add_files, self.btn_add_folder, self.btn_remove_selected, self.btn_clear_list):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumHeight(base_h)

        top_grid.addWidget(self.btn_add_files, 1, 0)
        top_grid.addWidget(self.btn_add_folder, 1, 1)
        top_grid.addWidget(self.btn_remove_selected, 1, 2)
        top_grid.addWidget(self.btn_clear_list, 1, 3)

        root.addLayout(top_grid)

        details_group = QtWidgets.QGroupBox(tr("files.details.title"))
        details_layout = QtWidgets.QVBoxLayout(details_group)

        self.tbl = DropTableWidget()
        self.tbl.setObjectName("SourcesTable")
        self.tbl.setColumnCount(8)
        self.tbl.setHorizontalHeaderLabels([
            "",
            "#",
            tr("files.details.col.name"),
            tr("files.details.col.duration"),
            tr("files.details.col.source"),
            tr("files.details.col.language"),
            tr("files.details.col.path"),
            tr("files.details.col.status"),
        ])

        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setCornerButtonEnabled(False)

        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.setTextElideMode(QtCore.Qt.ElideMiddle)

        self.tbl.itemSelectionChanged.connect(self._update_buttons)
        self.tbl.cellClicked.connect(self._on_table_cell_clicked)

        self._apply_empty_header_mode()

        details_layout.addWidget(self.tbl, 2)
        root.addWidget(details_group, 2)

        self.options_group = QtWidgets.QGroupBox(tr("files.options.title"))
        self.options_group.setObjectName("QuickOptions")
        ql = QtWidgets.QGridLayout(self.options_group)
        ql.setHorizontalSpacing(10)
        ql.setVerticalSpacing(6)
        ql.setColumnStretch(0, 1)
        ql.setColumnStretch(1, 1)

        self.lbl_mode = QtWidgets.QLabel(tr("files.options.mode.title"))
        self.rb_transcribe = QtWidgets.QRadioButton(tr("files.options.mode.transcribe"))
        self.rb_transcribe_translate = QtWidgets.QRadioButton(tr("files.options.mode.transcribe_translate"))
        self.rb_transcribe.setChecked(True)

        self._mode_group = QtWidgets.QButtonGroup(self)
        self._mode_group.addButton(self.rb_transcribe, 0)
        self._mode_group.addButton(self.rb_transcribe_translate, 1)

        mode_box = QtWidgets.QHBoxLayout()
        mode_box.setSpacing(10)
        mode_box.addWidget(self.rb_transcribe)
        mode_box.addWidget(self.rb_transcribe_translate)
        mode_box.addStretch(1)

        self.lbl_target_lang = QtWidgets.QLabel(tr("files.options.target_language.label"))
        self.cmb_target_lang = LanguageCombo(
            special_first=("lang.default_ui", "auto"),
            locale_prefix="lang.m2m100",
        )

        self.lbl_source_lang = QtWidgets.QLabel(tr("files.options.source_language.label"))
        self.cmb_source_lang = LanguageCombo(
            special_first=("lang.auto_detect", "auto"),
            locale_prefix="lang.whisper",
        )
        self.cmb_source_lang.setMinimumHeight(base_h)
        try:
            ed2 = self.cmb_source_lang.lineEdit()
            if ed2 is not None:
                ed2.setPlaceholderText(tr("files.options.source_language.placeholder"))
        except Exception:
            pass
        self.lbl_source_lang.setBuddy(self.cmb_source_lang)

        self.cmb_target_lang.setMinimumHeight(base_h)
        try:
            ed = self.cmb_target_lang.lineEdit()
            if ed is not None:
                ed.setPlaceholderText(tr("files.options.target_language.placeholder"))
        except Exception:
            pass
        self.lbl_target_lang.setBuddy(self.cmb_target_lang)

        self.lbl_output = QtWidgets.QLabel(tr("files.options.output_format.label"))
        self.opt_output = QtWidgets.QComboBox()
        self.opt_output.setObjectName("OptOutputFormat")
        self.opt_output.setMinimumHeight(base_h)
        self.lbl_output.setBuddy(self.opt_output)

        for mode in SettingsCatalog.transcription_output_modes():
            self.opt_output.addItem(
                tr(str(mode.get("tr_key", ""))),
                str(mode.get("id", "")),
            )

        self.opt_download_audio_only = QtWidgets.QCheckBox(tr("files.options.temp.download_audio_only"))
        self.opt_download_audio_only.setToolTip(tr("files.options.help.download_audio_only"))
        self.opt_download_audio_only.setMinimumHeight(base_h)

        self.chk_keep_url_audio = QtWidgets.QCheckBox(tr("files.options.temp.keep_audio"))
        self.chk_keep_url_audio.setToolTip(tr("files.options.help.keep_audio"))
        self.chk_keep_url_audio.setMinimumHeight(base_h)

        self.cmb_audio_ext = QtWidgets.QComboBox()
        self.cmb_audio_ext.setMinimumHeight(base_h)
        self.cmb_audio_ext.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.chk_keep_url_video = QtWidgets.QCheckBox(tr("files.options.temp.keep_video"))
        self.chk_keep_url_video.setToolTip(tr("files.options.help.keep_video"))
        self.chk_keep_url_video.setMinimumHeight(base_h)

        self.cmb_video_ext = QtWidgets.QComboBox()
        self.cmb_video_ext.setMinimumHeight(base_h)
        self.cmb_video_ext.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self._fill_audio_ext_combo()
        self._fill_video_ext_combo()

        self._opt_block_save = True
        try:
            tcfg = _get_transcription_settings()

            self.opt_download_audio_only.setChecked(bool(tcfg.get("download_audio_only", True)))
            self.chk_keep_url_audio.setChecked(bool(tcfg.get("url_keep_audio", False)))
            self.chk_keep_url_video.setChecked(bool(tcfg.get("url_keep_video", False)))

            aext = str(tcfg.get("url_audio_ext", "m4a") or "m4a").strip().lower().lstrip(".")
            vext = str(tcfg.get("url_video_ext", "mp4") or "mp4").strip().lower().lstrip(".")
            self._set_combo_data(self.cmb_audio_ext, aext)
            self._set_combo_data(self.cmb_video_ext, vext)

            translate_after = bool(tcfg.get("translate_after_transcription", False))
            self.rb_transcribe.setChecked(not translate_after)
            self.rb_transcribe_translate.setChecked(translate_after)

            mdl_cfg = _get_model_settings()
            tr_mdl = (mdl_cfg.get("translation_model") or {}) if isinstance(mdl_cfg.get("translation_model"), dict) else {}
            tr_eng = str(tr_mdl.get("engine_name", "none") or "none").strip().lower()
            tr_enabled = bool(tr_eng and tr_eng not in ("none", "off", "disabled"))
            self.rb_transcribe_translate.setEnabled(tr_enabled)
            if not tr_enabled:
                self.rb_transcribe.setChecked(True)

            self.cmb_target_lang.set_code("auto")
            self.cmb_source_lang.set_code("auto")

            mode_id = str(tcfg.get("output_format", "txt") or "txt").strip().lower()

            for i in range(self.opt_output.count()):
                if str(self.opt_output.itemData(i) or "").strip().lower() == mode_id:
                    self.opt_output.setCurrentIndex(i)
                    break
        except Exception:
            pass
        finally:
            self._opt_block_save = False
            self._session_target_language = str(self.cmb_target_lang.code() or "auto").strip().lower() or "auto"
            self._session_source_language = str(self.cmb_source_lang.code() or "auto").strip().lower() or "auto"

        mode_host = QtWidgets.QWidget()
        mode_lay = QtWidgets.QVBoxLayout(mode_host)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.setSpacing(4)
        mode_lay.addWidget(self.lbl_mode)
        mode_lay.addLayout(mode_box)

        lang_host = QtWidgets.QWidget()
        lang_lay = QtWidgets.QVBoxLayout(lang_host)
        lang_lay.setContentsMargins(0, 0, 0, 0)
        lang_lay.setSpacing(4)
        lang_lay.addWidget(self.lbl_target_lang)
        lang_lay.addWidget(self.cmb_target_lang)
        lang_lay.addSpacing(4)
        lang_lay.addWidget(self.lbl_source_lang)
        lang_lay.addWidget(self.cmb_source_lang)

        ql.addWidget(mode_host, 0, 0)
        ql.addWidget(lang_host, 0, 1)

        out_host = QtWidgets.QWidget()
        out_lay = QtWidgets.QVBoxLayout(out_host)
        out_lay.setContentsMargins(0, 0, 0, 0)
        out_lay.setSpacing(4)
        out_lay.addWidget(self.lbl_output)
        out_lay.addWidget(self.opt_output)

        tmp_host = QtWidgets.QWidget()
        tmp_lay = QtWidgets.QVBoxLayout(tmp_host)
        tmp_lay.setContentsMargins(0, 0, 0, 0)
        tmp_lay.setSpacing(4)
        tmp_lay.addWidget(self.opt_download_audio_only)

        ql.addWidget(out_host, 1, 0)
        ql.addWidget(tmp_host, 1, 1)

        audio_host = QtWidgets.QWidget()
        audio_lay = QtWidgets.QHBoxLayout(audio_host)
        audio_lay.setContentsMargins(0, 0, 0, 0)
        audio_lay.setSpacing(4)
        audio_lay.addWidget(self.chk_keep_url_audio, 1)
        audio_lay.addWidget(self.cmb_audio_ext, 1)

        video_host = QtWidgets.QWidget()
        video_lay = QtWidgets.QHBoxLayout(video_host)
        video_lay.setContentsMargins(0, 0, 0, 0)
        video_lay.setSpacing(4)
        video_lay.addWidget(self.chk_keep_url_video, 1)
        video_lay.addWidget(self.cmb_video_ext, 1)

        ql.addWidget(audio_host, 2, 0)
        ql.addWidget(video_host, 2, 1)

        root.addWidget(self.options_group, 0)

        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName("TranscriptionProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setMinimumHeight(base_h)

        self.btn_start = QtWidgets.QPushButton(tr("ctrl.start"))
        self.btn_cancel = QtWidgets.QPushButton(tr("ctrl.cancel"))
        for b in (self.btn_start, self.btn_cancel):
            b.setMinimumHeight(base_h)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        right_btn_box = QtWidgets.QHBoxLayout()
        right_btn_box.setSpacing(6)
        right_btn_box.addWidget(self.btn_start)
        right_btn_box.addWidget(self.btn_cancel)

        bottom_bar.addWidget(self.progress, 1)
        bottom_bar.addLayout(right_btn_box, 0)

        root.addLayout(bottom_bar)

        self.output = QtWidgets.QTextBrowser()
        self.output.setObjectName("LogOutput")
        self.output.setOpenExternalLinks(False)
        self.output.setOpenLinks(False)
        self.output.anchorClicked.connect(self._on_anchor_clicked)
        root.addWidget(self.output, 3)
        self.log = QtHtmlLogSink(self.output)

        self.pipe = None

        self._keys: set[str] = set()
        self._row_by_key: Dict[str, int] = {}
        self._transcript_by_key: Dict[str, str] = {}
        self._origin_src_by_key: Dict[str, str] = {}
        self._display_path_by_key: Dict[str, str] = {}
        self._audio_lang_by_key: Dict[str, Optional[str]] = {}

        self._session_target_language: str = "auto"
        self._session_source_language: str = "auto"

        self._opt_autosave_timer = QtCore.QTimer(self)
        self._opt_autosave_timer.setSingleShot(True)
        self._opt_autosave_timer.setInterval(1200)
        self._opt_autosave_timer.timeout.connect(self._save_quick_options)

        self._opt_save_thread: Optional[QtCore.QThread] = None
        self._opt_save_worker: Optional[SettingsWorker] = None
        self._opt_save_pending: bool = False

        self._start_model_load()

        self.btn_src_add.clicked.connect(self._on_add_clicked)
        self.src_edit.returnPressed.connect(self._on_add_clicked)

        self.btn_add_files.clicked.connect(self._on_add_files_clicked)
        self.btn_add_folder.clicked.connect(self._on_add_folder_clicked)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_clicked)
        self.btn_open_output.clicked.connect(self._open_output_folder)

        self.btn_start.clicked.connect(self._on_start_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        self.tbl.pathsDropped.connect(self._on_paths_dropped)
        self.tbl.deletePressed.connect(self._on_remove_selected)
        self.tbl.cellDoubleClicked.connect(lambda row, _col: self._open_transcript_for_row(row))

        self.rb_transcribe.toggled.connect(self._on_quick_option_changed)
        self.rb_transcribe_translate.toggled.connect(self._on_quick_option_changed)
        self.opt_download_audio_only.toggled.connect(self._on_quick_option_changed)
        self.chk_keep_url_audio.toggled.connect(self._on_quick_option_changed)
        self.chk_keep_url_video.toggled.connect(self._on_quick_option_changed)
        self.opt_output.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_audio_ext.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_video_ext.currentIndexChanged.connect(self._on_quick_option_changed)
        self.cmb_target_lang.currentTextChanged.connect(self._on_target_language_changed)
        self.cmb_source_lang.currentTextChanged.connect(self._on_source_language_changed)

        self._sync_options_ui()
        self._update_buttons()

    def _on_quick_option_changed(self, *_args) -> None:
        self._sync_options_ui()
        if getattr(self, "_opt_block_save", False):
            return
        if self._transcribe_thread is not None:
            return
        self._opt_autosave_timer.start()

    def _on_target_language_changed(self, *_args) -> None:
        self._session_target_language = str(self.cmb_target_lang.code() or "auto").strip().lower() or "auto"
        self._sync_options_ui()

    def _on_source_language_changed(self, *_args) -> None:
        self._session_source_language = str(self.cmb_source_lang.code() or "auto").strip().lower() or "auto"
        self._sync_options_ui()

    def _gather_quick_options_patch(self) -> Dict[str, Any]:
        translate_after = bool(self.rb_transcribe_translate.isChecked())

        output_format = str(self.opt_output.currentData() or "txt").strip().lower()

        audio_only = bool(self.opt_download_audio_only.isChecked())
        keep_audio = bool(self.chk_keep_url_audio.isChecked())
        keep_video = bool(self.chk_keep_url_video.isChecked()) and (not audio_only)

        aext = str(self.cmb_audio_ext.currentData() or "m4a").strip().lower().lstrip(".") or "m4a"
        vext = str(self.cmb_video_ext.currentData() or "mp4").strip().lower().lstrip(".") or "mp4"

        return {
            "translate_after_transcription": translate_after,
            "output_format": output_format,
            "download_audio_only": audio_only,
            "url_keep_audio": keep_audio,
            "url_audio_ext": aext,
            "url_keep_video": keep_video,
            "url_video_ext": vext,
        }

    def _save_quick_options(self) -> None:
        if self._transcribe_thread is not None:
            return
        if self._opt_save_thread is not None and self._opt_save_thread.isRunning():
            self._opt_save_pending = True
            self._opt_autosave_timer.start(600)
            return

        payload = {
            "transcription": self._gather_quick_options_patch(),
        }

        thread = QtCore.QThread(self)
        worker = SettingsWorker(action="save", payload=payload)
        worker.moveToThread(thread)

        self._opt_save_thread = thread
        self._opt_save_worker = worker

        worker.saved_snapshot.connect(self._on_quick_options_saved_snapshot)

        def _done() -> None:
            try:
                thread.quit()
                thread.wait(2000)
            except Exception:
                pass
            self._opt_save_thread = None
            self._opt_save_worker = None

            if self._opt_save_pending:
                self._opt_save_pending = False
                self._opt_autosave_timer.start(300)

        worker.finished.connect(_done)
        thread.started.connect(worker.run)
        thread.start()

    def _on_quick_options_saved_snapshot(self, snap: object) -> None:
        try:
            Config.update_from_snapshot(snap, sections=("transcription", "model"))  # type: ignore[arg-type]
        except Exception:
            pass

    @staticmethod
    def _set_combo_data(combo: QtWidgets.QComboBox, data: str) -> None:
        try:
            idx = combo.findData(data)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _fill_audio_ext_combo(self) -> None:
        self.cmb_audio_ext.clear()
        for ext in SettingsCatalog.download_audio_exts():
            self.cmb_audio_ext.addItem(tr(f"files.options.ext.audio.{ext}"), ext)
        self.cmb_audio_ext.setCurrentIndex(0)

    def _fill_video_ext_combo(self) -> None:
        self.cmb_video_ext.clear()
        for ext in SettingsCatalog.download_video_exts():
            self.cmb_video_ext.addItem(tr(f"files.options.ext.video.{ext}"), ext)
        self.cmb_video_ext.setCurrentIndex(0)

    def _translation_enabled(self) -> bool:
        mdl_cfg = _get_model_settings()
        cfg = (mdl_cfg.get("translation_model") or {}) if isinstance(mdl_cfg.get("translation_model"), dict) else {}
        eng = str(cfg.get("engine_name", "none") or "none").strip().lower()
        return bool(eng and eng not in ("none", "off", "disabled"))

    def _sync_options_ui(self) -> None:
        running = self._transcribe_thread is not None

        tr_enabled = self._translation_enabled()
        self.rb_transcribe_translate.setEnabled((not running) and tr_enabled)
        if not tr_enabled and self.rb_transcribe_translate.isChecked():
            self.rb_transcribe.setChecked(True)

        translate_mode = bool(self.rb_transcribe_translate.isChecked()) and tr_enabled
        self.lbl_target_lang.setEnabled((not running) and translate_mode)
        self.cmb_target_lang.setEnabled((not running) and translate_mode)

        audio_only = bool(self.opt_download_audio_only.isChecked())

        self.chk_keep_url_video.setEnabled((not running) and (not audio_only))
        self.cmb_video_ext.setEnabled((not running) and (not audio_only) and self.chk_keep_url_video.isChecked())

        self.chk_keep_url_audio.setEnabled(not running)
        self.cmb_audio_ext.setEnabled((not running) and self.chk_keep_url_audio.isChecked())

        self.opt_download_audio_only.setEnabled(not running)
        self.lbl_mode.setEnabled(not running)
        self.rb_transcribe.setEnabled(not running)
        self.lbl_output.setEnabled(not running)
        self.opt_output.setEnabled(not running)

    def _apply_empty_header_mode(self) -> None:
        header = self.tbl.horizontalHeader()

        check_w = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth) + 16
        header.setSectionResizeMode(self.COL_CHECK, QtWidgets.QHeaderView.Fixed)
        self.tbl.setColumnWidth(self.COL_CHECK, max(26, check_w))

        header.setSectionResizeMode(self.COL_NO, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_DUR, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_SRC, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_LANG, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_STATUS, QtWidgets.QHeaderView.ResizeToContents)

        header.setSectionResizeMode(self.COL_TITLE, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_PATH, QtWidgets.QHeaderView.Stretch)

    def _apply_populated_header_mode(self) -> None:
        header = self.tbl.horizontalHeader()

        check_w = self.style().pixelMetric(QtWidgets.QStyle.PM_IndicatorWidth) + 16
        header.setSectionResizeMode(self.COL_CHECK, QtWidgets.QHeaderView.Fixed)
        self.tbl.setColumnWidth(self.COL_CHECK, max(26, check_w))

        header.setSectionResizeMode(self.COL_NO, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_TITLE, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_DUR, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_SRC, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_LANG, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_STATUS, QtWidgets.QHeaderView.ResizeToContents)

    def _checkbox_at_row(self, row: int) -> Optional[QtWidgets.QCheckBox]:
        w = self.tbl.cellWidget(row, self.COL_CHECK)
        if not w:
            return None
        return w.findChild(QtWidgets.QCheckBox)

    def _make_checkbox_cell(self) -> QtWidgets.QWidget:
        host = QtWidgets.QWidget()
        host.setContentsMargins(0, 0, 0, 0)
        lay = QtWidgets.QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(QtCore.Qt.AlignCenter)
        cb = QtWidgets.QCheckBox()
        cb.setTristate(False)
        lay.addWidget(cb)
        cb.stateChanged.connect(lambda _v: self._update_buttons())
        return host

    @staticmethod
    def _normalize_lang_code(code: str | None) -> str | None:
        if not code:
            return None
        code = str(code).strip()
        if not code:
            return None
        code = code.replace("_", "-")
        parts = [p for p in code.split("-") if p]
        if not parts:
            return None
        parts[0] = parts[0].lower()
        for i in range(1, len(parts)):
            if len(parts[i]) == 2:
                parts[i] = parts[i].upper()
            else:
                parts[i] = parts[i].lower()
        return "-".join(parts)

    def _make_lang_combo(self, *, internal_key: str) -> QtWidgets.QComboBox:
        cb = QtWidgets.QComboBox()
        cb.addItem(tr("down.select.audio_track.default"))
        cb.setProperty("lang_codes", [None])
        cb.setProperty("has_choices", False)
        cb.setProperty("internal_key", internal_key)
        cb.setEnabled(False)
        cb.currentIndexChanged.connect(self._on_lang_combo_changed)
        return cb

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

        self._audio_lang_by_key[key] = code or None

    def _update_audio_tracks(self, row: int, meta: Dict[str, Any]) -> None:
        w = self.tbl.cellWidget(row, self.COL_LANG)
        if not isinstance(w, QtWidgets.QComboBox):
            return

        raw = meta.get("audio_tracks") or meta.get("audio_langs") or []
        codes: List[str] = []
        for t in raw or []:
            if not isinstance(t, dict):
                continue
            code = t.get("lang_code") or t.get("lang") or t.get("language")
            norm = self._normalize_lang_code(code)
            if norm and norm not in codes:
                codes.append(norm)

        w.blockSignals(True)
        w.clear()
        w.addItem(tr("down.select.audio_track.default"))
        lang_codes: List[Optional[str]] = [None]

        for c in codes:
            w.addItem(c)
            lang_codes.append(c)

        w.setCurrentIndex(0)
        w.blockSignals(False)

        w.setProperty("lang_codes", lang_codes)
        w.setProperty("has_choices", len(lang_codes) > 2)

        internal_key = self._internal_key_at_row(row)
        if internal_key:
            w.setProperty("internal_key", internal_key)
            self._audio_lang_by_key.setdefault(internal_key, None)

        self._update_buttons()

    def _internal_key_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_PATH)
        if not it:
            return ""
        v = it.data(QtCore.Qt.UserRole)
        if v:
            return str(v).strip()
        return (it.text() or "").strip()

    def _display_path_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_PATH)
        return (it.text() or "").strip() if it else ""

    def _src_label_at_row(self, row: int) -> str:
        it = self.tbl.item(row, self.COL_SRC)
        return (it.text() or "").strip() if it else ""

    def _reset_url_rows_to_original_keys(self) -> None:
        for r in range(self.tbl.rowCount()):
            if self._src_label_at_row(r) != "URL":
                continue

            it_path = self.tbl.item(r, self.COL_PATH)
            if not it_path:
                continue

            display_url = (it_path.text() or "").strip()
            if not display_url:
                continue

            current_internal = self._internal_key_at_row(r)
            if current_internal == display_url:
                it_path.setData(QtCore.Qt.UserRole, display_url)
                self._display_path_by_key[display_url] = display_url
                self._origin_src_by_key[display_url] = "URL"
                self._row_by_key[display_url] = r
                w = self.tbl.cellWidget(r, self.COL_LANG)
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
            self._origin_src_by_key[new_key] = "URL"

            if old_key in self._display_path_by_key:
                self._display_path_by_key.pop(old_key, None)
            self._display_path_by_key[new_key] = new_key

            if old_key in self._audio_lang_by_key:
                self._audio_lang_by_key[new_key] = self._audio_lang_by_key.pop(old_key)

            w = self.tbl.cellWidget(r, self.COL_LANG)
            if isinstance(w, QtWidgets.QComboBox):
                w.setProperty("internal_key", new_key)

            self._transcript_by_key.pop(old_key, None)

            it_path.setData(QtCore.Qt.UserRole, new_key)
            it_path.setToolTip(new_key)
            it_path.setText(new_key)

    @QtCore.pyqtSlot(int)
    def _on_global_progress(self, value: int) -> None:
        if self._was_cancelled:
            return
        self.progress.setValue(int(value))

    @QtCore.pyqtSlot(int, int)
    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if row < 0:
            return

        mods = QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.ControlModifier):
            return

        if col not in (self.COL_SRC, self.COL_PATH):
            return

        target = self._display_path_at_row(row)
        if not target:
            return

        try:
            if is_url(target):
                if "://" not in target:
                    target = "https://" + target
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(target))
                return

            p = Path(target)
            if not p.exists():
                return

            if os.name == "nt":
                import subprocess
                if p.is_file():
                    subprocess.Popen(["explorer", "/select,", str(p)])
                else:
                    os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(str(p.parent if p.is_file() else p))
                )
        except Exception:
            pass

    def get_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for r in range(self.tbl.rowCount()):
            key = self._internal_key_at_row(r)
            if not key:
                continue

            if is_url(key):
                payload: Dict[str, Any] = {"type": "url", "value": key}
                lang = self._audio_lang_by_key.get(key)
                if lang:
                    payload["audio_lang"] = lang
                entries.append(payload)
            else:
                entries.append({"type": "file", "value": key})
        return entries

    def _normalize_key(self, raw: str) -> str:
        return (raw or "").strip()

    def _try_add_key(self, key: str) -> bool:
        key = self._normalize_key(key)
        if not key:
            return False
        if key in self._keys:
            self.log.warn(f"{tr('status.skipped')} - already in list")
            return False
        self._keys.add(key)
        return True

    def _insert_placeholder_row(self, key: str, *, src_label: str) -> None:
        if self.tbl.rowCount() == 0:
            self._apply_populated_header_mode()

        row = self.tbl.rowCount()
        self.tbl.insertRow(row)

        self.tbl.setCellWidget(row, self.COL_CHECK, self._make_checkbox_cell())

        it_no = QtWidgets.QTableWidgetItem(str(row + 1))
        it_no.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_NO, it_no)

        it_title = QtWidgets.QTableWidgetItem("...")
        self.tbl.setItem(row, self.COL_TITLE, it_title)

        it_dur = QtWidgets.QTableWidgetItem("-")
        it_dur.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_DUR, it_dur)

        it_src = QtWidgets.QTableWidgetItem(src_label)
        it_src.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_SRC, it_src)
        it_src.setToolTip(src_label)

        lang_cb = self._make_lang_combo(internal_key=key)
        self.tbl.setCellWidget(row, self.COL_LANG, lang_cb)
        self._audio_lang_by_key.setdefault(key, None)

        it_path = QtWidgets.QTableWidgetItem(key)
        it_path.setToolTip(key)
        it_path.setData(QtCore.Qt.UserRole, key)
        self.tbl.setItem(row, self.COL_PATH, it_path)

        it_status = QtWidgets.QTableWidgetItem("-")
        it_status.setTextAlignment(QtCore.Qt.AlignCenter)
        self.tbl.setItem(row, self.COL_STATUS, it_status)

        self._row_by_key[key] = row
        self._origin_src_by_key[key] = src_label
        self._display_path_by_key[key] = key

    def _update_row_from_meta(self, row: int, meta: Dict[str, Any]) -> None:
        if row < 0 or row >= self.tbl.rowCount():
            return

        title = str(meta.get("name") or meta.get("title") or "-")
        duration = meta.get("duration")

        self.tbl.item(row, self.COL_TITLE).setText(title)
        self.tbl.item(row, self.COL_DUR).setText(_fmt_seconds(duration))
        self._update_audio_tracks(row, meta)

    def _start_metadata_for(self, keys: List[str]) -> None:
        if not keys:
            return
        if self._meta_thread is not None:
            try:
                self._meta_thread.requestInterruption()
            except Exception:
                pass

        entries = []
        for k in keys:
            entries.append({"type": ("url" if is_url(k) else "file"), "value": k})

        self._meta_thread = QtCore.QThread(self)
        self._meta_worker = MetadataWorker(entries)
        self._meta_worker.moveToThread(self._meta_thread)

        self._meta_thread.started.connect(self._meta_worker.run)
        self._meta_worker.progress_log.connect(self.log.plain)
        self._meta_worker.table_ready.connect(self._on_meta_rows_ready)

        self._meta_worker.finished.connect(self._meta_thread.quit)
        self._meta_worker.finished.connect(self._meta_worker.deleteLater)
        self._meta_thread.finished.connect(self._meta_thread.deleteLater)
        self._meta_thread.finished.connect(self._on_meta_finished)

        self._meta_thread.start()

    @QtCore.pyqtSlot(list)
    def _on_meta_rows_ready(self, batch: List[Dict[str, Any]]) -> None:
        for meta in batch:
            key = str(meta.get("path") or "").strip()
            if not key:
                continue
            row = self._row_by_key.get(key)
            if row is None:
                continue
            self._update_row_from_meta(row, meta)

    def _on_meta_finished(self) -> None:
        self._meta_thread = None
        self._meta_worker = None

    def _on_add_clicked(self) -> None:
        key = self._normalize_key(self.src_edit.text())
        if not key:
            return

        if is_url(key):
            if not self._try_add_key(key):
                return
            self._insert_placeholder_row(key, src_label="URL")
            self._start_metadata_for([key])
        else:
            p = Path(key)
            if not p.exists() or not p.is_file():
                self.log.warn(tr("log.add.missing"))
                return
            key = str(p)
            if not self._try_add_key(key):
                return
            self._insert_placeholder_row(key, src_label="LOCAL")
            self._start_metadata_for([key])

        self.src_edit.clear()
        self._refresh_order_numbers()
        self._update_buttons()

    def _on_paths_dropped(self, paths: List[str]) -> None:
        exts = {e.lower() for e in Config.SUPPORTED_MEDIA_EXTS}
        added: List[str] = []

        def _add_file(fp: Path) -> None:
            if not fp.exists() or not fp.is_file():
                return
            if fp.suffix.lower() not in exts:
                return
            key = str(fp)
            if not self._try_add_key(key):
                return
            self._insert_placeholder_row(key, src_label="LOCAL")
            added.append(key)

        for p in paths:
            p = self._normalize_key(p)
            if not p:
                continue
            pp = Path(p)
            if not pp.exists():
                continue

            if pp.is_dir():
                for fp in pp.rglob("*"):
                    if fp.is_file():
                        _add_file(fp)
                continue

            _add_file(pp)

        if added:
            self._start_metadata_for(added[:30])
        self._refresh_order_numbers()
        self._update_buttons()

    def _checked_rows(self) -> List[int]:
        rows: List[int] = []
        for r in range(self.tbl.rowCount()):
            cb = self._checkbox_at_row(r)
            if cb and cb.isChecked():
                rows.append(r)
        return rows

    def _selected_rows(self) -> List[int]:
        rows: List[int] = []
        sel = self.tbl.selectionModel()
        if not sel:
            return rows
        for idx in sel.selectedRows():
            rows.append(idx.row())
        return rows

    def _remove_rows(self, rows: List[int]) -> None:
        if not rows:
            return
        for r in sorted(set(rows), reverse=True):
            key = self._internal_key_at_row(r)
            if key:
                self._keys.discard(key)
                self._row_by_key.pop(key, None)
                self._transcript_by_key.pop(key, None)
                self._origin_src_by_key.pop(key, None)
                self._display_path_by_key.pop(key, None)
                self._audio_lang_by_key.pop(key, None)
            self.tbl.removeRow(r)

        if self.tbl.rowCount() == 0:
            self._apply_empty_header_mode()

        self._refresh_order_numbers()
        self._update_buttons()

    def _refresh_order_numbers(self) -> None:
        for r in range(self.tbl.rowCount()):
            it = self.tbl.item(r, self.COL_NO)
            if it:
                it.setText(str(r + 1))

    def _open_transcript_for_row(self, row: int) -> None:
        key = self._internal_key_at_row(row)
        if not key:
            return
        path = self._transcript_by_key.get(key)
        if not path:
            return
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(path))))
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

        exts = {e.lower() for e in Config.SUPPORTED_MEDIA_EXTS}
        files: List[str] = []
        for fp in p.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in exts:
                files.append(str(fp))
        self._on_paths_dropped(files)

    def _on_remove_selected(self) -> None:
        rows = self._checked_rows() or self._selected_rows()
        self._remove_rows(rows)

    def _on_clear_clicked(self) -> None:
        self._keys.clear()
        self._row_by_key.clear()
        self._transcript_by_key.clear()
        self._origin_src_by_key.clear()
        self._display_path_by_key.clear()
        self._audio_lang_by_key.clear()
        self.tbl.setRowCount(0)
        self.progress.setValue(0)
        self._apply_empty_header_mode()
        self._update_buttons()

    def _open_output_folder(self) -> None:
        try:
            out_dir = Config.TRANSCRIPTIONS_DIR
            out_dir.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(out_dir))  # type: ignore[attr-defined]
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out_dir)))
        except Exception as e:
            self.log.err(tr("log.unexpected", msg=str(e)))

    def _on_start_clicked(self) -> None:
        if self._transcribe_worker is not None or self._transcribe_thread is not None:
            return

        if not self.pipe:
            self.log.info(tr("log.pipe_not_ready"))
            return

        self._reset_url_rows_to_original_keys()

        entries = self.get_entries()
        if not entries:
            self.log.info(tr("log.no_items"))
            return

        self.log.clear()
        self.progress.setValue(0)

        self._was_cancelled = False
        self._conflict_apply_all_action = None
        self._conflict_apply_all_new_base = None

        self._transcribe_thread = QtCore.QThread(self)
        overrides = {
            "target_language": str(self._session_target_language or "auto").strip().lower() or "auto",
            "source_language": str(self._session_source_language or "auto").strip().lower() or "auto",
        }
        self._transcribe_worker = TranscriptionWorker(pipe=self.pipe, entries=entries, overrides=overrides)
        self._transcribe_worker.moveToThread(self._transcribe_thread)
        self._transcribe_thread.started.connect(self._transcribe_worker.run)

        self._transcribe_worker.log.connect(self.log.plain)
        self._transcribe_worker.progress.connect(self._on_global_progress)
        self._transcribe_worker.item_status.connect(self._on_item_status)
        self._transcribe_worker.item_progress.connect(self._on_item_progress)
        self._transcribe_worker.item_path_update.connect(self._on_item_path_update)
        self._transcribe_worker.transcript_ready.connect(self._on_transcript_ready)
        self._transcribe_worker.conflict_check.connect(self._on_conflict_check)
        self._transcribe_worker.session_done.connect(self._on_session_done)

        self._transcribe_worker.finished.connect(self._transcribe_thread.quit)
        self._transcribe_worker.finished.connect(self._transcribe_worker.deleteLater)
        self._transcribe_thread.finished.connect(self._transcribe_thread.deleteLater)
        self._transcribe_thread.finished.connect(self._on_transcribe_finished)

        self._update_buttons()
        self._transcribe_thread.start()

    def _on_cancel_clicked(self) -> None:
        if not self._transcribe_worker:
            return
        if not ask_cancel(self):
            return

        self._was_cancelled = True
        self.progress.setValue(0)

        try:
            if self._transcribe_thread is not None:
                self._transcribe_thread.requestInterruption()
            self._transcribe_worker.cancel()
        except Exception:
            pass

        for r in range(self.tbl.rowCount()):
            key = self._internal_key_at_row(r)
            finished = bool(key and self._transcript_by_key.get(key))
            if not finished:
                it = self.tbl.item(r, self.COL_STATUS)
                if it:
                    it.setText("-")

        self._reset_url_rows_to_original_keys()
        self.log.warn(tr("log.cancelled"))

    def _on_transcribe_finished(self) -> None:
        if self._was_cancelled:
            self.progress.setValue(0)
        self._transcribe_thread = None
        self._transcribe_worker = None
        self._update_buttons()

    @QtCore.pyqtSlot(str, bool, bool, bool)
    def _on_session_done(self, session_dir: str, processed_any: bool, had_errors: bool, was_cancelled: bool) -> None:
        if not processed_any or had_errors or was_cancelled:
            return
        try:
            if not ask_open_transcripts_folder(self, session_dir):
                return
            p = Path(session_dir)
            p.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))
        except Exception:
            pass

    @QtCore.pyqtSlot(str, str)
    def _on_item_status(self, key: str, status: str) -> None:
        if self._was_cancelled:
            return
        row = self._row_by_key.get(key)
        if row is None:
            return
        it = self.tbl.item(row, self.COL_STATUS)
        if it:
            it.setText(status)

    @QtCore.pyqtSlot(str, int)
    def _on_item_progress(self, key: str, pct: int) -> None:
        if self._was_cancelled:
            return
        row = self._row_by_key.get(key)
        if row is None:
            return
        if pct <= 0 or pct >= 100:
            return
        it = self.tbl.item(row, self.COL_STATUS)
        if it:
            it.setText(tr("status.proc"))

    @QtCore.pyqtSlot(str, str)
    def _on_item_path_update(self, old_key: str, new_key: str) -> None:
        row = self._row_by_key.pop(old_key, None)
        if row is None:
            return

        display = self._display_path_by_key.get(old_key, old_key)
        src_label = self._origin_src_by_key.get(old_key)

        self._keys.discard(old_key)
        self._keys.add(new_key)
        self._row_by_key[new_key] = row

        if old_key in self._transcript_by_key:
            self._transcript_by_key[new_key] = self._transcript_by_key.pop(old_key)

        if old_key in self._audio_lang_by_key:
            self._audio_lang_by_key[new_key] = self._audio_lang_by_key.pop(old_key)

        if src_label:
            self._origin_src_by_key[new_key] = src_label
        self._origin_src_by_key.pop(old_key, None)

        self._display_path_by_key[new_key] = display
        self._display_path_by_key.pop(old_key, None)

        it_path = self.tbl.item(row, self.COL_PATH)
        if it_path:
            it_path.setData(QtCore.Qt.UserRole, new_key)
            it_path.setText(display)
            it_path.setToolTip(display)

        w = self.tbl.cellWidget(row, self.COL_LANG)
        if isinstance(w, QtWidgets.QComboBox):
            w.setProperty("internal_key", new_key)

        self._start_metadata_for([new_key])

    @QtCore.pyqtSlot(str, str)
    def _on_transcript_ready(self, key: str, transcript_path: str) -> None:
        self._transcript_by_key[key] = transcript_path
        self.log.line_with_link(
            tr("log.transcript.saved_prefix"),
            Path(transcript_path),
            title=Path(transcript_path).name,
        )

    @QtCore.pyqtSlot(str, str)
    def _on_conflict_check(self, stem: str, existing_dir: str) -> None:
        if self._was_cancelled:
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")
            return

        try:
            if self._conflict_apply_all_action:
                action = self._conflict_apply_all_action
                new_stem = self._conflict_apply_all_new_base or stem if action == "new" else ""
                if self._transcribe_worker is not None:
                    self._transcribe_worker.on_conflict_decided(action, new_stem)
                return

            action, new_stem, apply_all = ask_conflict(self, stem)
            if apply_all and action != "new":
                self._conflict_apply_all_action = action
                self._conflict_apply_all_new_base = None

            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided(action, new_stem)
        except Exception:
            if self._transcribe_worker is not None:
                self._transcribe_worker.on_conflict_decided("skip", "")

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        try:
            u = url.toString()
            if not u:
                return
            if u.startswith("file://"):
                p = u.replace("file://", "", 1)
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(p))))
                return
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(u))
        except Exception:
            pass

    def _start_model_load(self) -> None:
        if self._model_thread is not None:
            return

        self._model_thread = QtCore.QThread(self)
        self._model_worker = TranscriptionLoadWorker()
        self._model_worker.moveToThread(self._model_thread)

        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.model_ready.connect(self._on_model_ready)
        self._model_worker.model_error.connect(self._on_model_error)

        self._model_worker.finished.connect(self._model_thread.quit)
        self._model_worker.finished.connect(self._model_worker.deleteLater)
        self._model_thread.finished.connect(self._model_thread.deleteLater)
        self._model_thread.finished.connect(self._on_model_thread_finished)

        self._model_thread.start()

    def _on_model_ready(self, pipe) -> None:
        self.pipe = pipe
        self.log.ok(tr("log.model.ready", device=Config.DEVICE_FRIENDLY_NAME))
        self._update_buttons()

    def _on_model_error(self, msg: str) -> None:
        self.pipe = None
        self.log.err(tr("log.model.error", msg=msg))
        self._update_buttons()

    def _on_model_thread_finished(self) -> None:
        self._model_thread = None
        self._model_worker = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_items = self.tbl.rowCount() > 0
        has_sel = bool(self._checked_rows() or self._selected_rows())
        model_ready = self.pipe is not None
        running = self._transcribe_thread is not None

        self.src_edit.setEnabled(not running)

        self.btn_start.setEnabled(has_items and model_ready and not running)
        self.btn_cancel.setEnabled(running)

        self.btn_clear_list.setEnabled(has_items and not running)
        self.btn_remove_selected.setEnabled(has_sel and not running)

        self.btn_src_add.setEnabled(not running)
        self.btn_open_output.setEnabled(True)

        self.btn_add_files.setEnabled(not running)
        self.btn_add_folder.setEnabled(not running)

        for r in range(self.tbl.rowCount()):
            w = self.tbl.cellWidget(r, self.COL_LANG)
            if isinstance(w, QtWidgets.QComboBox):
                can_choose = bool(w.property("has_choices"))
                w.setEnabled(bool(can_choose and (not running)))

        self._sync_options_ui()

    def on_parent_close(self) -> None:
        try:
            if self._transcribe_thread and self._transcribe_worker:
                self._transcribe_worker.cancel()
                self._transcribe_thread.requestInterruption()
        except Exception:
            pass

        try:
            if self._meta_thread and self._meta_worker:
                self._meta_thread.requestInterruption()
        except Exception:
            pass
