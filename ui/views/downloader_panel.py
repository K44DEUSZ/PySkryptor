# ui/views/downloader_panel.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.io.text import format_bytes, format_hms
from ui.utils.translating import tr
from ui.utils.logging import QtHtmlLogSink
from ui.views.dialogs import ask_download_duplicate, info_playlist_not_supported
from ui.workers.download_worker import DownloadWorker


class DownloaderPanel(QtWidgets.QWidget):
    """Downloader tab UI and control logic."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)

        url_row = QtWidgets.QHBoxLayout()
        self.ed_url = QtWidgets.QLineEdit()
        self.ed_url.setPlaceholderText(tr("down.url.placeholder"))

        self.btn_probe = QtWidgets.QPushButton(tr("down.probe"))
        self.btn_open_in_browser = QtWidgets.QPushButton(tr("down.open_in_browser"))
        self.btn_open_downloads = QtWidgets.QPushButton(tr("down.open_folder"))

        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_probe)
        url_row.addWidget(self.btn_open_in_browser)
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
        self.cb_audio = QtWidgets.QComboBox()

        self._vid_quals: List[str] = ["Auto", "1080p", "720p", "480p"]
        self._aud_quals: List[str] = ["Auto", "320k", "256k", "192k", "128k"]

        down_vid_ext = [
            e.lstrip(".") for e in getattr(Config, "downloader_video_extensions", lambda: ())()
        ]
        down_aud_ext = [
            e.lstrip(".") for e in getattr(Config, "downloader_audio_extensions", lambda: ())()
        ]
        self._vid_exts: List[str] = down_vid_ext or ["mp4", "webm"]
        self._aud_exts: List[str] = down_aud_ext or ["m4a", "mp3"]

        self.cb_quality.addItems(self._vid_quals)
        self.cb_ext.addItems(self._vid_exts)

        self._audio_lang_codes: List[Optional[str]] = [None]
        self.cb_audio.addItem(tr("down.select.audio_track.default"))
        self.cb_audio.setEnabled(False)

        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.type")))
        sel_layout.addWidget(self.cb_kind)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.quality")))
        sel_layout.addWidget(self.cb_quality)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.ext")))
        sel_layout.addWidget(self.cb_ext)
        sel_layout.addSpacing(8)
        sel_layout.addWidget(QtWidgets.QLabel(tr("down.select.audio_track")))
        sel_layout.addWidget(self.cb_audio)
        sel_layout.addStretch(1)
        root.addWidget(sel_group)

        dl_row = QtWidgets.QHBoxLayout()
        self.pb_download = QtWidgets.QProgressBar()
        self.pb_download.setRange(0, 100)
        self.pb_download.setValue(0)
        dl_row.addWidget(self.pb_download, 1)

        self.btn_download = QtWidgets.QPushButton(tr("down.download"))
        self.btn_download.setEnabled(False)
        dl_row.addWidget(self.btn_download)

        self.btn_cancel = QtWidgets.QPushButton(tr("ctrl.cancel"))
        self.btn_cancel.setEnabled(False)
        dl_row.addWidget(self.btn_cancel)
        root.addLayout(dl_row)

        self.down_log = QtWidgets.QTextBrowser()
        self.down_log.setOpenExternalLinks(False)
        self.down_log.setOpenLinks(False)
        self.down_log.anchorClicked.connect(self._on_anchor_clicked)
        root.addWidget(self.down_log, 2)

        self.log = QtHtmlLogSink(self.down_log)

        self._down_thread: Optional[QtCore.QThread] = None
        self._down_worker: Optional[DownloadWorker] = None
        self._down_meta: Optional[dict] = None
        self._down_running: bool = False
        self._download_aborted: bool = False

        self.btn_probe.clicked.connect(self._on_probe_clicked)
        self.btn_download.clicked.connect(self._on_download_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)

        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_quality.currentIndexChanged.connect(self._update_buttons_and_size)
        self.cb_ext.currentIndexChanged.connect(self._update_buttons_and_size)
        self.cb_audio.currentIndexChanged.connect(self._update_buttons_and_size)

        self.btn_open_downloads.clicked.connect(self._on_open_downloads_clicked)
        self.btn_open_in_browser.clicked.connect(self._on_open_in_browser_clicked)
        self.ed_url.textChanged.connect(self._update_open_in_browser_state)

        self._reset_meta_ui()
        self._update_open_in_browser_state()
        self._update_inputs_state()

    def _on_anchor_clicked(self, url: QtCore.QUrl) -> None:
        try:
            if url.isLocalFile():
                QtGui.QDesktopServices.openUrl(url)
        except Exception:
            pass

    def _on_open_downloads_clicked(self) -> None:
        try:
            Config.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Config.DOWNLOADS_DIR)))
        except Exception as e:
            self.log.err(tr("down.log.error", msg=str(e)))

    def _update_open_in_browser_state(self) -> None:
        url = (self.ed_url.text() or "").strip()
        self.btn_open_in_browser.setEnabled(bool(url))

    def _on_open_in_browser_clicked(self) -> None:
        url = (self.ed_url.text() or "").strip()
        if not url:
            return
        if "://" not in url:
            url = "https://" + url
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
        except Exception as e:
            self.log.err(tr("down.log.error", msg=str(e)))

    def _is_playlist_url(self, url: str) -> bool:
        try:
            from urllib.parse import urlparse, parse_qs

            u = urlparse(url)
            qs = parse_qs(u.query or "")
            if qs.get("list"):
                return True
            if "playlist" in (u.path or "").lower():
                return True
            if "list=" in (u.fragment or ""):
                return True
        except Exception:
            pass
        return False

    def _reset_meta_ui(self) -> None:
        self._down_meta = None
        for w in (self.lbl_service, self.lbl_title, self.lbl_duration, self.lbl_est_size):
            w.setText("-")
        self._reset_audio_tracks()
        self.btn_download.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.pb_download.setValue(0)

    def _update_inputs_state(self) -> None:
        has_meta = self._down_meta is not None

        self.ed_url.setEnabled(not self._down_running)
        self.btn_probe.setEnabled(not self._down_running)

        self.btn_open_downloads.setEnabled(True)
        self._update_open_in_browser_state()

        opts_enabled = bool((not self._down_running) and has_meta)
        self.cb_kind.setEnabled(opts_enabled)
        self.cb_quality.setEnabled(opts_enabled)
        self.cb_ext.setEnabled(opts_enabled)
        self.cb_audio.setEnabled(bool(opts_enabled and len(self._audio_lang_codes) > 2))

        self.btn_download.setEnabled(bool((not self._down_running) and has_meta))
        if not self._down_running:
            self.btn_cancel.setEnabled(False)

    # ----- Probe -----

    def _on_probe_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url:
            self.log.info(tr("down.url.placeholder"))
            return

        if self._is_playlist_url(url):
            info_playlist_not_supported(self)
            self.log.info(tr("down.log.playlist_not_supported"))
            return

        if self._down_running:
            self.log.info(tr("down.log.analyze"))
            return

        self._reset_meta_ui()
        self.log.clear()

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(action="probe", url=url)
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self.log.plain)
        self._down_worker.meta_ready.connect(self._on_probe_ready)
        self._down_worker.download_error.connect(lambda m: self.log.err(m))
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._update_inputs_state()
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

        fmts = meta.get("formats") or []
        vid_heights_all = {int(f["height"]) for f in fmts if f.get("height")}
        aud_abrs = {int(f["abr"]) for f in fmts if f.get("abr")}

        min_h = int(getattr(Config, "min_video_height", lambda: 1)())
        max_h = int(getattr(Config, "max_video_height", lambda: 10_000)())
        vid_heights = {h for h in vid_heights_all if min_h <= h <= max_h}

        fallback_heights = [2160, 1440, 1080, 720, 480, 360, 240, 144]
        fallback_quals = [f"{h}p" for h in fallback_heights if min_h <= h <= max_h]

        self._vid_quals = ["Auto"] + (
            [f"{h}p" for h in sorted(vid_heights, reverse=True)]
            if vid_heights
            else fallback_quals
        )
        self._aud_quals = ["Auto"] + (
            [f"{k}k" for k in sorted(aud_abrs, reverse=True)]
            if aud_abrs
            else ["320k", "256k", "192k", "128k"]
        )

        self._on_kind_changed(force=True)
        self._update_audio_tracks(meta)
        self._update_buttons_and_size()
        self.log.ok(tr("down.log.meta_ready"))

    # ----- Download -----

    def _on_download_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url or not self._down_meta:
            self.log.info(tr("down.url.placeholder"))
            return
        if self._down_running:
            self.log.info(tr("down.log.downloading"))
            return

        kind = "video" if tr("down.select.type.video").lower() in self.cb_kind.currentText().lower() else "audio"
        quality = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        audio_lang: Optional[str] = None
        idx = self.cb_audio.currentIndex()
        if 0 <= idx < len(self._audio_lang_codes):
            audio_lang = self._audio_lang_codes[idx]

        self._download_aborted = False
        self.pb_download.setValue(0)

        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(
            action="download",
            url=url,
            kind=kind,
            quality=quality,
            ext=ext,
            audio_lang=audio_lang,
        )
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self.log.plain)
        self._down_worker.progress_pct.connect(self.pb_download.setValue)

        self._down_worker.duplicate_check.connect(self._on_duplicate_decision)
        self._down_worker.download_finished.connect(self._on_download_finished)
        self._down_worker.download_error.connect(self.log.err)

        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self.btn_cancel.setEnabled(True)
        self._update_inputs_state()
        self._down_thread.start()

    def _on_cancel_clicked(self) -> None:
        self._download_aborted = True
        try:
            if self._down_worker:
                self._down_worker.cancel()
        except Exception:
            pass
        self.pb_download.setValue(0)
        self.btn_cancel.setEnabled(False)

    @QtCore.pyqtSlot(str, str)
    def _on_duplicate_decision(self, title: str, existing_path: str) -> None:
        try:
            suggested = Path(existing_path).stem
            action, new_name = ask_download_duplicate(self, title=title, suggested_name=suggested)
            if self._down_worker is not None:
                self._down_worker.on_duplicate_decided(action, new_name)
        except Exception:
            if self._down_worker is not None:
                self._down_worker.on_duplicate_decided("skip", "")

    def _on_download_finished(self, path: Path) -> None:
        try:
            title = None
            if isinstance(self._down_meta, dict):
                title = self._down_meta.get("title")
            if not title:
                title = path.stem
            self.pb_download.setValue(100)
            self.log.line_with_link(tr("down.log.downloaded_prefix"), path, title=title)
            self._update_buttons_and_size()
        except Exception as e:
            self.log.err(tr("down.log.error", msg=str(e)))

    def _on_down_thread_finished(self) -> None:
        self._down_thread = None
        self._down_worker = None
        self._down_running = False
        self.btn_cancel.setEnabled(False)

        if self._download_aborted:
            self.pb_download.setValue(0)
            self._download_aborted = False

        self._update_inputs_state()
        self._update_buttons_and_size()

    # ----- Helpers -----

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

    def _reset_audio_tracks(self) -> None:
        self.cb_audio.blockSignals(True)
        self.cb_audio.clear()
        self.cb_audio.addItem(tr("down.select.audio_track.default"))
        self.cb_audio.blockSignals(False)
        self._audio_lang_codes = [None]
        self.cb_audio.setEnabled(False)

    def _update_audio_tracks(self, meta: Dict[str, Any]) -> None:
        raw = meta.get("audio_tracks") or meta.get("audio_langs") or []
        codes: List[str] = []
        for t in raw:
            if isinstance(t, dict):
                code = t.get("lang_code") or t.get("lang")
            else:
                code = t
            norm = self._normalize_lang_code(code)
            if norm and norm not in codes:
                codes.append(norm)

        self.cb_audio.blockSignals(True)
        self.cb_audio.clear()
        self.cb_audio.addItem(tr("down.select.audio_track.default"))
        self._audio_lang_codes = [None]
        for c in sorted(codes):
            self.cb_audio.addItem(c)
            self._audio_lang_codes.append(c)
        self.cb_audio.setCurrentIndex(0)
        self.cb_audio.blockSignals(False)

        self.cb_audio.setEnabled(bool((len(self._audio_lang_codes) > 2) and (self._down_meta is not None) and (not self._down_running)))

    def _on_kind_changed(self, *, force: bool = False) -> None:
        kind_text = self.cb_kind.currentText().lower()

        if tr("down.select.type.audio").lower() in kind_text or force:
            if tr("down.select.type.audio").lower() in kind_text:
                self.cb_quality.blockSignals(True)
                self.cb_ext.blockSignals(True)
                self.cb_quality.clear()
                self.cb_quality.addItems(self._aud_quals)
                self.cb_ext.clear()
                self.cb_ext.addItems(self._aud_exts)
                self.cb_quality.blockSignals(False)
                self.cb_ext.blockSignals(False)

        if tr("down.select.type.video").lower() in kind_text or force:
            if tr("down.select.type.video").lower() in kind_text:
                self.cb_quality.blockSignals(True)
                self.cb_ext.blockSignals(True)
                self.cb_quality.clear()
                self.cb_quality.addItems(self._vid_quals)
                self.cb_ext.clear()
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

        kind_text = self.cb_kind.currentText().lower()
        q = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        min_h = int(getattr(Config, "min_video_height", lambda: 1)())
        max_h = int(getattr(Config, "max_video_height", lambda: 10_000)())

        is_audio_kind = tr("down.select.type.audio").lower() in kind_text
        is_video_kind = tr("down.select.type.video").lower() in kind_text

        candidates: List[int] = []
        for f in fmts:
            fext = str(f.get("ext") or "").lower()
            height = f.get("height") or 0
            abr = f.get("abr") or f.get("tbr") or 0

            has_video = f.get("vcodec") not in (None, "none")
            if is_audio_kind and has_video:
                continue
            if is_video_kind and not has_video:
                continue

            if is_video_kind:
                try:
                    h_int = int(height or 0)
                except Exception:
                    h_int = 0
                if h_int and (h_int < min_h or h_int > max_h):
                    continue

            if ext and fext and ext != "auto" and fext != ext:
                continue

            if q != "auto":
                try:
                    if is_audio_kind:
                        want = int(q.replace("k", ""))
                        if not abr or abr > want + 64 or abr < want - 64:
                            continue
                    else:
                        want = int(q.replace("p", ""))
                        upper = min(want, max_h)
                        if not height or int(height) > upper or int(height) < min_h:
                            continue
                except Exception:
                    pass

            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                candidates.append(int(size))

        self.lbl_est_size.setText(format_bytes(max(candidates)) if candidates else "-")

    def on_parent_close(self) -> None:
        try:
            if self._down_thread and self._down_worker:
                self._down_worker.cancel()
                self._down_thread.requestInterruption()
        except Exception:
            pass
