# app/view/panels/downloader_panel.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from PyQt5 import QtCore, QtGui, QtNetwork, QtWidgets

from app.controller.contracts import DownloaderCoordinatorProtocol
from app.model.config.app_config import AppConfig as Config
from app.model.config.app_meta import AppMeta
from app.model.config.download_policy import DownloadPolicy
from app.model.domain.results import ExpandedSourceItem, SourceExpansionResult
from app.model.helpers.string_utils import (
    format_bytes,
    format_hms,
    normalize_lang_code,
    sanitize_url_for_log,
)
from app.model.services.download_service import DownloadService
from app.model.services.localization_service import tr
from app.model.services.source_input_service import is_playlist_url
from app.view import dialogs
from app.view.components.progress_action_bar import ProgressActionBar
from app.view.components.section_group import SectionGroup
from app.view.components.source_table import SourceTable
from app.view.support.source_expansion_ui import (
    ensure_progress_dialog,
    hide_progress_dialog,
    limit_items as limit_expansion_items,
    sample_titles as sample_expansion_titles,
    show_progress_dialog,
    should_confirm_bulk_add,
    update_progress_dialog_message,
)
from app.view.support.status_presenter import (
    compose_status_text,
    display_texts_for_statuses,
    is_progress_status,
    is_terminal_status,
    status_display_text,
)
from app.view.support.view_runtime import (
    normalize_network_status,
    open_external_url,
    open_local_path,
    read_network_status,
)
from app.view.support.widget_effects import enable_styled_background, repolish_widget
from app.view.support.widget_setup import (
    build_layout_host,
    make_grid,
    setup_button,
    setup_input,
    setup_layout,
)
from app.view.ui_config import ui

_LOG = logging.getLogger(__name__)

_POSTPROCESS_INTERVAL_MS = 120
_PREVIEW_PROBE_INTERVAL_MS = 450
_PROGRESS_BASE_PCT = 5
_PROGRESS_SCALE_PCT = 85
_LINK_MAX_CHARS = 48
_DESCRIPTION_MAX_CHARS = 320
_DOWNLOAD_BULK_PROBE_LIMIT = 10


@dataclass
class _Job:
    """Mutable queue row state tracked while the downloader panel is open."""
    key: str
    url: str
    output_types: list[str]
    output_exts: list[str]
    quality: str
    audio_lang: str | None
    status: str

@dataclass(frozen=True)
class _DownloadQueueItem:
    """Normalized single download step prepared from one queue row."""
    key: str
    url: str
    kind: str
    quality: str
    ext: str
    audio_lang: str | None


class DownloaderPanel(QtWidgets.QWidget):
    """Downloader tab UI and control logic."""

    _dup_apply_all_action: str | None

    COL_CHECK = 0
    COL_NO = 1
    COL_TITLE = 2
    COL_PATH = 3
    COL_SOURCE = COL_PATH
    COL_OUTPUTS = 4
    COL_QUALITY = 5
    COL_FORMATS = 6
    COL_LANGUAGE = 7
    COL_STATUS = 8

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DownloaderPanel")
        self.setProperty("uiRole", "page")
        enable_styled_background(self)
        self._ui = ui(self)
        self._panel_coordinator: DownloaderCoordinatorProtocol | None = None
        self._queue_items: list[_DownloadQueueItem] = []
        self._active_download: _DownloadQueueItem | None = None
        self._preview_key = ""
        self._download_aborted = False
        self._closing = False

        self._init_state(parent)
        self._build_ui()
        self._wire_signals(parent)
        self._restore_initial_state()

    def bind_coordinator(self, coordinator: DownloaderCoordinatorProtocol) -> None:
        self._panel_coordinator = coordinator

    def coordinator(self) -> DownloaderCoordinatorProtocol | None:
        return self._panel_coordinator

    def _download_is_running(self) -> bool:
        coord = self.coordinator()
        return bool(coord is not None and coord.is_downloading())

    def _expansion_is_running(self) -> bool:
        coord = self.coordinator()
        return bool(coord is not None and coord.is_expanding())

    def _probe_is_running(self, job_key: str | None = None) -> bool:
        coord = self.coordinator()
        return bool(coord is not None and coord.is_probe_running(job_key))

    def _init_state(self, parent: QtWidgets.QWidget | None) -> None:
        self._jobs: list[_Job] = []
        self._meta_by_key: dict[str, dict[str, Any]] = {}
        self._thumb_by_key: dict[str, QtGui.QPixmap] = {}
        self._thumb_reply_by_key: dict[str, QtNetwork.QNetworkReply] = {}
        self._queue_items: list[_DownloadQueueItem] = []
        self._pct_by_key: dict[str, int] = {}
        self._status_base_by_key: dict[str, str] = {}

        self._active_download: _DownloadQueueItem | None = None
        self._preview_key: str = ""
        self._download_aborted: bool = False
        self._dup_apply_all_action: str | None = None
        self._closing: bool = False
        self._network_status: str = read_network_status(parent)
        self._expansion_progress_dialog: dialogs.ExpansionProgressDialog | None = None
        self._last_availability_debug_key: tuple | None = None

        self._net = QtNetwork.QNetworkAccessManager(self)

        self._post_timer = QtCore.QTimer(self)
        self._post_timer.setInterval(_POSTPROCESS_INTERVAL_MS)
        self._post_timer.timeout.connect(self._tick_postprocess)

        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_PROBE_INTERVAL_MS)
        self._preview_timer.timeout.connect(self._run_preview_probe)

    def _build_ui(self) -> None:
        cfg = self._ui
        root = QtWidgets.QVBoxLayout(self)
        setup_layout(root, cfg=cfg, margins=(0, cfg.spacing, 0, 0), spacing=cfg.spacing)

        base_h = cfg.control_min_h
        self._build_top_section(root, base_h)
        self._build_queue_section(root)
        self._build_action_bar(root, base_h)
        self._build_meta_section(root, base_h)

    def _build_top_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        self._top_section_host = QtWidgets.QWidget(self)
        top_grid = make_grid(4, cfg)
        self._top_section_host.setLayout(top_grid)
        top_grid.setVerticalSpacing(cfg.space_l)

        self.ed_url = QtWidgets.QLineEdit()
        setup_input(self.ed_url, placeholder=tr("down.url.placeholder"), min_h=base_h)

        self.btn_add = QtWidgets.QPushButton(tr("ctrl.add"))
        setup_button(self.btn_add, min_h=base_h, min_w=cfg.control_min_w)

        self.btn_open_downloads = QtWidgets.QPushButton(tr("down.open_folder"))
        setup_button(self.btn_open_downloads, min_h=base_h, min_w=cfg.control_min_w)

        top_btn_host, top_btn_box = build_layout_host(
            layout="hbox",
            margins=(0, 0, 0, 0),
            spacing=cfg.space_l,
        )
        top_btn_box.addWidget(self.btn_add, 1)
        top_btn_box.addWidget(self.btn_open_downloads, 3)

        self.btn_open_in_browser = QtWidgets.QPushButton(tr("down.open_in_browser"))
        self.btn_remove_selected = QtWidgets.QPushButton(tr("files.remove_selected"))
        self.btn_clear_list = QtWidgets.QPushButton(tr("files.clear"))
        for button in (self.btn_open_in_browser, self.btn_remove_selected, self.btn_clear_list):
            setup_button(button, min_h=base_h, min_w=cfg.control_min_w)

        top_grid.addWidget(self.ed_url, 0, 0, 1, 3)
        top_grid.addWidget(top_btn_host, 0, 3, 1, 1)
        top_grid.addWidget(self.btn_open_in_browser, 1, 1)
        top_grid.addWidget(self.btn_remove_selected, 1, 2)
        top_grid.addWidget(self.btn_clear_list, 1, 3)
        root.addWidget(self._top_section_host)

    def _build_queue_section(self, root: QtWidgets.QVBoxLayout) -> None:
        details_group = SectionGroup(self, object_name="DownloaderDetailsGroup")
        details_layout = cast(QtWidgets.QVBoxLayout, details_group.root)

        self.tbl_queue = SourceTable()
        self.tbl_queue.setObjectName("DownloaderQueueTable")
        self.tbl_queue.setColumnCount(9)
        self.tbl_queue.setHorizontalHeaderLabels(
            [
                "",
                "#",
                tr("files.details.col.name"),
                tr("files.details.col.path"),
                tr("down.queue.outputs"),
                tr("down.queue.quality"),
                tr("down.queue.formats"),
                tr("files.details.col.language"),
                tr("files.details.col.status"),
            ]
        )
        self.tbl_queue.verticalHeader().setVisible(False)
        self.tbl_queue.setCornerButtonEnabled(False)
        self.tbl_queue.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_queue.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_queue.setAlternatingRowColors(True)
        self.tbl_queue.setSortingEnabled(False)
        self.tbl_queue.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.tbl_queue.setAcceptDrops(False)
        self.tbl_queue.setWordWrap(True)
        self.tbl_queue.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)

        self._header_mode = "empty"
        self._apply_empty_header_mode()
        details_layout.addWidget(self.tbl_queue, 2)
        self._queue_group = details_group
        root.addWidget(details_group, 2)

    def _build_action_bar(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        self.action_bar = ProgressActionBar(
            primary_text=tr("ctrl.start"),
            secondary_text=tr("ctrl.cancel"),
            height=base_h,
        )
        root.addWidget(self.action_bar)

    def _build_meta_section(self, root: QtWidgets.QVBoxLayout, base_h: int) -> None:
        cfg = self._ui
        meta_group = SectionGroup(self, object_name="DownloaderMetaGroup", layout="hbox")
        meta_root = cast(QtWidgets.QHBoxLayout, meta_group.root)

        col_left, meta_left = build_layout_host(layout="form", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        col_right, meta_right = build_layout_host(layout="form", margins=(0, 0, 0, 0), spacing=cfg.spacing)
        self._meta_col_left = col_left
        self._meta_col_right = col_right

        self.lbl_service = QtWidgets.QLabel(tr("common.na"))
        self.lbl_title = QtWidgets.QLabel(tr("common.na"))
        self.lbl_channel = QtWidgets.QLabel(tr("common.na"))
        self.lbl_upload_date = QtWidgets.QLabel(tr("common.na"))
        self.lbl_duration = QtWidgets.QLabel(tr("common.na"))
        self.lbl_est_size = QtWidgets.QLabel(tr("common.na"))
        self.lbl_views = QtWidgets.QLabel(tr("common.na"))
        self.lbl_likes = QtWidgets.QLabel(tr("common.na"))
        self._meta_value_labels = [
            self.lbl_service,
            self.lbl_title,
            self.lbl_channel,
            self.lbl_upload_date,
            self.lbl_duration,
            self.lbl_est_size,
            self.lbl_views,
            self.lbl_likes,
        ]
        for label in self._meta_value_labels:
            label.setWordWrap(False)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        meta_left.addRow(tr("down.meta.service"), self.lbl_service)
        meta_left.addRow(tr("down.meta.name"), self.lbl_title)
        meta_left.addRow(tr("down.meta.channel"), self.lbl_channel)
        meta_left.addRow(tr("down.meta.date"), self.lbl_upload_date)
        meta_left.addRow(tr("down.meta.duration"), self.lbl_duration)
        meta_left.addRow(tr("down.meta.size"), self.lbl_est_size)

        self.lbl_description = QtWidgets.QLabel(tr("common.na"))
        self.lbl_description.setWordWrap(True)
        self.lbl_description.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_description.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        self.lbl_description.setMinimumHeight(int(base_h * 3))
        self.lbl_description.setMaximumHeight(int(base_h * 5 + cfg.pad_y_m))
        self.lbl_description.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.lbl_description.setObjectName("DownloaderMetaDescription")

        meta_right.addRow(tr("down.meta.views"), self.lbl_views)
        meta_right.addRow(tr("down.meta.likes"), self.lbl_likes)
        lbl_description_title = QtWidgets.QLabel(tr("down.meta.description"))
        lbl_description_title.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        meta_right.addRow(lbl_description_title, self.lbl_description)

        meta_root.addWidget(col_left, 3)
        meta_root.addWidget(col_right, 3)

        thumb_host = QtWidgets.QFrame()
        thumb_host.setObjectName("DownloaderThumbHost")
        self._thumb_host = thumb_host
        thumb_host.setProperty("uiEmpty", "true")
        thumb_host.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        thumb_lay = QtWidgets.QVBoxLayout(thumb_host)
        setup_layout(thumb_lay, cfg=cfg, margins=(0, 0, 0, 0), spacing=0)
        thumb_lay.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.lbl_thumbnail = QtWidgets.QLabel()
        self.lbl_thumbnail.setWordWrap(True)
        self.lbl_thumbnail.setObjectName("DownloaderThumbnail")
        self.lbl_thumbnail.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumbnail.setMinimumWidth(int(base_h * 7))
        self.lbl_thumbnail.setMaximumWidth(int(base_h * 9))
        self.lbl_thumbnail.setMinimumHeight(int(base_h * 4 + cfg.margin * 2 + max(0, cfg.space_s - 2)))
        self.lbl_thumbnail.setMaximumHeight(int(base_h * 6 + cfg.margin * 2 + max(0, cfg.space_s - 1) + cfg.pad_y_s))
        self.lbl_thumbnail.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.lbl_thumbnail.setScaledContents(False)
        thumb_lay.addWidget(self.lbl_thumbnail, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        thumb_host.setMinimumWidth(int(self.lbl_thumbnail.maximumWidth() + cfg.margin * 2))

        meta_root.addWidget(thumb_host, 0)
        meta_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        meta_group.setMaximumHeight(int(base_h * 11 + cfg.pad_x_l + max(0, cfg.space_s - 1)))
        self._meta_group = meta_group
        root.addWidget(meta_group, 1)

    def _wire_signals(self, parent: QtWidgets.QWidget | None) -> None:
        self.btn_add.clicked.connect(self._on_add_clicked)
        self.btn_open_downloads.clicked.connect(self._on_open_downloads_clicked)
        self.btn_open_in_browser.clicked.connect(self._on_open_in_browser_clicked)
        self.btn_remove_selected.clicked.connect(self._on_remove_selected)
        self.btn_clear_list.clicked.connect(self._on_clear_list)

        self.ed_url.textChanged.connect(self._on_url_text_changed)
        self.ed_url.returnPressed.connect(self._on_add_clicked)

        self.action_bar.primary_clicked.connect(self._on_download_selected)
        self.action_bar.secondary_clicked.connect(self._on_cancel_clicked)

        self.tbl_queue.itemSelectionChanged.connect(self._on_selection_changed)
        self.tbl_queue.itemSelectionChanged.connect(self._sync_buttons)
        self.tbl_queue.cellClicked.connect(self._on_table_cell_clicked)
        self.tbl_queue.delete_pressed.connect(self._on_remove_selected)

        parent_signal = getattr(parent, "network_status_changed", None)
        if parent_signal is not None:
            try:
                parent_signal.connect(self._on_network_status_changed)
            except (AttributeError, RuntimeError, TypeError) as ex:
                _LOG.debug("Downloader network signal hookup skipped. detail=%s", ex)

    def _restore_initial_state(self) -> None:
        self._reset_meta_ui()
        self._update_open_in_browser_state()
        self._sync_buttons()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        self._refresh_meta_value_elision()
        key = self._active_preview_key() or self._selected_job_key()
        pm = self._thumb_by_key.get(key)
        if pm is not None:
            self._apply_pixmap(pm)

    def on_parent_close(self) -> None:
        self._closing = True
        self._preview_timer.stop()
        self._post_timer.stop()
        self._download_aborted = True
        self._queue_items = []
        self._active_download = None
        self._preview_key = ""

        coord = self.coordinator()
        if coord is not None:
            coord.cancel_download()
            coord.cancel_all_probes()

        for key in list(self._thumb_reply_by_key.keys()):
            self._cancel_thumb_reply(key)

        self._meta_by_key.clear()
        self._thumb_by_key.clear()

        self.action_bar.set_busy(False)
        self.action_bar.reset()

    def _reset_meta_ui(self) -> None:
        for w in (
            self.lbl_service,
            self.lbl_title,
            self.lbl_channel,
            self.lbl_upload_date,
            self.lbl_duration,
            self.lbl_est_size,
            self.lbl_views,
            self.lbl_likes,
        ):
            self._set_meta_value_text(w, tr("common.na"))
        self.lbl_description.setToolTip("")
        self.lbl_description.setText(tr("common.na"))
        self._show_thumbnail_placeholder()

    def _set_meta_value_text(self, label: QtWidgets.QLabel, text: str) -> None:
        raw = str(text or "-").strip() or "-"
        label.setProperty("fullText", raw)
        self._apply_meta_value_elision(label)

    @staticmethod
    def _apply_meta_value_elision(label: QtWidgets.QLabel) -> None:
        raw = str(label.property("fullText") or label.text() or "-")
        available = max(32, int(label.contentsRect().width()) or int(label.width()) or 32)
        rendered = label.fontMetrics().elidedText(raw, QtCore.Qt.TextElideMode.ElideRight, available)
        label.setText(rendered)
        label.setToolTip(raw if rendered != raw else "")

    def _refresh_meta_value_elision(self) -> None:
        for label in getattr(self, "_meta_value_labels", []):
            if isinstance(label, QtWidgets.QLabel):
                self._apply_meta_value_elision(label)

    def _set_thumb_empty(self, empty: bool) -> None:
        val = "true" if empty else "false"
        try:
            self._thumb_host.setProperty("uiEmpty", val)
            self.lbl_thumbnail.setProperty("uiEmpty", val)
            repolish_widget(self._thumb_host)
            repolish_widget(self.lbl_thumbnail)
        except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
            _LOG.debug("Thumbnail empty-state update skipped. detail=%s", ex)

    def _show_thumbnail_placeholder(self) -> None:
        self._set_thumb_empty(True)
        try:
            self.lbl_thumbnail.setPixmap(QtGui.QPixmap())
        except (AttributeError, RuntimeError, TypeError):
            self.lbl_thumbnail.clear()
        try:
            self.lbl_thumbnail.setText(tr("down.meta.thumb_placeholder"))
        except (RuntimeError, TypeError, ValueError, KeyError):
            self.lbl_thumbnail.setText(tr("common.na"))

    def _network_available(self) -> bool:
        return self._network_status != "offline"

    @staticmethod
    def _is_network_error_key(err_key: str) -> bool:
        return str(err_key or "").strip().startswith("error.down.network_")

    def _log_queue_state(self, *, reason: str) -> None:
        state = (
            self._network_status,
            bool(self._download_is_running()),
            int(self.tbl_queue.rowCount()),
            bool(self.action_bar.primary_btn.isEnabled()) if hasattr(self.action_bar, "primary_btn") else False,
            )
        if state == self._last_availability_debug_key:
            return
        self._last_availability_debug_key = state
        _LOG.debug(
            (
                "Downloader availability changed. reason=%s panel=downloader online=%s running=%s "
                "queued_rows=%s can_start=%s"
            ),
            reason,
            bool(self._network_available()),
            bool(self._download_is_running()),
            self.tbl_queue.rowCount(),
            bool(self.action_bar.primary_btn.isEnabled()) if hasattr(self.action_bar, "primary_btn") else False,
            )

    @QtCore.pyqtSlot(str)
    def _on_network_status_changed(self, status: str) -> None:
        previous = self._network_status
        self._network_status = normalize_network_status(status)
        if previous != self._network_status:
            _LOG.debug("Downloader network state updated. previous=%s current=%s", previous, self._network_status)
        if not self._network_available():
            self._preview_timer.stop()
        self._sync_buttons()
        self._refresh_meta_panel()
        if self._network_available():
            self._schedule_preview_probe()
        self._log_queue_state(reason="network_status_changed")

    def _sync_buttons(self) -> None:
        running = self._download_is_running()
        expanding = self._expansion_is_running()
        online = self._network_available()
        can_start = bool((not running) and (not expanding) and self.tbl_queue.rowCount() > 0 and online)
        self.action_bar.set_primary_enabled(can_start)
        self.action_bar.set_secondary_enabled(bool(running))

        self.ed_url.setEnabled((not running) and (not expanding) and online)
        self.btn_add.setEnabled((not running) and (not expanding) and online)

        can_edit_queue = not running and not expanding
        self.btn_remove_selected.setEnabled(can_edit_queue and bool(self.tbl_queue.rows_for_removal(self.COL_CHECK)))
        self.btn_clear_list.setEnabled(can_edit_queue and self.tbl_queue.rowCount() > 0)

        self.tbl_queue.setEnabled(not running)
        self.tbl_queue.set_header_checkbox_enabled(bool(can_edit_queue and self.tbl_queue.rowCount() > 0))
        self._update_open_in_browser_state()
        self._log_queue_state(reason="buttons_synced")

    def _browser_target_url(self) -> str:
        raw = str(self.ed_url.text() or "").strip()
        if raw:
            return raw

        key = self._selected_job_key()
        job = self._job_for_key(key)
        if job is None:
            return ""
        return str(job.url or "").strip()

    def _update_open_in_browser_state(self) -> None:
        self.btn_open_in_browser.setEnabled(bool(self._browser_target_url()))

    def _active_preview_key(self) -> str:
        raw = (self.ed_url.text() or "").strip()
        if not raw or not self._preview_key:
            return ""
        return self._preview_key if self._make_job_key(raw) == self._preview_key else ""

    def _set_preview_key(self, key: str) -> None:
        next_key = str(key or "").strip()
        prev_key = str(self._preview_key or "").strip()
        if prev_key == next_key:
            return

        self._preview_key = next_key
        if prev_key and self._find_job_index(prev_key) < 0:
            self._clear_job_state(prev_key)

    @staticmethod
    def _looks_like_probe_input(raw: str) -> bool:
        text = str(raw or "").strip()
        if not text or any(ch.isspace() for ch in text):
            return False
        return "://" in text or "." in text

    def _on_url_text_changed(self) -> None:
        self._update_open_in_browser_state()
        self._schedule_preview_probe()

    def _schedule_preview_probe(self) -> None:
        self._preview_timer.stop()

        raw = (self.ed_url.text() or "").strip()
        if not self._looks_like_probe_input(raw):
            self._set_preview_key("")
            self._refresh_meta_panel()
            return

        key = self._make_job_key(raw)
        if is_playlist_url(key):
            self._set_preview_key("")
            self._refresh_meta_panel()
            return

        self._set_preview_key(key)
        if isinstance(self._meta_by_key.get(key), dict):
            _LOG.debug("Downloader preview probe skipped. reason=cache_hit job_key=%s", sanitize_url_for_log(key))
            self._refresh_meta_panel()
            return

        self._reset_meta_ui()
        if not self._network_available():
            self.lbl_description.setText(status_display_text("status.offline", "status.offline"))
            _LOG.debug("Downloader preview probe blocked. reason=offline job_key=%s", sanitize_url_for_log(key))
            return

        self.lbl_description.setText(status_display_text("status.probing", "status.probing"))
        _LOG.debug("Downloader preview probe scheduled. job_key=%s", sanitize_url_for_log(key))
        self._preview_timer.start()

    def _run_preview_probe(self) -> None:
        key = self._active_preview_key()
        if not key:
            self._refresh_meta_panel()
            return

        if isinstance(self._meta_by_key.get(key), dict):
            self._refresh_meta_panel()
            return

        if not self._network_available():
            self._refresh_meta_panel()
            return

        _LOG.debug("Downloader preview probe started. job_key=%s", sanitize_url_for_log(key))
        self._start_probe_request(key, key)
        self._refresh_meta_panel()

    def _refresh_meta_panel(self) -> None:
        key = self._active_preview_key()
        if key:
            meta = self._meta_by_key.get(key)
            if isinstance(meta, dict):
                self._apply_meta_ui(meta, job_key=key)
            else:
                self._reset_meta_ui()
                self.lbl_description.setToolTip("")
                self.lbl_description.setText(
                    status_display_text("status.offline", "status.offline")
                    if not self._network_available()
                    else status_display_text("status.probing", "status.probing")
                )
            return

        key = self._selected_job_key()
        meta = self._meta_by_key.get(key)
        if isinstance(meta, dict):
            self._apply_meta_ui(meta, job_key=key)
            return
        self._reset_meta_ui()

    def _status_width(self) -> int:
        labels: list[str] = []
        header_item = self.tbl_queue.horizontalHeaderItem(self.COL_STATUS)
        if header_item is not None:
            header_text = str(header_item.text() or "").strip()
            if header_text:
                labels.append(header_text)
        labels.extend(
            display_texts_for_statuses(
                (
                    "status.queued",
                    "status.probing",
                    "status.downloading",
                    "status.postprocessing",
                    "status.done",
                    "status.offline",
                    "status.error",
                )
            )
        )
        metrics = QtGui.QFontMetrics(self.tbl_queue.font())
        text_width = max((metrics.horizontalAdvance(label) for label in labels), default=0)
        cfg = self._ui
        min_w = int(cfg.control_min_w)
        pad_w = int(cfg.pad_x_l + cfg.pad_y_l + cfg.space_l - 1)
        max_w = int(cfg.control_min_w + cfg.control_min_h * 2 + cfg.space_l + cfg.pad_y_l - 1)
        return max(min_w, min(text_width + pad_w, max_w))

    def _apply_empty_header_mode(self) -> None:
        self._header_mode = 'empty'
        cfg = self._ui
        status_width = self._status_width()
        title_min_w = int(cfg.control_min_w + cfg.margin * 4 + max(0, cfg.space_s - 1))
        path_min_w = int(cfg.control_min_w + cfg.margin * 5)
        outputs_min_w = int(cfg.control_min_h * 3)
        outputs_empty_w = int(cfg.control_min_h * 4)
        outputs_cap_w = int(cfg.control_min_h * 4 + cfg.pad_x_l + cfg.space_l - 1)
        quality_min_w = int(cfg.control_min_h * 2 + cfg.pad_x_l + cfg.space_l - 1)
        quality_empty_w = int(cfg.control_min_h * 3 + max(0, cfg.space_s - 3))
        quality_cap_w = int(cfg.control_min_h * 3 + cfg.margin + cfg.space_s + 3)
        formats_min_w = int(cfg.control_min_h * 3 - max(0, cfg.space_s - 1))
        formats_empty_w = int(cfg.control_min_h * 3 + cfg.pad_y_l + cfg.pad_x_m - 2)
        formats_cap_w = int(cfg.control_min_h * 4 + cfg.pad_y_l)
        language_min_w = int(cfg.control_min_h * 3 + cfg.pad_x_m + cfg.pad_y_s + 1)
        language_empty_w = int(cfg.control_min_h * 4 + cfg.pad_x_l)
        language_cap_w = int(cfg.control_min_w + cfg.control_min_h + cfg.margin + cfg.space_s - 1)
        status_cap_w = int(title_min_w + cfg.control_min_h + cfg.margin * 3)
        fit_padding = int(cfg.margin + cfg.pad_x_m)
        self.tbl_queue.reset_header_user_widths()
        self.tbl_queue.apply_content_header_layout(
            check_col=self.COL_CHECK,
            number_col=self.COL_NO,
            fill_column=self.COL_PATH,
            stretch_weights={
                self.COL_TITLE: 2,
                self.COL_PATH: 3,
            },
            fit_columns=[],
            preferred_widths={
                self.COL_OUTPUTS: outputs_empty_w,
                self.COL_QUALITY: quality_empty_w,
                self.COL_FORMATS: formats_empty_w,
                self.COL_LANGUAGE: language_empty_w,
                self.COL_STATUS: status_width,
            },
            min_widths={
                self.COL_TITLE: title_min_w,
                self.COL_PATH: path_min_w,
                self.COL_OUTPUTS: outputs_min_w,
                self.COL_QUALITY: quality_min_w,
                self.COL_FORMATS: formats_min_w,
                self.COL_LANGUAGE: language_min_w,
                self.COL_STATUS: int(cfg.control_min_w),
            },
            max_widths={
                self.COL_OUTPUTS: outputs_cap_w,
                self.COL_QUALITY: quality_cap_w,
                self.COL_FORMATS: formats_cap_w,
                self.COL_LANGUAGE: language_cap_w,
                self.COL_STATUS: status_cap_w,
            },
            fit_padding=fit_padding,
        )

    def _apply_populated_header_mode(self) -> None:
        self._header_mode = 'populated'
        cfg = self._ui
        status_width = self._status_width()
        title_min_w = int(cfg.control_min_w + cfg.margin * 5 + max(0, cfg.space_s - 1))
        path_min_w = int(cfg.control_min_w + cfg.margin * 5)
        outputs_min_w = int(cfg.control_min_h * 3)
        outputs_fallback_w = int(cfg.control_min_h * 4)
        outputs_pad_w = int(max(1, cfg.space_s - 1))
        outputs_cap_w = int(cfg.control_min_h * 4 + cfg.pad_x_l + cfg.space_l - 1)
        outputs_floor_w = int(cfg.control_min_h * 4 + cfg.pad_x_m - 2)
        quality_min_w = int(cfg.control_min_h * 2 + cfg.pad_x_l + cfg.space_l - 1)
        quality_fallback_w = int(cfg.control_min_h * 3)
        quality_pad_w = int(max(1, cfg.space_s - 1))
        quality_cap_w = int(cfg.control_min_h * 3 + cfg.margin + cfg.space_s + 3)
        quality_floor_w = int(cfg.control_min_h * 3 + cfg.pad_y_l + max(0, cfg.space_s - 1))
        formats_min_w = int(cfg.control_min_h * 3 - max(0, cfg.space_s - 1))
        formats_fallback_w = int(cfg.control_min_h * 3 + cfg.pad_y_l + cfg.pad_x_m - 2)
        formats_pad_w = int(max(1, cfg.space_s - 1))
        formats_cap_w = int(cfg.control_min_h * 4 + cfg.pad_y_l)
        formats_floor_w = int(cfg.control_min_h * 4)
        language_min_w = int(cfg.control_min_h * 3 + cfg.pad_x_m + cfg.pad_y_s + 1)
        language_fallback_w = int(cfg.control_min_h * 4 + cfg.pad_y_l)
        language_pad_w = int(cfg.pad_y_m)
        language_cap_w = int(cfg.control_min_w + cfg.control_min_h + cfg.margin + cfg.space_s - 1)
        language_floor_w = int(cfg.control_min_w + cfg.control_min_h + cfg.pad_y_s - 1)
        language_extra_w = int(cfg.pad_x_m)
        column_growth_pad_w = int(cfg.pad_y_l)
        status_cap_w = int(title_min_w + cfg.control_min_h + cfg.margin * 3)
        fit_padding = int(cfg.margin + cfg.pad_x_m)
        outputs_width = self.tbl_queue.column_widget_width_hint(
            self.COL_OUTPUTS,
            fallback=outputs_fallback_w,
            pad=outputs_pad_w,
            cap=outputs_cap_w,
        )
        quality_width = self.tbl_queue.column_widget_width_hint(
            self.COL_QUALITY,
            fallback=quality_fallback_w,
            pad=quality_pad_w,
            cap=quality_cap_w,
        )
        formats_width = self.tbl_queue.column_widget_width_hint(
            self.COL_FORMATS,
            fallback=formats_fallback_w,
            pad=formats_pad_w,
            cap=formats_cap_w,
        )
        language_width = self.tbl_queue.column_widget_width_hint(
            self.COL_LANGUAGE,
            fallback=language_fallback_w,
            pad=language_pad_w,
            cap=language_cap_w,
        )
        self.tbl_queue.apply_content_header_layout(
            check_col=self.COL_CHECK,
            number_col=self.COL_NO,
            fill_column=self.COL_PATH,
            stretch_weights={
                self.COL_TITLE: 2,
                self.COL_PATH: 3,
            },
            fit_columns=[],
            preferred_widths={
                self.COL_OUTPUTS: outputs_width,
                self.COL_QUALITY: quality_width,
                self.COL_FORMATS: formats_width,
                self.COL_LANGUAGE: language_width,
                self.COL_STATUS: status_width,
            },
            min_widths={
                self.COL_TITLE: title_min_w,
                self.COL_PATH: path_min_w,
                self.COL_OUTPUTS: outputs_min_w,
                self.COL_QUALITY: quality_min_w,
                self.COL_FORMATS: formats_min_w,
                self.COL_LANGUAGE: language_min_w,
                self.COL_STATUS: int(cfg.control_min_w),
            },
            max_widths={
                self.COL_OUTPUTS: max(outputs_width + column_growth_pad_w, outputs_floor_w),
                self.COL_QUALITY: max(quality_width + column_growth_pad_w, quality_floor_w),
                self.COL_FORMATS: max(formats_width + column_growth_pad_w, formats_floor_w),
                self.COL_LANGUAGE: max(language_width + language_extra_w, language_floor_w),
                self.COL_STATUS: status_cap_w,
            },
            fit_padding=fit_padding,
        )

    def _schedule_queue_header_refresh(self) -> None:
        self.tbl_queue.schedule_populated_header_refresh(
            is_active=lambda: self._header_mode == 'populated',
            reapply=self._apply_populated_header_mode,
        )

    @staticmethod
    def _job_status_key(status: str) -> str:
        st = str(status or "").strip().lower()
        mapping = {
            "queued": "status.queued",
            "probing": "status.probing",
            "downloading": "status.downloading",
            "postprocessing": "status.postprocessing",
            "done": "status.done",
            "offline": "status.offline",
            "error": "status.error",
        }
        return mapping.get(st, st)

    @staticmethod
    def _on_open_downloads_clicked() -> None:
        try:
            Config.PATHS.DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            if not open_local_path(Config.PATHS.DOWNLOADS_DIR):
                _LOG.error("Opening the downloads folder failed. path=%s", Config.PATHS.DOWNLOADS_DIR)
        except (OSError, RuntimeError, TypeError, ValueError):
            _LOG.exception("Opening the downloads folder failed.")

    def _on_open_in_browser_clicked(self) -> None:
        url = self._browser_target_url()
        if not url:
            return
        if "://" not in url:
            url = "https://" + url
        if not open_external_url(url):
            _LOG.error("Opening the URL in the browser failed. url=%s", sanitize_url_for_log(url))

    def _collect_checked_job_keys(self) -> list[str]:
        keys: list[str] = []
        for row in self.tbl_queue.checked_rows(self.COL_CHECK):
            key = self.tbl_queue.internal_key_at(row, self.COL_TITLE)
            if key and key not in keys:
                keys.append(key)
        return keys

    def _collect_download_job_keys(self) -> list[str]:
        keys = self._collect_checked_job_keys()
        if keys:
            return keys

        selected = self._selected_job_key()
        if selected:
            return [selected]

        return [job.key for job in self._jobs if job.key]

    def _build_queue_items(self, keys: list[str]) -> list[_DownloadQueueItem]:
        items: list[_DownloadQueueItem] = []
        audio_exts = {str(x).strip().lower() for x in list(DownloadPolicy.DOWNLOAD_AUDIO_OUTPUT_EXTENSIONS)}

        for key in keys:
            idx = self._find_job_index(key)
            if idx < 0:
                continue
            job = self._jobs[idx]

            output_exts = [str(x).strip().lower() for x in (job.output_exts or []) if str(x).strip()]
            if not output_exts:
                output_exts = [str(x).strip().lower() for x in self._format_items(job.output_types) if str(x).strip()]

            only_audio = bool(job.output_types) and "audio" in job.output_types and "video" not in job.output_types

            for ext in output_exts:
                kind = "audio" if ext in audio_exts else "video"
                quality = str(job.quality or DownloadPolicy.download_ui_default_quality()).strip().lower()
                if kind == "audio" and not only_audio:
                    quality = DownloadPolicy.download_ui_default_quality()
                items.append(
                    _DownloadQueueItem(
                        key=job.key,
                        url=job.url,
                        kind=kind,
                        quality=quality,
                        ext=ext,
                        audio_lang=job.audio_lang,
                    )
                )

        return items

    def _on_add_clicked(self) -> None:
        raw = (self.ed_url.text() or "").strip()
        if not raw:
            return

        if not self._network_available():
            _LOG.debug("Downloader add blocked. reason=offline url=%s", sanitize_url_for_log(raw))
            dialogs.show_downloader_offline_dialog(self)
            return

        coord = self.coordinator()
        if coord is None:
            url = self._make_job_key(raw)
            _LOG.debug(
                "Downloader URL normalized without coordinator. raw=%s normalized=%s",
                sanitize_url_for_log(raw),
                sanitize_url_for_log(url),
            )
            self._add_single_job(url)
            return

        coord.expand_manual_input(raw)

    def _ensure_expansion_progress_dialog(self) -> dialogs.ExpansionProgressDialog:
        dlg = ensure_progress_dialog(self, self._expansion_progress_dialog, self._cancel_expansion_request)
        self._expansion_progress_dialog = dlg
        return dlg

    def _cancel_expansion_request(self) -> None:
        coord = self.coordinator()
        if coord is None:
            return
        coord.cancel_expansion()

    @staticmethod
    def _bulk_add_target_label() -> str:
        return tr("dialog.bulk_add.target.downloads")

    @staticmethod
    def _build_job_from_url(url: str) -> _Job:
        key = str(url or "").strip()
        first_ext = DownloadPolicy.download_default_video_ext()
        return _Job(
            key=key,
            url=key,
            output_types=["video"],
            output_exts=[str(first_ext).strip().lower()],
            quality=DownloadPolicy.download_ui_default_quality(),
            audio_lang=None,
            status="queued",
        )

    def _prime_job_meta(self, job_key: str, *, title: str = "", duration_s: int | None = None) -> None:
        meta = self._meta_by_key.get(job_key, {}) if isinstance(self._meta_by_key.get(job_key), dict) else {}
        changed = False
        title_text = str(title or "").strip()
        if title_text:
            meta["title"] = title_text
            changed = True
        if duration_s is not None:
            meta["duration"] = int(duration_s)
            changed = True
        if changed:
            self._meta_by_key[job_key] = meta
            self._refresh_row_from_meta(job_key)

    def _add_single_job(self, url: str) -> int:
        key = str(url or "").strip()
        if not key:
            return -1
        if self._find_job_index(key) >= 0:
            _LOG.debug("Downloader add skipped. reason=duplicate job_key=%s", sanitize_url_for_log(key))
            return -1

        job = self._build_job_from_url(key)
        self._jobs.append(job)
        row = self._append_job_row(job)
        self._select_row(row)
        _LOG.debug(
            "Downloader job added. job_key=%s default_ext=%s",
            sanitize_url_for_log(job.key),
            DownloadPolicy.download_default_video_ext(),
        )
        self._start_probe(job)
        self.ed_url.clear()
        self._sync_buttons()
        return row

    def _apply_expansion_result(
        self,
        result: SourceExpansionResult,
        items: tuple[ExpandedSourceItem, ...],
    ) -> tuple[int, int, list[str]]:
        added_keys: list[str] = []
        duplicate_count = 0
        selected_row = -1

        for item in items:
            url = self._make_job_key(str(getattr(item, "key", "") or "").strip())
            if not url:
                continue
            if self._find_job_index(url) >= 0:
                duplicate_count += 1
                continue
            job = self._build_job_from_url(url)
            self._jobs.append(job)
            row = self._append_job_row(job)
            if selected_row < 0:
                selected_row = row
            added_keys.append(job.key)
            self._prime_job_meta(
                job.key,
                title=str(getattr(item, "title", "") or ""),
                duration_s=getattr(item, "duration_s", None),
            )

        if selected_row >= 0:
            self._select_row(selected_row)

        for key in added_keys[:_DOWNLOAD_BULK_PROBE_LIMIT]:
            job = self._job_for_key(key)
            if job is not None:
                self._start_probe(job)

        if result.origin_kind in {"manual_input", "playlist"}:
            self.ed_url.clear()

        self._sync_buttons()
        return len(added_keys), duplicate_count, added_keys

    def _show_expansion_summary(
        self,
        result: SourceExpansionResult,
        *,
        added_count: int,
        duplicate_count: int,
        selected_count: int,
    ) -> None:
        message = ""
        total = int(max(0, int(result.discovered_count or 0)))
        selected = int(max(0, int(selected_count or 0)))
        limited = bool(total > 0 and 0 < selected < total)

        if total <= 1:
            if added_count == 0 and duplicate_count > 0:
                message = tr("down.msg.already_on_list")
        elif limited and added_count > 0 and duplicate_count > 0:
            message = tr(
                "down.msg.bulk_add_summary_limited_with_duplicates",
                added=added_count,
                selected=selected,
                total=total,
                skipped=duplicate_count,
            )
        elif limited and added_count > 0:
            message = tr("down.msg.bulk_add_summary_limited", added=added_count, selected=selected, total=total)
        elif added_count > 0 and duplicate_count > 0:
            message = tr("down.msg.bulk_add_summary_with_duplicates", added=added_count, skipped=duplicate_count)
        elif added_count > 0:
            message = tr("down.msg.bulk_add_summary_added", added=added_count)
        elif duplicate_count > 0:
            message = tr("down.msg.bulk_add_summary_duplicates_only", skipped=duplicate_count)

        if not message:
            return

        dialogs.show_info(
            self,
            title=tr("dialog.info.title"),
            header=tr("dialog.info.header"),
            message=message,
        )

    @QtCore.pyqtSlot(bool)
    def on_expansion_busy_changed(self, busy: bool) -> None:
        if busy:
            show_progress_dialog(self._ensure_expansion_progress_dialog())
        else:
            hide_progress_dialog(self._expansion_progress_dialog)
        self._sync_buttons()

    @QtCore.pyqtSlot(str, dict)
    def on_expansion_status_changed(self, key: str, params: dict[str, Any]) -> None:
        update_progress_dialog_message(self._ensure_expansion_progress_dialog(), key, params or {})

    @QtCore.pyqtSlot(object)
    def on_expansion_ready(self, result: SourceExpansionResult) -> None:
        hide_progress_dialog(self._expansion_progress_dialog)
        if result.discovered_count <= 0 or not result.items:
            dialogs.show_info(
                self,
                title=tr("dialog.info.title"),
                header=tr("dialog.info.header"),
                message=tr("files.msg.no_media_found"),
            )
            return

        selected_items = tuple(result.items)
        threshold = int(Config.ui_bulk_add_confirmation_threshold())
        if should_confirm_bulk_add(result.discovered_count):
            action, chosen_count = dialogs.ask_bulk_add_plan(
                self,
                origin_kind=result.origin_kind,
                count=result.discovered_count,
                origin_label=result.origin_label,
                sample_titles=sample_expansion_titles(result),
                default_limit=threshold,
                target_label=self._bulk_add_target_label(),
            )
            if action == "cancel":
                return
            if action == "first_n":
                selected_items = limit_expansion_items(result, chosen_count)

        added_count, duplicate_count, _added_keys = self._apply_expansion_result(result, selected_items)
        self._show_expansion_summary(
            result,
            added_count=added_count,
            duplicate_count=duplicate_count,
            selected_count=len(selected_items),
        )

    @QtCore.pyqtSlot(str, dict)
    def on_expansion_error(self, key: str, params: dict[str, Any]) -> None:
        hide_progress_dialog(self._expansion_progress_dialog)
        dialogs.show_error(self, key, params or {})

    @staticmethod
    def _make_job_key(url: str) -> str:
        u = str(url or "").strip()
        if "://" not in u:
            u = "https://" + u
        return u

    @staticmethod
    def _short_link_text(url: str) -> str:
        u = str(url or "").strip()
        if u.startswith("http://"):
            u = u[7:]
        if u.startswith("https://"):
            u = u[8:]
        if len(u) <= _LINK_MAX_CHARS:
            return u
        return u[: _LINK_MAX_CHARS - 3] + "..." if _LINK_MAX_CHARS > 3 else u[:_LINK_MAX_CHARS]

    def _find_job_index(self, key: str) -> int:
        for i, j in enumerate(self._jobs):
            if j.key == key:
                return i
        return -1

    def _cancel_thumb_reply(self, job_key: str) -> None:
        reply = self._thumb_reply_by_key.pop(job_key, None)
        if reply is None:
            return
        try:
            reply.finished.disconnect()
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Thumbnail reply disconnect skipped. key=%s detail=%s", job_key, ex)
        try:
            reply.abort()
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Thumbnail reply abort skipped. key=%s detail=%s", job_key, ex)
        try:
            reply.deleteLater()
        except (AttributeError, RuntimeError, TypeError) as ex:
            _LOG.debug("Thumbnail reply deleteLater skipped. key=%s detail=%s", job_key, ex)

    def _cancel_probe(self, job_key: str) -> None:
        if not self._probe_is_running(job_key):
            return
        coord = self.coordinator()
        if coord is None:
            return
        coord.cancel_probe(job_key)

    def _clear_job_state(self, job_key: str) -> None:
        self._cancel_thumb_reply(job_key)
        self._cancel_probe(job_key)
        self._meta_by_key.pop(job_key, None)
        self._thumb_by_key.pop(job_key, None)

    def _finalize_queue_rows_changed(self, *, next_row: int | None = None) -> None:
        self.tbl_queue.renumber_rows(self.COL_NO)
        if self.tbl_queue.rowCount() > 0:
            self._apply_populated_header_mode()
            if next_row is not None:
                self._select_row(min(max(0, int(next_row)), self.tbl_queue.rowCount() - 1))
        else:
            self._apply_empty_header_mode()
        self._refresh_meta_panel()
        self._sync_buttons()

    def _on_remove_selected(self) -> None:
        if self._download_is_running():
            return

        rows = sorted(self.tbl_queue.rows_for_removal(self.COL_CHECK), reverse=True)
        if not rows:
            return

        next_row = max(0, min(rows))
        keys = [self.tbl_queue.internal_key_at(r, self.COL_TITLE) for r in rows]
        for r in rows:
            self.tbl_queue.removeRow(r)

        key_set = {k for k in keys if k}
        self._jobs = [j for j in self._jobs if j.key not in key_set]
        for key in key_set:
            self._clear_job_state(key)

        self._finalize_queue_rows_changed(next_row=next_row)

    def _on_clear_list(self) -> None:
        if self._download_is_running():
            return

        for job in list(self._jobs):
            self._clear_job_state(job.key)

        self.tbl_queue.setRowCount(0)
        self._jobs = []
        self._meta_by_key = {}
        self._thumb_by_key = {}
        self._finalize_queue_rows_changed()

    def _append_job_row(self, job: _Job) -> int:
        row = self.tbl_queue.rowCount()
        self.tbl_queue.insertRow(row)

        self._build_job_row_static_cells(row, job)
        self._build_job_row_outputs_cell(row, job)
        self._build_job_row_quality_cell(row, job)
        self._build_job_row_formats_cell(row, job)
        self._build_job_row_audio_cell(row, job)
        self._build_job_row_status_cell(row, job)
        self._finalize_appended_job_row(row)
        return row

    def _build_job_row_static_cells(self, row: int, job: _Job) -> None:
        self.tbl_queue.setCellWidget(
            row,
            self.COL_CHECK,
            self.tbl_queue.make_checkbox_cell(on_changed=self._sync_buttons),
        )

        it_no = QtWidgets.QTableWidgetItem(str(row + 1))
        it_no.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
        self.tbl_queue.setItem(row, self.COL_NO, it_no)

        it_title = QtWidgets.QTableWidgetItem(tr("common.loading"))
        it_title.setData(QtCore.Qt.ItemDataRole.UserRole, job.key)
        self.tbl_queue.setItem(row, self.COL_TITLE, it_title)

        it_link = QtWidgets.QTableWidgetItem(self._short_link_text(job.url))
        it_link.setToolTip(job.url)
        it_link.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter))
        self.tbl_queue.setItem(row, self.COL_SOURCE, it_link)

    def _build_job_row_outputs_cell(self, row: int, job: _Job) -> None:
        type_items = [tr("down.select.type.video"), tr("down.select.type.audio")]
        btn_types = self.tbl_queue.make_multi_select_field(
            internal_key=job.key,
            items=type_items,
            selected=[type_items[0]] if job.output_types else [],
            placeholder=tr("down.select.outputs.placeholder"),
            on_changed=self._on_outputs_changed,
            )
        self.tbl_queue.setCellWidget(row, self.COL_OUTPUTS, btn_types)

    def _build_job_row_quality_cell(self, row: int, job: _Job) -> None:
        cb_quality = self.tbl_queue.make_simple_combo(
            internal_key=job.key,
            items=self._quality_items_for_job(job),
            on_changed=self._on_quality_changed,
            )
        self.tbl_queue.setCellWidget(row, self.COL_QUALITY, cb_quality)

    def _build_job_row_formats_cell(self, row: int, job: _Job) -> None:
        btn_exts = self.tbl_queue.make_multi_select_field(
            internal_key=job.key,
            items=list(self._format_items(job.output_types)),
            selected=list(job.output_exts or []),
            placeholder=tr("down.select.formats.placeholder"),
            on_changed=self._on_formats_changed,
            )
        self.tbl_queue.setCellWidget(row, self.COL_FORMATS, btn_exts)

    def _build_job_row_audio_cell(self, row: int, job: _Job) -> None:
        cb_audio = self.tbl_queue.make_audio_track_combo(
            internal_key=job.key,
            default_text=tr("down.select.audio_track.default"),
            on_changed=self._on_audio_changed,
            enabled=False,
            )
        self.tbl_queue.setCellWidget(row, self.COL_LANGUAGE, cb_audio)

    def _build_job_row_status_cell(self, row: int, job: _Job) -> None:
        it_status = QtWidgets.QTableWidgetItem("")
        it_status.setTextAlignment(int(QtCore.Qt.AlignmentFlag.AlignCenter))
        self.tbl_queue.setItem(row, self.COL_STATUS, it_status)
        self._set_job_status(job.key, job.status)

    def _finalize_appended_job_row(self, row: int) -> None:
        self.tbl_queue.setRowHeight(row, int(self._ui.control_min_h + 8))
        if self.tbl_queue.rowCount() == 1:
            self._apply_populated_header_mode()
        else:
            self.tbl_queue.schedule_populated_header_refresh(
                is_active=lambda: self._header_mode == "populated",
                reapply=self._apply_populated_header_mode,
            )

    def _select_row(self, row: int) -> None:
        if row < 0 or row >= self.tbl_queue.rowCount():
            return
        self.tbl_queue.setCurrentCell(row, self.COL_TITLE)
        self.tbl_queue.selectRow(row)
        self.tbl_queue.scrollToItem(
            self.tbl_queue.item(row, self.COL_TITLE),
            QtWidgets.QAbstractItemView.PositionAtCenter,
        )

    def _selected_job_key(self) -> str:
        return self.tbl_queue.selected_internal_key(self.COL_TITLE)

    def _on_selection_changed(self) -> None:
        self._refresh_meta_panel()
        self._update_open_in_browser_state()

    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if col != self.COL_SOURCE:
            return
        mods = QtWidgets.QApplication.keyboardModifiers()
        if not (mods & QtCore.Qt.KeyboardModifier.ControlModifier):
            return
        job = self._job_for_key(self.tbl_queue.internal_key_at(row, self.COL_TITLE))
        if job is None:
            return
        url = str(job.url or "").strip()
        if not url:
            return
        if "://" not in url:
            url = "https://" + url
        if not open_external_url(url):
            _LOG.error("Opening the selected URL failed. url=%s", sanitize_url_for_log(url))

    def _apply_meta_ui(self, meta: dict[str, Any], *, job_key: str = "") -> None:
        self._apply_meta_value_labels(meta)
        self._apply_meta_description(meta)
        self._refresh_meta_value_elision()

        key = str(job_key or self._selected_job_key() or "").strip()
        thumb_url = str(meta.get("thumbnail_url") or meta.get("thumbnail") or "").strip()
        self._set_thumbnail(key, thumb_url)

    @staticmethod
    def _format_meta_count(value: Any) -> str:
        if isinstance(value, bool) or value is None:
            return "-"
        if isinstance(value, (int, float)):
            try:
                return f"{int(value):,}".replace(",", " ")
            except (OverflowError, TypeError, ValueError):
                return str(int(value))
        return "-"

    @staticmethod
    def _meta_upload_date_text(meta: dict[str, Any]) -> str:
        raw = str(meta.get("upload_date") or "").strip()
        if len(raw) == 8 and raw.isdigit():
            return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        return raw or "-"

    @staticmethod
    def _meta_description_text(meta: dict[str, Any]) -> str:
        err_key = meta.get("_error_key")
        err_params = meta.get("_error_params") if isinstance(meta.get("_error_params"), dict) else {}
        if err_key:
            try:
                desc = tr(str(err_key), **(err_params or {}))
            except (RuntimeError, TypeError, ValueError, KeyError):
                desc = str(err_key)
        else:
            desc = str(meta.get("description") or "").strip()

        if not desc:
            return "-"

        if len(desc) > _DESCRIPTION_MAX_CHARS:
            return (
                desc[: _DESCRIPTION_MAX_CHARS - 3] + "..."
                if _DESCRIPTION_MAX_CHARS > 3
                else desc[:_DESCRIPTION_MAX_CHARS]
            )
        return desc

    def _apply_meta_value_labels(self, meta: dict[str, Any]) -> None:
        service = str(meta.get("extractor") or meta.get("service") or "-")
        title = str(meta.get("title") or meta.get("id") or "-")
        uploader = str(meta.get("uploader") or meta.get("channel") or "-").strip() or "-"
        duration = meta.get("duration")
        est_size = meta.get("estimated_size") or meta.get("filesize") or meta.get("filesize_approx")

        self._set_meta_value_text(self.lbl_service, service or "-")
        self._set_meta_value_text(self.lbl_title, title or "-")
        self._set_meta_value_text(self.lbl_channel, uploader)
        self._set_meta_value_text(self.lbl_upload_date, self._meta_upload_date_text(meta))
        self._set_meta_value_text(
            self.lbl_duration,
            format_hms(int(duration)) if isinstance(duration, (int, float)) else "-",
        )
        self._set_meta_value_text(
            self.lbl_est_size,
            format_bytes(int(est_size)) if isinstance(est_size, (int, float)) else "-",
        )
        self._set_meta_value_text(self.lbl_views, self._format_meta_count(meta.get("view_count")))
        self._set_meta_value_text(self.lbl_likes, self._format_meta_count(meta.get("like_count")))

    def _apply_meta_description(self, meta: dict[str, Any]) -> None:
        desc = self._meta_description_text(meta)
        self.lbl_description.setToolTip(desc if len(desc) > 120 and desc != "-" else "")
        self.lbl_description.setText(desc)

    def _set_thumbnail(self, job_key: str, url: str) -> None:
        if not job_key:
            self._show_thumbnail_placeholder()
            return

        cached = self._thumb_by_key.get(job_key)
        if isinstance(cached, QtGui.QPixmap):
            self._apply_pixmap(cached)
            return

        if not url:
            self._show_thumbnail_placeholder_if_active(job_key)
            return

        if job_key in self._thumb_reply_by_key:
            return

        qurl = QtCore.QUrl(url)
        if not qurl.isValid():
            self._show_thumbnail_placeholder_if_active(job_key)
            return

        self._request_thumbnail(job_key, qurl)

    def _is_active_thumbnail_target(self, job_key: str) -> bool:
        return self._active_preview_key() == job_key or self._selected_job_key() == job_key

    def _show_thumbnail_placeholder_if_active(self, job_key: str) -> None:
        if self._is_active_thumbnail_target(job_key):
            self._show_thumbnail_placeholder()

    def _request_thumbnail(self, job_key: str, qurl: QtCore.QUrl) -> None:
        req = QtNetwork.QNetworkRequest(qurl)
        req.setRawHeader(b"User-Agent", AppMeta.NAME.encode("utf-8", errors="ignore"))
        reply = self._net.get(req)
        self._thumb_reply_by_key[job_key] = reply
        reply.finished.connect(lambda _job_key=job_key, _reply=reply: self._on_thumbnail_reply(_job_key, _reply))

    def _on_thumbnail_reply(self, job_key: str, reply: QtNetwork.QNetworkReply) -> None:
        self._thumb_reply_by_key.pop(job_key, None)
        no_error = getattr(
            getattr(QtNetwork.QNetworkReply, "NetworkError", None),
            "NoError",
            getattr(QtNetwork.QNetworkReply, "NoError", None),
        )
        if reply.error() != no_error:
            reply.deleteLater()
            self._show_thumbnail_placeholder_if_active(job_key)
            return

        raw_data = cast(QtCore.QByteArray, reply.readAll())
        data = bytes(raw_data.data())
        reply.deleteLater()

        pm = QtGui.QPixmap()
        if not pm.loadFromData(data):
            self._show_thumbnail_placeholder_if_active(job_key)
            return

        self._thumb_by_key[job_key] = pm
        if self._is_active_thumbnail_target(job_key):
            self._apply_pixmap(pm)

    def _apply_pixmap(self, pm: QtGui.QPixmap) -> None:
        if pm.isNull():
            self._show_thumbnail_placeholder()
            return
        size = self.lbl_thumbnail.size()
        scaled = pm.scaled(
            size,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self._set_thumb_empty(False)
        self.lbl_thumbnail.setText("")
        self.lbl_thumbnail.setPixmap(scaled)

    def _start_probe(self, job: _Job) -> None:
        meta = self._meta_by_key.get(job.key)
        if isinstance(meta, dict) and not meta.get("_error_key"):
            self._refresh_row_from_meta(job.key)
            self._set_job_status(job.key, "queued")
            self._refresh_meta_panel()
            return

        self._start_probe_request(job.key, job.url)

    def _start_probe_request(self, job_key: str, url: str) -> None:
        if not self._network_available():
            self._meta_by_key[job_key] = {
                "_error_key": "error.down.network_offline",
                "_error_params": {},
            }
            if self._row_for_key(job_key) >= 0:
                self._set_job_status(job_key, "offline")
            _LOG.debug("Downloader probe blocked. reason=offline job_key=%s", sanitize_url_for_log(job_key))
            self._refresh_meta_panel()
            return

        if self._probe_is_running(job_key):
            _LOG.debug("Downloader probe skipped. reason=already_running job_key=%s", sanitize_url_for_log(job_key))
            return

        coord = self.coordinator()
        if coord is None:
            return

        _LOG.debug(
            "Downloader probe request started. job_key=%s url=%s",
            sanitize_url_for_log(job_key),
            sanitize_url_for_log(url),
        )
        coord.start_probe(job_key=job_key, url=url)

        if self._row_for_key(job_key) >= 0:
            self._set_job_status(job_key, "probing")

    def _probe_target_relevant(self, job_key: str) -> tuple[bool, bool]:
        has_row = self._row_for_key(job_key) >= 0
        keep_state = bool(has_row or job_key == self._active_preview_key())
        return has_row, keep_state

    def on_probe_ready(self, job_key: str, meta: dict[str, Any]) -> None:
        has_row, keep_state = self._probe_target_relevant(job_key)
        if not keep_state:
            self._clear_job_state(job_key)
            return

        if isinstance(meta, dict):
            self._meta_by_key[job_key] = meta

        if has_row:
            self._refresh_row_from_meta(job_key)
            self._set_job_status(job_key, "queued")

        _LOG.debug(
            "Downloader probe ready. job_key=%s title=%s",
            sanitize_url_for_log(job_key),
            str((meta or {}).get("title") or (meta or {}).get("id") or ""),
        )
        self._refresh_meta_panel()

    def on_probe_error(self, job_key: str, err_key: str, params: dict[str, Any]) -> None:
        has_row, keep_state = self._probe_target_relevant(job_key)
        if not keep_state:
            self._clear_job_state(job_key)
            return

        if has_row:
            self._set_job_status(job_key, "error")

        meta = self._meta_by_key.setdefault(job_key, {})
        if isinstance(meta, dict):
            meta["_error_key"] = str(err_key)
            meta["_error_params"] = (params or {})

        _LOG.debug(
            "Downloader probe error. job_key=%s detail=%s",
            sanitize_url_for_log(job_key),
            str((params or {}).get("detail") or ""),
        )
        self._refresh_meta_panel()

    def _refresh_row_from_meta(self, job_key: str) -> None:
        meta = self._meta_by_key.get(job_key)
        if not isinstance(meta, dict):
            return

        r = self._row_for_key(job_key)
        if r < 0:
            return

        title = str(meta.get("title") or meta.get("id") or "").strip()
        if title:
            it = self.tbl_queue.item(r, self.COL_TITLE)
            if it is not None:
                it.setText(title)
                it.setToolTip(title)

        url = str(meta.get("webpage_url") or meta.get("original_url") or "").strip() or job_key
        it_link = self.tbl_queue.item(r, self.COL_SOURCE)
        if it_link is not None:
            it_link.setText(self._short_link_text(url))
            it_link.setToolTip(url)

        self._update_audio_tracks_row(r, meta)
        job = self._job_for_key(job_key)
        if job is not None:
            self._refresh_quality_row_option(r, job)
        self.tbl_queue.schedule_populated_header_refresh(
            is_active=lambda: self._header_mode == "populated",
            reapply=self._apply_populated_header_mode,
        )

    def _update_audio_tracks_row(self, row: int, meta: dict[str, Any]) -> None:
        job = self._job_for_key(self.tbl_queue.internal_key_at(row, self.COL_TITLE))
        codes = self.tbl_queue.update_audio_tracks(
            row=row,
            col=self.COL_LANGUAGE,
            meta=meta,
            default_text=tr("down.select.audio_track.default"),
            preferred_lang_code=job.audio_lang if job is not None else None,
            internal_key=job.key if job is not None else None,
        )
        self.tbl_queue.apply_probe_diag_notice(row=row, col=self.COL_LANGUAGE, status_col=self.COL_STATUS, meta=meta)

        cb_audio = self.tbl_queue.combo_at(row, self.COL_LANGUAGE)
        if isinstance(cb_audio, QtWidgets.QComboBox):
            cb_audio.setEnabled(bool(len(codes) > 2))

    def _row_for_key(self, key: str) -> int:
        return self.tbl_queue.row_for_internal_key(self.COL_TITLE, key)

    def _job_for_key(self, key: str) -> _Job | None:
        idx = self._find_job_index(key)
        return self._jobs[idx] if idx >= 0 else None

    def _job_key_from_sender(self) -> str:
        sender = self.sender()
        if not isinstance(sender, QtCore.QObject):
            return ""
        return str(sender.property("internal_key") or "").strip()

    def _on_outputs_changed(self, selected_labels: list[str]) -> None:
        job_key = self._job_key_from_sender()
        job = self._job_for_key(job_key)
        if job is None:
            return

        video_lbl = tr("down.select.type.video")
        audio_lbl = tr("down.select.type.audio")

        types: list[str] = []
        for s in selected_labels or []:
            if s == video_lbl and "video" not in types:
                types.append("video")
            if s == audio_lbl and "audio" not in types:
                types.append("audio")

        if not types:
            types = ["video"]

        job.output_types = types
        row = self._row_for_key(job.key)
        if row >= 0:
            self._refresh_row_options(row)

    def _refresh_row_options(self, row: int) -> None:
        job = self._job_for_key(self.tbl_queue.internal_key_at(row, self.COL_TITLE))
        if job is None:
            return

        self._refresh_quality_row_option(row, job)
        self._rebuild_formats_button(row)
        self._refresh_audio_row_option(row, job)
        self._schedule_queue_header_refresh()

    def _refresh_quality_row_option(self, row: int, job: _Job) -> None:
        cb_quality = self.tbl_queue.combo_at(row, self.COL_QUALITY)
        if not isinstance(cb_quality, QtWidgets.QComboBox):
            return

        default_quality = DownloadPolicy.download_ui_default_quality()
        previous_value = (
            str(job.quality or cb_quality.currentText() or default_quality).strip().lower()
            or default_quality
        )
        items = self._quality_items_for_job(job)
        selected_value = previous_value if previous_value in {x.lower() for x in items} else default_quality

        cb_quality.blockSignals(True)
        cb_quality.clear()
        cb_quality.addItems(items)
        match_flags = QtCore.Qt.MatchFlag.MatchFixedString | QtCore.Qt.MatchFlag.MatchCaseSensitive
        match_index = max(0, cb_quality.findText(selected_value, match_flags))
        if match_index < 0:
            match_index = 0
        cb_quality.setCurrentIndex(match_index)
        cb_quality.blockSignals(False)
        job.quality = str(cb_quality.currentText() or DownloadPolicy.download_ui_default_quality()).strip().lower()

    def _refresh_audio_row_option(self, row: int, job: _Job) -> None:
        cb_audio = self.tbl_queue.combo_at(row, self.COL_LANGUAGE)
        if not isinstance(cb_audio, QtWidgets.QComboBox):
            return

        meta = self._meta_by_key.get(job.key) or {}
        self._update_audio_tracks_row(row, meta if isinstance(meta, dict) else {})

    @staticmethod
    def _job_output_types(job: _Job) -> list[str]:
        return [str(x).strip().lower() for x in (job.output_types or []) if str(x).strip()]

    def _quality_items_for_job(self, job: _Job) -> list[str]:
        output_types = self._job_output_types(job)
        only_audio = bool(output_types) and "audio" in output_types and "video" not in output_types
        meta = self._meta_by_key.get(job.key)
        meta_dict = meta if isinstance(meta, dict) else None

        if only_audio:
            bitrates = DownloadService.available_audio_bitrates(meta_dict)
            if bitrates:
                return ["Auto", *[f"{int(v)}k" for v in bitrates if int(v) > 0]]
            return ["Auto", "320k", "256k", "192k", "128k"]

        heights = DownloadService.available_video_heights(
            meta_dict,
            min_h=Config.downloader_min_video_height(),
            max_h=Config.downloader_max_video_height(),
        )
        if heights:
            return ["Auto", *[f"{int(v)}p" for v in heights if int(v) > 0]]

        min_h = Config.downloader_min_video_height()
        max_h = Config.downloader_max_video_height()
        fallback_heights = [4320, 2160, 1440, 1080, 720, 480, 360, 240, 144]
        filtered = [h for h in fallback_heights if min_h <= h <= max_h]
        return ["Auto", *[f"{int(v)}p" for v in filtered]] if filtered else ["Auto"]

    @staticmethod
    def _format_items(output_types: list[str]) -> list[str]:
        out: list[str] = []
        if "video" in (output_types or []):
            out.extend([str(x).strip().lower() for x in list(DownloadPolicy.DOWNLOAD_VIDEO_OUTPUT_EXTENSIONS)])
        if "audio" in (output_types or []):
            out.extend([str(x).strip().lower() for x in list(DownloadPolicy.DOWNLOAD_AUDIO_OUTPUT_EXTENSIONS)])
        seen = set()
        uniq: list[str] = []
        for x in out:
            if x and x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    def _rebuild_formats_button(self, row: int) -> None:
        job = self._job_for_key(self.tbl_queue.internal_key_at(row, self.COL_TITLE))
        if job is None:
            return

        items = list(self._format_items(job.output_types))
        keep = self._current_format_selection(row, job, items)
        btn = self.tbl_queue.make_multi_select_field(
            internal_key=job.key,
            items=items,
            selected=keep,
            placeholder=tr("down.select.formats.placeholder"),
            on_changed=self._on_formats_changed,
            )
        self.tbl_queue.setCellWidget(row, self.COL_FORMATS, btn)

        btn_cell = self.tbl_queue.multi_select_field_at(row, self.COL_FORMATS)
        job.output_exts = [
            str(x).strip().lower()
            for x in ((btn_cell.property("selected_items") if btn_cell is not None else []) or [])
        ]

    def _current_format_selection(self, row: int, job: _Job, items: list[str]) -> list[str]:
        w = self.tbl_queue.multi_select_field_at(row, self.COL_FORMATS)
        prev = list(w.property("selected_items") or []) if w is not None else []
        keep = [str(x).strip().lower() for x in prev if str(x).strip().lower() in items]
        if keep:
            return keep
        return [str(x).strip().lower() for x in job.output_exts if str(x).strip().lower() in items]

    def _on_quality_changed(self, _index: int = -1) -> None:
        job_key = self._job_key_from_sender()
        job = self._job_for_key(job_key)
        if job is None:
            return
        row = self._row_for_key(job_key)
        cb = self.tbl_queue.combo_at(row, self.COL_QUALITY) if row >= 0 else None
        if isinstance(cb, QtWidgets.QComboBox):
            job.quality = str(cb.currentText() or DownloadPolicy.download_ui_default_quality()).strip().lower()
            self._schedule_queue_header_refresh()

    def _on_formats_changed(self, selected: list[str]) -> None:
        job_key = self._job_key_from_sender()
        job = self._job_for_key(job_key)
        if job is None:
            return
        job.output_exts = [str(x).strip().lower() for x in (selected or [])]
        self._schedule_queue_header_refresh()

    def _on_audio_changed(self, _index: int = -1) -> None:
        job_key = self._job_key_from_sender()
        job = self._job_for_key(job_key)
        if job is None:
            return
        row = self._row_for_key(job_key)
        if row < 0:
            return
        job.audio_lang = (
            normalize_lang_code(
                self.tbl_queue.audio_lang_code_at(row, self.COL_LANGUAGE),
                drop_region=True,
            )
            or None
        )
        self._schedule_queue_header_refresh()

    def _on_download_selected(self) -> None:
        if self._download_is_running():
            return

        if not self._network_available():
            _LOG.debug("Downloader queue start blocked. reason=offline")
            dialogs.show_downloader_offline_dialog(self)
            return

        keys = self._collect_download_job_keys()
        self._queue_items = self._build_queue_items(keys)
        if not self._queue_items:
            return

        _LOG.debug("Downloader queue prepared. selected_jobs=%s queued_items=%s", len(keys), len(self._queue_items))
        self._download_aborted = False
        self._dup_apply_all_action = None
        self._start_next_download()

    def _start_next_download(self) -> None:
        if self._download_aborted or not self._queue_items:
            self._finish_queue()
            return

        item = self._queue_items.pop(0)
        key = self._set_active_download(item)
        coord = self.coordinator()
        if coord is None:
            return
        self._set_job_status(key, "downloading")
        coord.start_download(
            url=item.url,
            kind=item.kind,
            quality=item.quality,
            ext=item.ext,
            audio_lang=item.audio_lang,
        )
        self._reset_download_action_bar()
        self._sync_buttons()

    def _active_download_key(self) -> str:
        return self._active_download.key if self._active_download is not None else ""

    def _set_active_download(self, item: _DownloadQueueItem | None) -> str:
        self._active_download = item
        if item is None:
            return ""

        _LOG.debug(
            "Downloader item started. job_key=%s kind=%s quality=%s ext=%s audio_lang=%s",
            sanitize_url_for_log(item.key),
            item.kind,
            item.quality,
            item.ext,
            str(item.audio_lang or ""),
            )
        return item.key

    def _reset_download_action_bar(self) -> None:
        self.action_bar.reset()
        self.action_bar.set_busy(False)

    def _submit_duplicate_resolution(self, action: str, new_name: str = "") -> None:
        coord = self.coordinator()
        if coord is None:
            return
        coord.resolve_duplicate(action, new_name)

    def on_duplicate_check(self, title: str, expected: str) -> None:
        if self._closing:
            self._submit_duplicate_resolution("skip", "")
            return

        if self._dup_apply_all_action in ("skip", "overwrite"):
            _LOG.debug(
                "Downloader duplicate decision reused. action=%s expected=%s",
                self._dup_apply_all_action,
                Path(expected).name,
            )
            self._submit_duplicate_resolution(self._dup_apply_all_action, "")
            return

        suggested_name = Path(str(expected or "")).name or str(expected or "")

        action, new_name, apply_all = dialogs.ask_download_duplicate(self, title=title, suggested_name=suggested_name)
        if action == "rename":
            self._dup_apply_all_action = None
        elif apply_all and action in ("skip", "overwrite"):
            self._dup_apply_all_action = action

        _LOG.debug(
            "Downloader duplicate decision made. action=%s apply_all=%s expected=%s",
            action,
            bool(apply_all),
            suggested_name,
        )
        self._submit_duplicate_resolution(action, new_name)

    def on_progress_pct(self, pct: int) -> None:
        v = int(max(0, min(100, int(pct))))
        mapped = _PROGRESS_BASE_PCT + int(v * (_PROGRESS_SCALE_PCT / 100.0))
        self.action_bar.set_progress(mapped)

        active_key = self._active_download_key()
        if active_key:
            self._pct_by_key[active_key] = v
            base_status = self._status_base_by_key.get(active_key, "status.downloading")
            self._render_job_status_text(active_key, base_status)

    def on_stage_changed(self, stage: str) -> None:
        st = str(stage or "").strip().lower()
        active_key = self._active_download_key()

        if st == "postprocessing":
            self._set_job_status(active_key, "postprocessing")
            self._post_timer.start()
            return

        if st == "postprocessed":
            self._post_timer.stop()
            self.action_bar.set_progress(99)
            return

    def _tick_postprocess(self) -> None:
        cur = int(self.action_bar.progress.value())
        if cur < 90:
            self.action_bar.set_progress(90)
            return
        if cur < 99:
            self.action_bar.set_progress(cur + 1)
            return
        self._post_timer.stop()

    def on_download_finished(self, path: Path) -> None:
        self._post_timer.stop()
        self.action_bar.set_progress(100)
        active_key = self._active_download_key()
        self._set_job_status(active_key, "done")
        _LOG.debug(
            "Downloader item finished. job_key=%s path=%s remaining_items=%s",
            sanitize_url_for_log(active_key),
            Path(path).name,
            len(self._queue_items),
        )

        if self._closing:
            return

        if not self._queue_items and not self._download_aborted:
            try:
                if dialogs.ask_open_downloads_folder(self, str(path)):
                    _LOG.debug("Downloader completion action accepted. action=open_downloads_folder")
                    self._on_open_downloads_clicked()
                else:
                    _LOG.debug("Downloader completion action declined. action=open_downloads_folder")
            except (OSError, RuntimeError, ValueError) as ex:
                _LOG.debug("Downloader completion follow-up skipped. path=%s detail=%s", path, ex)

    def on_download_error(self, err_key: str, params: dict[str, Any]) -> None:
        self._post_timer.stop()
        is_network_error = self._is_network_error_key(err_key)
        active_key = self._active_download_key()
        self._set_job_status(active_key, "offline" if is_network_error else "error")
        if is_network_error:
            self._download_aborted = True
        _LOG.debug(
            "Downloader item failed. job_key=%s network_error=%s detail=%s remaining_items=%s",
            sanitize_url_for_log(active_key),
            bool(is_network_error),
            str((params or {}).get("detail") or ""),
            len(self._queue_items),
        )
        if self._closing:
            return
        dialogs.show_error(self, err_key, params or {})

    def _on_cancel_clicked(self) -> None:
        if not self._download_is_running():
            return
        if not dialogs.ask_cancel(self):
            return
        self._download_aborted = True
        _LOG.debug(
            "Downloader queue cancellation requested. job_key=%s",
            sanitize_url_for_log(self._active_download_key()),
        )
        coord = self.coordinator()
        if coord is not None:
            coord.cancel_download()
        self._post_timer.stop()

    def on_download_cancelled(self) -> None:
        self._post_timer.stop()
        self._set_job_status(self._active_download_key(), "queued")

    def on_download_cycle_finished(self) -> None:
        self._post_timer.stop()

        if self._download_aborted:
            self._finish_queue()
            return

        if self._queue_items:
            self._start_next_download()
            return

        self._finish_queue()

    def _finish_queue(self) -> None:
        was_aborted = bool(self._download_aborted)
        self._active_download = None
        self._queue_items = []
        self._download_aborted = False
        self._post_timer.stop()

        _LOG.debug("Downloader queue finished. aborted=%s", was_aborted)
        self.action_bar.set_busy(False)
        self.action_bar.reset()
        self._sync_buttons()

    def _render_job_status_text(self, key: str, status_key: str) -> None:
        row = self._row_for_key(key)
        if row < 0:
            return

        item = self.tbl_queue.item(row, self.COL_STATUS)
        if item is None:
            return

        pct = self._pct_by_key.get(key)
        item.setText(compose_status_text(status_key, pct, fallback=status_key))

    def _set_job_status(self, key: str, status: str) -> None:
        idx = self._find_job_index(key)
        if idx >= 0:
            self._jobs[idx].status = str(status or "").strip().lower()

        status_key = self._job_status_key(status)
        self._status_base_by_key[key] = status_key
        if is_terminal_status(status_key):
            if status_key == "status.done":
                self._pct_by_key[key] = 100
            else:
                self._pct_by_key.pop(key, None)
        elif not is_progress_status(status_key):
            self._pct_by_key.pop(key, None)

        self._render_job_status_text(key, status_key)
