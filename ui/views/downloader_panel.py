from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt5 import QtWidgets, QtCore, QtGui

from core.config.app_config import AppConfig as Config
from core.utils.text import format_bytes, format_hms
from ui.utils.translating import tr
from ui.utils.logging import QtHtmlLogSink
from ui.views.dialogs import ask_download_duplicate
from ui.workers.download_worker import DownloadWorker


class DownloaderPanel(QtWidgets.QWidget):
    """Downloader tab UI + logic (probe + download, cancel, duplicate handling)."""

    # ----- Init / Layout -----

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        root = QtWidgets.QVBoxLayout(self)

        # URL row
        url_row = QtWidgets.QHBoxLayout()
        self.ed_url = QtWidgets.QLineEdit()
        self.ed_url.setPlaceholderText(tr("down.url.placeholder"))
        self.btn_probe = QtWidgets.QPushButton(tr("down.probe"))
        self.btn_open_downloads = QtWidgets.QPushButton(tr("down.open_folder"))
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_probe)
        url_row.addWidget(self.btn_open_downloads)
        root.addLayout(url_row)

        # Metadata
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

        # Selection
        sel_group = QtWidgets.QGroupBox(tr("down.select.title"))
        sel_layout = QtWidgets.QHBoxLayout(sel_group)
        self.cb_kind = QtWidgets.QComboBox()
        self.cb_kind.addItems([tr("down.select.type.video"), tr("down.select.type.audio")])
        self.cb_quality = QtWidgets.QComboBox()
        self.cb_ext = QtWidgets.QComboBox()
        self.cb_audio = QtWidgets.QComboBox()

        # defaults overridden after probe
        self._vid_quals: List[str] = ["Auto", "1080p", "720p", "480p"]

        # Extensions for output formats now driven by settings (media.downloader.*).
        # We normalize by stripping a leading dot to keep UI values clean.
        down_vid_ext = [e.lstrip(".") for e in getattr(Config, "downloader_video_extensions", lambda: ())()]
        down_aud_ext = [e.lstrip(".") for e in getattr(Config, "downloader_audio_extensions", lambda: ())()]

        # Fallbacks in case settings are missing or empty.
        self._vid_exts: List[str] = down_vid_ext or ["mp4", "webm"]
        self._aud_quals: List[str] = ["Auto", "320k", "256k", "192k", "128k"]
        self._aud_exts: List[str] = down_aud_ext or ["m4a", "mp3"]

        self.cb_quality.addItems(self._vid_quals)
        self.cb_ext.addItems(self._vid_exts)

        # audio language selection (will be filled after probe)
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

        # Progress row: [bar] [Download] [Cancel]
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

        # Log (QTextBrowser + HTML sink)
        self.down_log = QtWidgets.QTextBrowser()
        self.down_log.setOpenExternalLinks(False)
        self.down_log.setOpenLinks(False)
        self.down_log.anchorClicked.connect(self._on_anchor_clicked)
        root.addWidget(self.down_log, 2)

        self.log = QtHtmlLogSink(self.down_log)

        # ----- State -----
        self._down_thread: Optional[QtCore.QThread] = None
        self._down_worker: Optional[DownloadWorker] = None
        self._down_meta: Optional[dict] = None
        self._down_running: bool = False

        # ----- Signals -----
        self.btn_probe.clicked.connect(self._on_probe_clicked)
        self.btn_download.clicked.connect(self._on_download_clicked)
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        self.cb_kind.currentIndexChanged.connect(self._on_kind_changed)
        self.cb_quality.currentIndexChanged.connect(self._update_buttons_and_size)
        self.cb_ext.currentIndexChanged.connect(self._update_buttons_and_size)
        self.cb_audio.currentIndexChanged.connect(self._update_buttons_and_size)
        self.btn_open_downloads.clicked.connect(self._on_open_downloads_clicked)

    # ----- Link opener / Folders -----

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

    # ----- Probe flow -----

    def _on_probe_clicked(self) -> None:
        url = self.ed_url.text().strip()
        if not url:
            self.log.info(tr("down.url.placeholder"))
            return
        if self._down_running:
            self.log.info(tr("down.log.analyze"))
            return

        # reset UI
        self._down_meta = None
        for w in (self.lbl_service, self.lbl_title, self.lbl_duration, self.lbl_est_size):
            w.setText("-")
        self.pb_download.setValue(0)
        self.log.clear()
        self.btn_download.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._reset_audio_tracks()

        # thread + worker
        self._down_thread = QtCore.QThread(self)
        self._down_worker = DownloadWorker(action="probe", url=url)
        self._down_worker.moveToThread(self._down_thread)

        self._down_thread.started.connect(self._down_worker.run)
        self._down_worker.progress_log.connect(self.log.plain)
        self._down_worker.meta_ready.connect(self._on_probe_ready)
        self._down_worker.download_error.connect(lambda m: self.log.err(tr("down.log.error", msg=m)))
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._toggle_inputs(False)
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

        # refine quality lists from formats (best effort)
        fmts = meta.get("formats") or []
        vid_heights = {int(f["height"]) for f in fmts if f.get("height")}
        aud_abrs = {int(f["abr"]) for f in fmts if f.get("abr")}

        self._vid_quals = ["Auto"] + (
            [f"{h}p" for h in sorted(vid_heights, reverse=True)]
            if vid_heights
            else ["1080p", "720p", "480p"]
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

    # ----- Download / Cancel flow (+ duplicate decision) -----

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

        # duplicate rendezvous
        self._down_worker.duplicate_check.connect(self._on_duplicate_decision)

        self._down_worker.download_finished.connect(self._on_download_finished)
        self._down_worker.download_error.connect(lambda m: self.log.err(m))
        self._down_worker.finished.connect(self._down_thread.quit)
        self._down_worker.finished.connect(self._down_worker.deleteLater)
        self._down_thread.finished.connect(self._on_down_thread_finished)
        self._down_thread.finished.connect(self._down_thread.deleteLater)

        self._down_running = True
        self._toggle_inputs(False)
        self.btn_cancel.setEnabled(True)
        self._down_thread.start()

    def _on_cancel_clicked(self) -> None:
        if self._down_worker:
            try:
                self._down_worker.cancel()
            except Exception:
                pass
        self.btn_cancel.setEnabled(False)

    @QtCore.pyqtSlot(str, str)
    def _on_duplicate_decision(self, title: str, existing_path: str) -> None:
        """Popup for duplicate download; pass decision back to worker."""
        try:
            suggested = Path(existing_path).stem
            action, new_name = ask_download_duplicate(self, title=title, suggested_name=suggested)
            if self._down_worker is not None:
                self._down_worker.on_duplicate_decided(action, new_name)
        except Exception:
            if self._down_worker is not None:
                self._down_worker.on_duplicate_decided("skip", "")

    def _on_download_finished(self, path: Path) -> None:
        title = self._down_meta.get("title") if isinstance(self._down_meta, dict) else path.stem
        self.pb_download.setValue(100)
        # single line: "Pobrano: <link>" – prefix from i18n
        self.log.line_with_link(tr("down.log.downloaded_prefix"), path, title=title)
        self._update_buttons_and_size()

    def _on_down_thread_finished(self) -> None:
        self._down_thread = None
        self._down_worker = None
        self._down_running = False
        self._toggle_inputs(True)
        self.btn_cancel.setEnabled(False)
        self._update_buttons_and_size()

    # ----- Selections / UI helpers -----

    @staticmethod
    def _normalize_lang_code(code: str | None) -> str | None:
        """Same normalization as in DownloadService – keep local to avoid coupling."""
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
        self.cb_audio.setEnabled(False)
        self._audio_lang_codes = [None]

    def _update_audio_tracks(self, meta: Dict[str, Any]) -> None:
        """Fill audio-track combo from metadata."""
        raw = meta.get("audio_tracks") or []
        codes: List[str] = []
        for t in raw:
            code = t.get("lang_code") or t.get("lang")
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

        # if there is only one actual track → do not allow changing
        self.cb_audio.setEnabled(len(self._audio_lang_codes) > 2)

    def _on_kind_changed(self, *, force: bool = False) -> None:
        kind = self.cb_kind.currentText().lower()

        if tr("down.select.type.audio").lower() in kind or force:
            if tr("down.select.type.audio").lower() in kind:
                self.cb_quality.blockSignals(True)
                self.cb_ext.blockSignals(True)
                self.cb_quality.clear()
                self.cb_quality.addItems(self._aud_quals)
                self.cb_ext.clear()
                self.cb_ext.addItems(self._aud_exts)
                self.cb_quality.blockSignals(False)
                self.cb_ext.blockSignals(False)

        if tr("down.select.type.video").lower() in kind or force:
            if tr("down.select.type.video").lower() in kind:
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

        kind = self.cb_kind.currentText().lower()
        q = self.cb_quality.currentText().lower()
        ext = self.cb_ext.currentText().lower()

        candidates: List[int] = []
        for f in fmts:
            fext = str(f.get("ext") or "").lower()
            height = f.get("height") or 0
            abr = f.get("abr") or f.get("tbr") or 0

            is_video = f.get("vcodec") not in (None, "none")
            if tr("down.select.type.audio").lower() in kind and is_video:
                continue
            if tr("down.select.type.video").lower() in kind and not is_video:
                continue

            if ext and fext and ext != "auto" and fext != ext:
                continue

            if q != "auto":
                try:
                    if tr("down.select.type.audio").lower() in kind:
                        want = int(q.replace("k", ""))
                        if not abr or abr > want + 64 or abr < want - 64:
                            continue
                    else:
                        want = int(q.replace("p", ""))
                        if not height or height > want:
                            continue
                except Exception:
                    pass

            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                candidates.append(int(size))

        self.lbl_est_size.setText(format_bytes(max(candidates)) if candidates else "-")

    def _toggle_inputs(self, enabled: bool) -> None:
        for w in (
            self.ed_url,
            self.btn_probe,
            self.cb_kind,
            self.cb_quality,
            self.cb_ext,
            self.cb_audio,
            self.btn_open_downloads,
        ):
            w.setEnabled(enabled)

    # ----- Cleanup -----

    def on_parent_close(self) -> None:
        try:
            if self._down_thread and self._down_worker:
                self._down_worker.cancel()
                self._down_thread.requestInterruption()
        except Exception:
            pass
