# ui/views/downloader_panel.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.utils.text import format_bytes, format_hms
from ui.i18n.translator import tr


class DownloaderPanel(QtWidgets.QWidget):
    """UI for the 'Downloader' tab, exposes simple signals."""

    # ---------- Outgoing Signals ----------
    probe_requested = QtCore.pyqtSignal(str)                      # url
    download_requested = QtCore.pyqtSignal(str, str, str, str)    # url, kind, quality, ext

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # ---------- Layout ----------
        layout = QtWidgets.QVBoxLayout(self)

        # URL row
        url_row = QtWidgets.QHBoxLayout()
        self.ed_url = QtWidgets.QLineEdit()
        self.ed_url.setPlaceholderText(tr("down.url.placeholder"))
        self.btn_probe = QtWidgets.QPushButton(tr("down.probe"))
        self.btn_open_downloads = QtWidgets.QPushButton(tr("down.open_folder"))
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_probe)
        url_row.addWidget(self.btn_open_downloads)
        layout.addLayout(url_row)

        # Meta group
        meta_group = QtWidgets.QGroupBox(tr("down.meta.title"))
        meta_form = QtWidgets.QFormLayout(meta_group)
        self.lbl_service = QtWidgets.QLabel("-")
        self.lbl_title = QtWidgets.QLabel("-")
        self.lbl_duration = QtWidgets.QLabel("-")
        self.lbl_est_size = QtWidgets.QLabel("-")
        meta_form.addRow(tr("down.meta.service"), self.lbl_service)
        meta_form.addRow(tr("down.meta.name"), self.lbl_title)
        meta_form.addRow(tr("down.meta.duration"), self.lbl_duration)
        meta_form.addRow(tr("down.meta.size"), self.lbl_est_size)
        layout.addWidget(meta_group)

        # Select group
        sel_group = QtWidgets.QGroupBox(tr("down.select.title"))
        sel_layout = QtWidgets.QHBoxLayout(sel_group)
        self.cb_kind = QtWidgets.QComboBox()
        self.cb_kind.addItems([tr("down.select.type.video"), tr("down.select.type.audio")])
        self.cb_quality = QtWidgets.QComboBox()
        self.cb_ext = QtWidgets.QComboBox()
        self.cb_quality.addItems(["Auto", "1080p", "720p", "480p"])
        self.cb_ext.addItems(["mp4", "webm", "m4a", "mp3"])
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.type")))
        sel_layout.addWidget(self.cb_kind)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.quality")))
        sel_layout.addWidget(self.cb_quality)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.ext")))
        sel_layout.addWidget(self.cb_ext)
        sel_layout.addStretch(1)
        self.btn_download = QtWidgets.QPushButton(tr("down.download"))
        self.btn_download.setEnabled(False)
        sel_layout.addWidget(self.btn_download)
        layout.addWidget(sel_group)

        # Progress + log
        dl_row = QtWidgets.QHBoxLayout()
        self.pb_download = QtWidgets.QProgressBar()
        self.pb_download.setRange(0, 100)
        self.pb_download.setValue(0)
        dl_row.addWidget(self.pb_download, 1)
        layout.addLayout(dl_row)

        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 2)

        # ---------- State ----------
        self._meta: Optional[Dict[str, Any]] = None
        self._busy = False

        # ---------- Signals ----------
        self.btn_probe.clicked.connect(self._on_probe_clicked)
        self.btn_download.clicked.connect(self._on_download_clicked)
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_quality.currentIndexChanged.connect(self._update_estimated_size)
        self.cb_ext.currentIndexChanged.connect(self._update_estimated_size)
        self.btn_open_downloads.clicked.connect(self._open_downloads)

    # ---------- Public Helpers ----------

    def set_meta(self, meta: Dict[str, Any]) -> None:
        self._meta = meta or {}
        service = meta.get("extractor") or meta.get("service") or "-"
        title = meta.get("title") or "-"
        duration = meta.get("duration")
        filesize = meta.get("filesize") or meta.get("filesize_approx")

        self.lbl_service.setText(str(service))
        self.lbl_title.setText(str(title))
        self.lbl_duration.setText(format_hms(duration))
        self.lbl_est_size.setText(format_bytes(filesize) if filesize else "-")
        self._update_buttons()
        self._update_estimated_size()

    def set_busy(self, on: bool) -> None:
        self._busy = on
        self._update_buttons()

    def set_progress(self, pct: int) -> None:
        self.pb_download.setValue(int(pct))

    def on_download_finished(self, path: Path) -> None:
        self.pb_download.setValue(100)
        self.append_log(tr("down.log.downloaded", path=str(path)))
        self._update_buttons()

    def show_error(self, msg: str) -> None:
        self.append_log(tr("down.log.error", msg=msg))
        self._update_buttons()

    def append_log(self, text: str) -> None:
        try:
            self.log.append(text)
        except Exception:
            pass

    # ---------- Internal UI Handlers ----------

    def _on_probe_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url:
            self.append_log(tr("down.url.placeholder"))
            return
        if self._busy:
            self.append_log(tr("down.log.analyze"))
            return
        self._meta = None
        self.pb_download.setValue(0)
        self.log.clear()
        self.lbl_service.setText("-")
        self.lbl_title.setText("-")
        self.lbl_duration.setText("-")
        self.lbl_est_size.setText("-")
        self._update_buttons()
        self.probe_requested.emit(url)

    def _on_download_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url or not self._meta:
            self.append_log(tr("down.url.placeholder"))
            return
        if self._busy:
            self.append_log(tr("down.log.downloading"))
            return
        kind_text = self.cb_kind.currentText().lower()
        kind = "video" if tr("down.select.type.video").lower() in kind_text else "audio"
        quality = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()
        self.pb_download.setValue(0)
        self.download_requested.emit(url, kind, quality, ext)

    def _on_kind_changed(self) -> None:
        txt = self.cb_kind.currentText().lower()
        if tr("down.select.type.audio").lower() in txt:
            self.cb_quality.clear()
            self.cb_quality.addItems(["Auto", "320k", "256k", "192k", "128k"])
            self.cb_ext.clear()
            self.cb_ext.addItems(["m4a", "mp3"])
        else:
            self.cb_quality.clear()
            self.cb_quality.addItems(["Auto", "1080p", "720p", "480p"])
            self.cb_ext.clear()
            self.cb_ext.addItems(["mp4", "webm"])
        self._update_buttons()
        self._update_estimated_size()

    def _open_downloads(self) -> None:
        try:
            Config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.DOWNLOADS_DIR)))
        except Exception as e:
            self.append_log(tr("down.log.error", msg=str(e)))

    # ---------- Enable/Disable ----------

    def _update_buttons(self) -> None:
        self.btn_download.setEnabled(bool(self._meta and not self._busy))

    # ---------- Size Estimate ----------
    def _update_estimated_size(self) -> None:
        meta = self._meta or {}
        fmts = meta.get("formats") or []
        if not fmts:
            self.lbl_est_size.setText("-")
            return
        kind = self.cb_kind.currentText().lower()
        q = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        def is_video(fmt: Dict[str, Any]) -> bool:
            return bool(fmt.get("vcodec") not in (None, "none"))

        def is_audio(fmt: Dict[str, Any]) -> bool:
            return not is_video(fmt)

        candidates = []
        for f in fmts:
            fext = str(f.get("ext") or "").lower()
            height = f.get("height") or 0
            abr = f.get("abr") or f.get("tbr") or 0
            if tr("down.select.type.audio").lower() in kind and not is_audio(f):
                continue
            if tr("down.select.type.video").lower() in kind and not is_video(f):
                continue
            if ext and fext and ext != "auto" and fext != ext:
                continue
            if q != "auto":
                try:
                    if tr("down.select.type.audio").lower() in kind:
                        want = int(q.replace("k", ""))
                        if not abr or abs(int(abr) - want) > 64:
                            continue
                    else:
                        want = int(q.replace("p", ""))
                        if not height or abs(int(height) - want) > 200:
                            continue
                except Exception:
                    pass
            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                candidates.append(int(size))

        self.lbl_est_size.setText(format_bytes(max(candidates)) if candidates else "-")
