# ui/views/panels/downloader_panel.py
from __future__ import annotations

import html
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.utils.text import format_bytes, format_hms
from ui.i18n.translator import tr
from ui.workers.download_worker import DownloadWorker


class DownloaderPanel(QtWidgets.QWidget):
    """Downloader tab: probe + download."""
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        # ---------- Layout ----------
        root = QtWidgets.QVBoxLayout(self)

        url_row = QtWidgets.QHBoxLayout()
        self.ed_url = QtWidgets.QLineEdit()
        self.ed_url.setPlaceholderText(tr("down.url.placeholder"))
        self.btn_probe = QtWidgets.QPushButton(tr("down.probe"))
        self.btn_open_downloads = QtWidgets.QPushButton(tr("down.open_folder"))
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_probe)
        url_row.addWidget(self.btn_open_downloads)
        root.addLayout(url_row)

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
        root.addWidget(meta_group)

        sel_group = QtWidgets.QGroupBox(tr("down.select.title"))
        sel_layout = QtWidgets.QHBoxLayout(sel_group)
        self.cb_kind = QtWidgets.QComboBox()
        self.cb_kind.addItems([tr("down.select.type.video"), tr("down.select.type.audio")])
        self.cb_quality = QtWidgets.QComboBox()
        self.cb_ext = QtWidgets.QComboBox()
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
        root.addWidget(sel_group)

        dl_row = QtWidgets.QHBoxLayout()
        self.pb_download = QtWidgets.QProgressBar()
        self.pb_download.setRange(0, 100)
        self.pb_download.setValue(0)
        dl_row.addWidget(self.pb_download, 1)
        root.addLayout(dl_row)

        # QTextBrowser log: manual link handling; each line via cursor -> separate block
        self.down_log = QtWidgets.QTextBrowser()
        self.down_log.setOpenExternalLinks(False)
        self.down_log.setOpenLinks(False)
        self.down_log.anchorClicked.connect(lambda url: QtGui.QDesktopServices.openUrl(url))
        root.addWidget(self.down_log, 2)

        # ---------- Dynamic options derived from probe ----------
        self._vid_quals: List[str] = ["Auto", "1080p", "720p", "480p"]
        self._vid_exts: List[str] = ["mp4", "webm"]
        self._aud_quals: List[str] = ["Auto", "320k", "256k", "192k", "128k"]
        self._aud_exts: List[str] = ["m4a", "mp3"]

        # ---------- State ----------
        self._down_thread: Optional[QtCore.QThread] = None
        self._down_worker: Optional[DownloadWorker] = None
        self._down_meta: Optional[dict] = None
        self._down_running: bool = False

        # ---------- Signals ----------
        self.btn_probe.clicked.connect(self._on_probe_clicked)
        self.btn_download.clicked.connect(self._on_download_clicked)
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_quality.currentIndexChanged.connect(self._update_buttons_and_size)
        self.cb_ext.currentIndexChanged.connect(self._update_buttons_and_size)
        self.btn_open_downloads.clicked.connect(self._on_open_downloads_clicked)

        # Initialize selectors according to default kind (Video)
        self.cb_kind.setCurrentIndex(0)
        self._on_kind_changed()

    # ---------- Logging helpers ----------
    def _append_html_line(self, html_line: str) -> None:
        """Append a single HTML line as its own block (prevents anchor bleed)."""
        try:
            doc = self.down_log.document()
            cur = QtGui.QTextCursor(doc)
            cur.movePosition(QtGui.QTextCursor.End)
            cur.insertHtml(html_line)
            # Force a new paragraph so the next message is never part of the same <a>
            cur.insertBlock()
            self.down_log.setTextCursor(cur)
            self.down_log.ensureCursorVisible()
        except Exception:
            pass

    def _log(self, text: str) -> None:
        if "<a " in text:
            self._append_html_line(text)
        else:
            self._append_html_line(html.escape(str(text)))

    # ---------- Open downloads ----------
    def _on_open_downloads_clicked(self) -> None:
        try:
            Config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.DOWNLOADS_DIR)))
        except Exception as e:
            self._log(tr("down.log.error", msg=str(e)))

    # ---------- Probe ----------
    def _on_probe_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url:
            self._log(tr("down.url.placeholder"))
            return
        if self._down_running:
            self._log("ℹ️ " + tr("down.log.analyze"))
            return

        # reset UI
        self._down_meta = None
        for w in (self.lbl_service, self.lbl_title, self.lbl_duration, self.lbl_est_size):
            w.setText("-")
        self.pb_download.setValue(0)
        self.down_log.clear()
        self.btn_download.setEnabled(False)

        # thread
        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(action="probe", url=url)
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self._log)
        self._down_worker.meta_ready.connect(self._on_probe_ready)
        self._down_worker.download_error.connect(self._on_download_error)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._down_thread.start()

    def _on_probe_ready(self, meta: Dict[str, Any]) -> None:
        self._down_meta = meta or {}
        service = meta.get("extractor") or meta.get("service") or "-"
        title = meta.get("title") or "-"
        duration = meta.get("duration")
        filesize = meta.get("filesize") or meta.get("filesize_approx")
        self.lbl_service.setText(str(service))
        self.lbl_title.setText(str(title))
        self.lbl_duration.setText(format_hms(duration))
        self.lbl_est_size.setText(format_bytes(filesize) if filesize else "-")

        # --- Build dynamic options from formats ---
        fmts = meta.get("formats") or []
        vid_heights: Set[int] = set()
        aud_bitrates: Set[int] = set()
        vid_exts: Set[str] = set()
        aud_exts: Set[str] = set()

        for f in fmts:
            ext = str(f.get("ext") or "").lower()
            vcodec = f.get("vcodec")
            acodec = f.get("acodec")
            is_video = vcodec not in (None, "none")
            is_audio_only = (vcodec in (None, "none")) and acodec not in (None, "none")

            if is_video:
                h = f.get("height")
                if isinstance(h, int) and h > 0:
                    vid_heights.add(h)
                if ext:
                    vid_exts.add(ext)
            if is_audio_only:
                br = f.get("abr") or f.get("tbr")
                try:
                    if br:
                        aud_bitrates.add(int(br))
                except Exception:
                    pass
                if ext:
                    aud_exts.add(ext)

        if vid_heights:
            self._vid_quals = ["Auto"] + [f"{h}p" for h in sorted(vid_heights, reverse=True)]
        else:
            self._vid_quals = ["Auto", "1080p", "720p", "480p"]

        vexts_sorted = [e for e in ("mp4", "webm") if e in vid_exts]
        if not vexts_sorted:
            vexts_sorted = ["mp4", "webm"]
        self._vid_exts = vexts_sorted

        if aud_bitrates:
            self._aud_quals = ["Auto"] + [f"{k}k" for k in sorted(aud_bitrates, reverse=True)]
        else:
            self._aud_quals = ["Auto", "320k", "256k", "192k", "128k"]

        aexts_sorted = [e for e in ("m4a", "mp3") if e in aud_exts] or ["m4a", "mp3"]
        self._aud_exts = aexts_sorted

        self._on_kind_changed()

    # ---------- Download ----------
    def _on_download_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url or not self._down_meta:
            self._log(tr("down.url.placeholder"))
            return
        if self._down_running:
            self._log("ℹ️ " + tr("down.log.downloading"))
            return

        is_audio = (self.cb_kind.currentIndex() == 1)  # 0 = Video, 1 = Audio
        kind = "audio" if is_audio else "video"
        quality = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        self.pb_download.setValue(0)

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(
            action="download",
            url=url,
            kind=kind,
            quality=quality,
            ext=ext,
        )
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self._log)
        self._down_worker.progress_pct.connect(self.pb_download.setValue)
        self._down_worker.download_finished.connect(self._on_download_finished)
        self._down_worker.download_error.connect(self._on_download_error)
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._down_thread.start()

    def _on_download_finished(self, path: Path) -> None:
        self.pb_download.setValue(100)

        title = (self._down_meta or {}).get("title") or path.stem
        safe_title = html.escape(str(title))
        file_url = QtCore.QUrl.fromLocalFile(str(path)).toString()

        line_html = tr("down.log.downloaded", path=f"<a href='{file_url}'>{safe_title}</a>")
        self._append_html_line(line_html)
        self._update_buttons_and_size()

    def _on_download_error(self, msg: str) -> None:
        self._log(tr("down.log.error", msg=msg))
        self._update_buttons_and_size()

    def _on_down_thread_finished(self) -> None:
        self._down_thread = None
        self._down_worker = None
        self._down_running = False
        self._update_buttons_and_size()

    # ---------- Selections / UI ----------
    def _on_kind_changed(self) -> None:
        is_audio = (self.cb_kind.currentIndex() == 1)

        self.cb_quality.blockSignals(True)
        self.cb_ext.blockSignals(True)

        self.cb_quality.clear()
        self.cb_ext.clear()

        if is_audio:
            self.cb_quality.addItems(self._aud_quals)
            self.cb_ext.addItems(self._aud_exts)
        else:
            self.cb_quality.addItems(self._vid_quals)
            self.cb_ext.addItems(self._vid_exts)

        self.cb_quality.blockSignals(False)
        self.cb_ext.blockSignals(False)

        self._update_buttons_and_size()

    def _update_buttons_and_size(self) -> None:
        has_meta = self._down_meta is not None
        self.btn_download.setEnabled(bool(has_meta and not self._down_running))
        self._update_estimated_size()

    def _update_estimated_size(self) -> None:
        meta = self._down_meta or {}
        fmts = meta.get("formats") or []
        if not fmts:
            self.lbl_est_size.setText("-")
            return

        is_audio = (self.cb_kind.currentIndex() == 1)
        q = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        candidates = []
        for f in fmts:
            fext = str(f.get("ext") or "").lower()
            height = f.get("height") or 0
            abr = f.get("abr") or f.get("tbr") or 0

            is_video_fmt = f.get("vcodec") not in (None, "none")
            if is_audio and is_video_fmt:
                continue
            if (not is_audio) and (not is_video_fmt):
                continue

            if ext and fext and ext != "auto" and fext != ext:
                continue

            if q != "auto":
                try:
                    if is_audio:
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

    # ---------- Cleanup from parent ----------
    def on_parent_close(self) -> None:
        try:
            if self._down_thread and self._down_worker:
                self._down_thread.requestInterruption()
        except Exception:
            pass
