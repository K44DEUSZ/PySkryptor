# app/view/ui_config.py
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from PyQt5 import QtCore, QtGui, QtWidgets, QtSvg

from app.model.config.app_config import AppConfig as Config

@dataclass(frozen=True)
class UIConfig:
    window_default_w: int = 1380
    window_default_h: int = 820
    window_min_w: int = 1280
    window_min_h: int = 720

    margin: int = 10
    spacing: int = 8
    grid_hspacing: int = 10
    grid_vspacing: int = 6

    control_min_h: int = 32
    control_min_w: int = 120
    button_big_h: int = 46
    button_min_w: int = 140

    field_label_gap: int = 4
    inline_spacing: int = 6
    option_spacing: int = 4
    option_row_min_h: int = 24
    combo_text_pad_x: int = 10
    combo_clear_gap: int = 6

    dialog_min_w: int = 560
    dialog_max_w: int = 760

    floating_shadow_blur: int = 18
    floating_shadow_offset_y: int = 4
    floating_shadow_alpha: int = 36
    floating_shadow_margin: int = 8

    spectrum_min_h: int = 46
    loading_min_w: int = 720
    loading_min_h: int = 420

    settings_label_min_w: int = 210

    hint_popup_max_text_w: int = 360
    hint_popup_edge_margin: int = 6
    hint_popup_anchor_gap_x: int = 8
    hint_popup_left_gap_x: int = 16
    hint_popup_avoid_gap_x: int = 10
    hint_icon_size: int = 14

    popup_anchor_gap_y: int = 2
    popup_edge_margin: int = 6
    popup_content_extra_h: int = 13
    popup_multiselect_content_margin_x: int = 2
    popup_multiselect_content_spacing: int = 2

    source_table_row_extra_h: int = 10
    source_table_row_min_h: int = 40
    source_table_header_check_min_w: int = 40
    source_table_header_check_pad_x: int = 18
    source_table_cell_margin_x: int = 4
    source_table_cell_margin_y: int = 2

    downloader_meta_description_min_h_factor: float = 3.0
    downloader_meta_description_max_h_factor: float = 5.2
    downloader_thumb_min_w_factor: float = 7.0
    downloader_thumb_max_w_factor: float = 9.0
    downloader_thumb_min_h_factor: float = 4.6
    downloader_thumb_max_h_factor: float = 6.8
    downloader_thumb_host_margin_mult: int = 2
    downloader_meta_group_max_h_factor: float = 11.5
    downloader_status_text_pad_w: int = 28
    downloader_status_max_w: int = 200
    downloader_empty_outputs_w: int = 128
    downloader_empty_quality_w: int = 98
    downloader_empty_formats_w: int = 112
    downloader_empty_language_w: int = 140
    downloader_outputs_col_fallback: int = 128
    downloader_outputs_col_pad: int = 4
    downloader_outputs_col_cap: int = 148
    downloader_outputs_col_floor: int = 136
    downloader_quality_col_fallback: int = 96
    downloader_quality_col_pad: int = 4
    downloader_quality_col_cap: int = 112
    downloader_quality_col_floor: int = 108
    downloader_formats_col_fallback: int = 112
    downloader_formats_col_pad: int = 4
    downloader_formats_col_cap: int = 136
    downloader_formats_col_floor: int = 128
    downloader_language_col_fallback: int = 136
    downloader_language_col_pad: int = 6
    downloader_language_col_cap: int = 164
    downloader_language_col_floor: int = 156
    downloader_language_col_extra_w: int = 10
    downloader_status_col_cap: int = 220
    downloader_header_fit_padding: int = 18

    about_logo_max_w_ratio: float = 0.42
    about_logo_max_w_cap: int = 520
    about_logo_max_w_floor: int = 240
    about_logo_max_h_ratio: float = 0.55
    about_logo_max_h_cap: int = 420
    about_logo_max_h_floor: int = 200
    about_left_panel_max_w_ratio: float = 0.45
    about_text_browser_height_pad: int = 6

_DEFAULT_UI = UIConfig()

def _coerce_cfg(obj: Any) -> Optional[UIConfig]:
    if obj is None:
        return None
    if isinstance(obj, UIConfig):
        return obj

    keys = {f.name for f in fields(UIConfig)}
    data: dict[str, Any] = {}

    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                data[key] = obj.get(key)
    else:
        for key in keys:
            if hasattr(obj, key):
                data[key] = getattr(obj, key)

    if not data:
        return None

    merged = {key: data.get(key, getattr(_DEFAULT_UI, key)) for key in keys}
    try:
        return UIConfig(**merged)
    except Exception:
        return None

# ----- Core helpers -----
def enable_styled_background(w: QtWidgets.QWidget) -> None:
    w.setAttribute(QtCore.Qt.WA_StyledBackground, True)

def ui(widget: Optional[QtWidgets.QWidget]) -> UIConfig:
    w = widget
    while w is not None:
        if hasattr(w, 'ui_config'):
            try:
                cfg = _coerce_cfg(w.ui_config())  # type: ignore[attr-defined]
                if cfg is not None:
                    return cfg
            except Exception:
                pass
        w = w.parentWidget()

    app = QtWidgets.QApplication.instance()
    cfg = _coerce_cfg(app.property('ui_config') if app is not None else None)
    return cfg if cfg is not None else _DEFAULT_UI

def button_required_width(button: QtWidgets.QPushButton) -> int:
    try:
        button.ensurePolished()
    except Exception:
        pass
    return max(int(button.minimumWidth()), int(button.sizeHint().width()), 0)


def capped_minimum_width_hint(widget: QtWidgets.QWidget, *, fallback: int, cap: int) -> int:
    try:
        widget.ensurePolished()
    except Exception:
        pass
    try:
        hint = int(widget.minimumSizeHint().width())
    except Exception:
        hint = 0
    return max(int(fallback), min(max(0, hint), int(cap)))


def install_app_event_filter(owner: QtCore.QObject, *, installed: bool) -> bool:
    app = QtWidgets.QApplication.instance()
    if app is None or installed:
        return bool(installed)
    app.installEventFilter(owner)
    return True


def bind_tracked_window(
    owner: QtCore.QObject,
    tracked_window: Optional[QtWidgets.QWidget],
    widget: Optional[QtWidgets.QWidget],
) -> Optional[QtWidgets.QWidget]:
    win = widget.window() if isinstance(widget, QtWidgets.QWidget) else None
    if win is tracked_window:
        return tracked_window if isinstance(tracked_window, QtWidgets.QWidget) else None

    if tracked_window is not None:
        tracked_window.removeEventFilter(owner)

    tracked = win if isinstance(win, QtWidgets.QWidget) else None
    if tracked is not None:
        tracked.installEventFilter(owner)
    return tracked


def contains_widget_chain(widget: Optional[QtWidgets.QWidget], *roots: Optional[QtWidgets.QWidget]) -> bool:
    valid_roots = [root for root in roots if isinstance(root, QtWidgets.QWidget)]
    current = widget
    while current is not None:
        for root in valid_roots:
            if current is root or root.isAncestorOf(current):
                return True
        current = current.parentWidget()
    return False

def normalize_network_status(value: str) -> str:
    raw = str(value or '').strip().lower()
    if raw in {'online', 'offline', 'checking'}:
        return raw
    return 'checking'

def read_network_status(parent: Optional[QtWidgets.QWidget]) -> str:
    getter = getattr(parent, 'network_status', None) if parent is not None else None
    if callable(getter):
        try:
            return normalize_network_status(getter())
        except Exception:
            pass
    return 'checking'

def open_local_path(target: str | Path) -> bool:
    try:
        path = Path(target).expanduser().resolve()
    except Exception:
        return False

    if os.name == 'nt':
        try:
            os.startfile(str(path))
            return True
        except Exception:
            return False

    return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path))))

def open_external_url(url: str) -> bool:
    target = str(url or '').strip()
    if not target:
        return False
    return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl(target)))

def _windows_dark_mode() -> bool:
    if sys.platform != 'win32':
        return False

    try:
        settings = QtCore.QSettings(
            'HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize',
            QtCore.QSettings.NativeFormat,
        )
        value = settings.value('AppsUseLightTheme', 1)
        return str(value).strip() in {'0', 'false', 'False'}
    except Exception:
        return False

def system_theme_key(app: Optional[QtWidgets.QApplication] = None) -> str:
    app = app or QtWidgets.QApplication.instance()

    if _windows_dark_mode():
        return 'dark'

    try:
        pal = app.palette() if app is not None else QtWidgets.QApplication.palette()
        return 'dark' if pal.color(QtGui.QPalette.Window).lightness() < 128 else 'light'
    except Exception:
        return 'light'

def apply_windows_dark_titlebar(w: QtWidgets.QWidget, theme: str | None = None) -> None:
    if sys.platform != 'win32':
        return

    app = QtWidgets.QApplication.instance()
    resolved = str(theme or (app.property('theme') if app is not None else '')).strip().lower()
    if resolved != 'dark':
        return

    try:
        import ctypes

        hwnd = int(w.winId())
        dwm = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        for attr in (20, 19):
            try:
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
                break
            except Exception:
                continue
    except Exception:
        return

# ----- Theme helpers -----
def _hex_to_rgba(value: str) -> QtGui.QColor:
    raw = str(value or '').strip()
    if raw.startswith('#'):
        return QtGui.QColor(raw)

    match = re.match(r'rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)', raw, flags=re.IGNORECASE)
    if match:
        r, g, b, a = (int(part) for part in match.groups())
        return QtGui.QColor(r, g, b, a)

    return QtGui.QColor(raw)

def _theme_tokens() -> dict[str, dict[str, str]]:
    return {
        'light': {
            '@WINDOW_BG@': '#F5F7F5',
            '@CARD_BG@': '#FFFFFF',
            '@CONTROL_BG@': '#FFFFFF',
            '@CONTROL_BG_HOVER@': '#F1F5F1',
            '@CONTROL_BG_DISABLED@': '#F2F4F2',
            '@HEADER_BG@': '#EFF3EF',
            '@BORDER_DEFAULT@': '#D6DDD6',
            '@BORDER_SUBTLE@': '#E1E6E1',
            '@TABLE_GRIDLINE@': '#D5DDD5',
            '@TABLE_HEADER_SEPARATOR@': '#CAD3CA',
            '@BORDER_ACTIVE@': '#70A82E',
            '@BORDER_PRESSED@': '#5E9326',
            '@STATE_READY_TEXT@': '#4E7821',
            '@STATE_ERROR_TEXT@': '#B8473F',
            '@TEXT_PRIMARY@': '#3A3F3A',
            '@TEXT_MUTED@': '#667066',
            '@TEXT_DISABLED@': '#9AA39A',
            '@TEXT_ON_ACCENT@': '#FFFFFF',
            '@TEXT_ON_ROW_SELECTED@': '#1F241F',
            '@TEXT_TAB_HOVER@': '#3A3F3A',
            '@PROGRESS_CHUNK_BG@': '#4E7821',
            '@SELECTION_BG@': 'rgba(112, 168, 46, 48)',
            '@ITEM_HOVER_BG@': 'rgba(58, 63, 58, 18)',
            '@ITEM_SELECTED_BG@': 'rgba(112, 168, 46, 48)',
            '@ITEM_SELECTED_HOVER_BG@': 'rgba(112, 168, 46, 56)',
            '@POPUP_SELECTED_BG@': 'rgba(112, 168, 46, 20)',
            '@POPUP_SELECTED_HOVER_BG@': 'rgba(112, 168, 46, 28)',
            '@TOGGLE_CHECKED_BG@': 'rgba(112, 168, 46, 26)',
            '@TOGGLE_CHECKED_HOVER_BG@': 'rgba(112, 168, 46, 34)',
            '@CLEAR_HOVER_BG@': 'rgba(112, 168, 46, 22)',
            '@CLEAR_PRESSED_BG@': 'rgba(112, 168, 46, 32)',
            '@SCROLLBAR_BG@': 'rgba(58, 63, 58, 12)',
            '@SCROLLBAR_HANDLE_BG@': 'rgba(58, 63, 58, 45)',
            '@SCROLLBAR_HANDLE_HOVER_BG@': 'rgba(58, 63, 58, 65)',
            '@MENU_SELECTED_BG@': 'rgba(112, 168, 46, 40)',
            '@ICON_INFO@': '@ASSETS@/icons/info_light.svg',
            '@ICON_ARROW_DOWN@': '@ASSETS@/icons/arrow_down_light.svg',
            '@ICON_ARROW_UP@': '@ASSETS@/icons/arrow_up_light.svg',
            '@ICON_CHECKBOX_UNCHECKED@': '@ASSETS@/icons/checkbox_unchecked_light.svg',
            '@ICON_CHECKBOX_UNCHECKED_HOVER@': '@ASSETS@/icons/checkbox_unchecked_light_hover.svg',
            '@ICON_CHECKBOX_CHECKED@': '@ASSETS@/icons/checkbox_checked_light.svg',
            '@ICON_CHECKBOX_UNCHECKED_DISABLED@': '@ASSETS@/icons/checkbox_unchecked_light_disabled.svg',
            '@ICON_CHECKBOX_CHECKED_DISABLED@': '@ASSETS@/icons/checkbox_checked_light_disabled.svg',
            '@ICON_RADIO_UNCHECKED@': '@ASSETS@/icons/radio_unchecked_light.svg',
            '@ICON_RADIO_UNCHECKED_HOVER@': '@ASSETS@/icons/radio_unchecked_light_hover.svg',
            '@ICON_RADIO_CHECKED@': '@ASSETS@/icons/radio_checked_light.svg',
            '@ICON_RADIO_UNCHECKED_DISABLED@': '@ASSETS@/icons/radio_unchecked_light_disabled.svg',
            '@ICON_RADIO_CHECKED_DISABLED@': '@ASSETS@/icons/radio_checked_light_disabled.svg',
        },
        'dark': {
            '@WINDOW_BG@': '#121513',
            '@CARD_BG@': '#171B18',
            '@CONTROL_BG@': '#1A1F1B',
            '@CONTROL_BG_HOVER@': '#1F261F',
            '@CONTROL_BG_DISABLED@': '#141816',
            '@HEADER_BG@': '#141816',
            '@BORDER_DEFAULT@': '#2B332D',
            '@BORDER_SUBTLE@': '#232B25',
            '@TABLE_GRIDLINE@': '#364037',
            '@TABLE_HEADER_SEPARATOR@': '#425045',
            '@BORDER_ACTIVE@': '#70A82E',
            '@BORDER_PRESSED@': '#5E9326',
            '@STATE_READY_TEXT@': '#8FCD48',
            '@STATE_ERROR_TEXT@': '#F08F86',
            '@TEXT_PRIMARY@': '#E6EFE0',
            '@TEXT_MUTED@': '#AAB6A4',
            '@TEXT_DISABLED@': '#707B72',
            '@TEXT_ON_ACCENT@': '#F5FAEF',
            '@TEXT_ON_ROW_SELECTED@': '#F5FAEF',
            '@TEXT_TAB_HOVER@': '#D2E5BB',
            '@PROGRESS_CHUNK_BG@': '#4E7821',
            '@SELECTION_BG@': 'rgba(112, 168, 46, 70)',
            '@ITEM_HOVER_BG@': 'rgba(210, 229, 187, 22)',
            '@ITEM_SELECTED_BG@': 'rgba(112, 168, 46, 55)',
            '@ITEM_SELECTED_HOVER_BG@': 'rgba(112, 168, 46, 62)',
            '@POPUP_SELECTED_BG@': 'rgba(112, 168, 46, 30)',
            '@POPUP_SELECTED_HOVER_BG@': 'rgba(112, 168, 46, 40)',
            '@TOGGLE_CHECKED_BG@': 'rgba(112, 168, 46, 34)',
            '@TOGGLE_CHECKED_HOVER_BG@': 'rgba(112, 168, 46, 44)',
            '@CLEAR_HOVER_BG@': 'rgba(112, 168, 46, 28)',
            '@CLEAR_PRESSED_BG@': 'rgba(112, 168, 46, 40)',
            '@SCROLLBAR_BG@': 'rgba(230, 239, 224, 10)',
            '@SCROLLBAR_HANDLE_BG@': 'rgba(230, 239, 224, 26)',
            '@SCROLLBAR_HANDLE_HOVER_BG@': 'rgba(230, 239, 224, 40)',
            '@MENU_SELECTED_BG@': 'rgba(112, 168, 46, 55)',
            '@ICON_INFO@': '@ASSETS@/icons/info_dark.svg',
            '@ICON_ARROW_DOWN@': '@ASSETS@/icons/arrow_down_dark.svg',
            '@ICON_ARROW_UP@': '@ASSETS@/icons/arrow_up_dark.svg',
            '@ICON_CHECKBOX_UNCHECKED@': '@ASSETS@/icons/checkbox_unchecked_dark.svg',
            '@ICON_CHECKBOX_UNCHECKED_HOVER@': '@ASSETS@/icons/checkbox_unchecked_dark_hover.svg',
            '@ICON_CHECKBOX_CHECKED@': '@ASSETS@/icons/checkbox_checked_dark.svg',
            '@ICON_CHECKBOX_UNCHECKED_DISABLED@': '@ASSETS@/icons/checkbox_unchecked_dark_disabled.svg',
            '@ICON_CHECKBOX_CHECKED_DISABLED@': '@ASSETS@/icons/checkbox_checked_dark_disabled.svg',
            '@ICON_RADIO_UNCHECKED@': '@ASSETS@/icons/radio_unchecked_dark.svg',
            '@ICON_RADIO_UNCHECKED_HOVER@': '@ASSETS@/icons/radio_unchecked_dark_hover.svg',
            '@ICON_RADIO_CHECKED@': '@ASSETS@/icons/radio_checked_dark.svg',
            '@ICON_RADIO_UNCHECKED_DISABLED@': '@ASSETS@/icons/radio_unchecked_dark_disabled.svg',
            '@ICON_RADIO_CHECKED_DISABLED@': '@ASSETS@/icons/radio_checked_dark_disabled.svg',
        },
    }

_THEME_STYLE_TOKENS = _theme_tokens()

def theme_style_tokens(theme: str) -> dict[str, str]:
    key = 'dark' if str(theme or '').strip().lower() == 'dark' else 'light'
    assets = Config.ASSETS_DIR.resolve().as_posix()
    out: dict[str, str] = {}
    for token, value in _THEME_STYLE_TOKENS[key].items():
        out[token] = str(value).replace('@ASSETS@', assets)
    return out

def theme_color(theme: str, token: str) -> QtGui.QColor:
    value = theme_style_tokens(theme).get(str(token or '').strip(), '#000000')
    return _hex_to_rgba(value)

def render_theme_stylesheet(
    styles_dir: Path,
    theme_pref: str,
    *,
    app: Optional[QtWidgets.QApplication] = None,
) -> tuple[str, str]:
    pref = str(theme_pref or 'auto').strip().lower()
    theme = pref if pref in {'light', 'dark'} else system_theme_key(app)

    qss_path = Path(styles_dir) / 'style.qss'
    if not qss_path.exists():
        return theme, ''

    try:
        qss = qss_path.read_text(encoding='utf-8')
    except Exception:
        return theme, ''

    for token, value in theme_style_tokens(theme).items():
        qss = qss.replace(token, value)
    qss = qss.replace('@ASSETS@', Config.ASSETS_DIR.resolve().as_posix())
    return theme, qss

# ----- Icons -----
def _resolve_app_icon_path(theme: str | None = None) -> Optional[Path]:
    resolved = str(theme or '').strip().lower()
    if resolved not in {'light', 'dark'}:
        resolved = system_theme_key()

    candidates = [
        Config.ICONS_DIR / f'app_icon_{resolved}.svg',
        Config.ICONS_DIR / 'app_icon_light.svg',
        Config.ICONS_DIR / 'app_icon_dark.svg',
    ]
    for path in candidates:
        if path.exists():
            return path
    return None

def _render_svg_icon(path: Path, *, sizes: tuple[int, ...] = (16, 20, 24, 32, 48, 64, 128)) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    if not path.exists():
        return icon

    renderer = QtSvg.QSvgRenderer(str(path))
    if not renderer.isValid():
        return QtGui.QIcon(str(path))

    view_box = renderer.viewBoxF()
    src_w = float(view_box.width()) if view_box.width() > 0 else 1.0
    src_h = float(view_box.height()) if view_box.height() > 0 else 1.0

    for size in sizes:
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pm)
        ratio = min(float(size) / src_w, float(size) / src_h)
        draw_w = max(1.0, src_w * ratio)
        draw_h = max(1.0, src_h * ratio)
        x = (float(size) - draw_w) / 2.0
        y = (float(size) - draw_h) / 2.0
        renderer.render(painter, QtCore.QRectF(x, y, draw_w, draw_h))
        painter.end()
        icon.addPixmap(pm)
    return icon

def app_icon(theme: str | None = None) -> QtGui.QIcon:
    path = _resolve_app_icon_path(theme)
    return _render_svg_icon(path) if path is not None else QtGui.QIcon()

def status_icon(name: str, *, theme: str | None = None) -> QtGui.QIcon:
    resolved = str(theme or '').strip().lower()
    if resolved not in {'light', 'dark'}:
        resolved = system_theme_key()

    candidates = [
        Config.ICONS_DIR / f'{name}_{resolved}.svg',
        Config.ICONS_DIR / f'{name}.svg',
    ]
    for path in candidates:
        if path.exists():
            return _render_svg_icon(path)
    return QtGui.QIcon()

def logo_svg_path(theme: str | None = None) -> Optional[Path]:
    resolved = str(theme or '').strip().lower()
    if resolved not in {'light', 'dark'}:
        resolved = system_theme_key()

    candidates = [
        Config.IMAGES_DIR / f'logo_{resolved}.svg',
        Config.IMAGES_DIR / 'logo_light.svg',
        Config.IMAGES_DIR / 'logo_dark.svg',
    ]
    for path in candidates:
        if path.exists():
            return path
    return None

class LogoSvgLabel(QtWidgets.QLabel):
    def __init__(self, path: Path, *, object_name: str | None = None, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._renderer = QtSvg.QSvgRenderer(str(self._path))
        if object_name:
            self.setObjectName(object_name)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setScaledContents(False)
        self.update_for_bounds(360, 140)

    def update_for_bounds(self, max_w: int, max_h: int) -> None:
        width = max(1, int(max_w))
        height = max(1, int(max_h))

        if not self._renderer.isValid():
            self.setText('')
            self.setFixedSize(width, height)
            return

        view_box = self._renderer.viewBoxF()
        src_w = float(view_box.width()) if view_box.width() > 0 else float(width)
        src_h = float(view_box.height()) if view_box.height() > 0 else float(height)
        ratio = min(float(width) / src_w, float(height) / src_h)
        draw_w = max(1, int(round(src_w * ratio)))
        draw_h = max(1, int(round(src_h * ratio)))

        dpr = max(1.0, float(self.devicePixelRatioF()))
        pm = QtGui.QPixmap(max(1, int(round(draw_w * dpr))), max(1, int(round(draw_h * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pm)
        self._renderer.render(painter)
        painter.end()

        self.setPixmap(pm)
        self.setFixedSize(draw_w, draw_h)
        self.updateGeometry()

# ----- Layout and widget setup -----
def make_grid(columns: int, cfg: UIConfig | None = None) -> QtWidgets.QGridLayout:
    cfg = cfg or _DEFAULT_UI
    layout = QtWidgets.QGridLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(cfg.grid_hspacing)
    layout.setVerticalSpacing(cfg.grid_vspacing)
    for index in range(max(0, int(columns))):
        layout.setColumnStretch(index, 1)
    return layout

def repolish_widget(w: QtWidgets.QWidget | None) -> None:
    if w is None:
        return
    try:
        style = w.style()
        if style is not None:
            style.unpolish(w)
            style.polish(w)
    except Exception:
        pass
    w.update()

def sync_progress_text_role(progress_bar: QtWidgets.QProgressBar) -> None:
    role = "primary"
    maximum = int(progress_bar.maximum())
    minimum = int(progress_bar.minimum())
    if maximum > minimum:
        value = int(progress_bar.value())
        filled = int(round(((value - minimum) * 100.0) / float(maximum - minimum)))
        if filled >= 48:
            role = "accent"
    progress_bar.setProperty("progressTextRole", role)
    repolish_widget(progress_bar)

def apply_floating_shadow(w: QtWidgets.QWidget) -> QtWidgets.QGraphicsDropShadowEffect:
    cfg = ui(w)
    shadow = QtWidgets.QGraphicsDropShadowEffect(w)
    shadow.setBlurRadius(int(cfg.floating_shadow_blur))
    shadow.setOffset(0, int(cfg.floating_shadow_offset_y))
    shadow.setColor(QtGui.QColor(0, 0, 0, int(cfg.floating_shadow_alpha)))
    w.setGraphicsEffect(shadow)
    return shadow

def floating_shadow_margins(
    widget: Optional[QtWidgets.QWidget],
    *,
    extra: int = 0,
) -> tuple[int, int, int, int]:
    cfg = ui(widget)
    base = max(0, int(cfg.floating_shadow_margin))
    bottom = base + max(0, int(cfg.floating_shadow_offset_y)) + max(0, int(extra))
    return base, base, base, bottom

def _text_menu_label(name: str) -> str:
    from app.controller.support.localization import tr

    return tr(f"common.edit_menu.{name}")

def _text_menu_shortcut(shortcut: QtGui.QKeySequence | QtGui.QKeySequence.StandardKey) -> str:
    try:
        return QtGui.QKeySequence(shortcut).toString(QtGui.QKeySequence.NativeText)
    except Exception:
        return ""

def _has_clipboard_text() -> bool:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    clipboard = app.clipboard()
    if clipboard is None:
        return False
    mime = clipboard.mimeData()
    return bool(mime is not None and mime.hasText())

def _text_widget_has_content(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.text())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return bool(widget.toPlainText())
    return False

def _text_widget_has_selection(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.hasSelectedText())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return bool(widget.textCursor().hasSelection())
    return False

def _text_widget_can_undo(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.isUndoAvailable())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        try:
            return bool(widget.document().isUndoAvailable())
        except Exception:
            return False
    return False

def _text_widget_can_redo(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.isRedoAvailable())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        try:
            return bool(widget.document().isRedoAvailable())
        except Exception:
            return False
    return False

def _text_widget_is_read_only(widget: QtWidgets.QWidget) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        return bool(widget.isReadOnly())
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return bool(widget.isReadOnly())
    return True

def _text_widget_can_paste(widget: QtWidgets.QWidget) -> bool:
    if _text_widget_is_read_only(widget):
        return False
    if isinstance(widget, QtWidgets.QLineEdit):
        return _has_clipboard_text()
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        try:
            return bool(widget.canPaste())
        except Exception:
            return _has_clipboard_text()
    return False

def _delete_text_selection(widget: QtWidgets.QWidget) -> None:
    if _text_widget_is_read_only(widget) or not _text_widget_has_selection(widget):
        return
    if isinstance(widget, QtWidgets.QLineEdit):
        widget.del_()
        return
    if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        cursor = widget.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            widget.setTextCursor(cursor)

class _TextContextActionRow(QtWidgets.QFrame):
    triggered = QtCore.pyqtSignal()

    def __init__(
        self,
        *,
        label: str,
        shortcut_text: str = "",
        enabled: bool,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        cfg = ui(self)

        self._pressed = False
        self.setProperty("role", "textContextAction")
        self.setProperty("hovered", False)
        self.setProperty("pressed", False)
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor if enabled else QtCore.Qt.ArrowCursor))
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setEnabled(bool(enabled))
        self.setFixedHeight(max(int(cfg.control_min_h) - 2, 28))

        lay = QtWidgets.QHBoxLayout(self)
        setup_layout(lay, cfg=cfg, margins=(cfg.combo_text_pad_x, cfg.inline_spacing, cfg.combo_text_pad_x, cfg.inline_spacing), spacing=cfg.grid_hspacing)

        self._label = QtWidgets.QLabel(label, self)
        self._label.setProperty("role", "textContextActionLabel")
        self._label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        self._shortcut = QtWidgets.QLabel(shortcut_text, self)
        self._shortcut.setProperty("role", "textContextActionShortcut")
        self._shortcut.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
        self._shortcut.setVisible(bool(shortcut_text))

        lay.addWidget(self._label, 1)
        lay.addWidget(self._shortcut, 0)

    def _set_state(self, *, hovered: Optional[bool] = None, pressed: Optional[bool] = None) -> None:
        changed = False
        if hovered is not None and self.property("hovered") != bool(hovered):
            self.setProperty("hovered", bool(hovered))
            changed = True
        if pressed is not None and self.property("pressed") != bool(pressed):
            self.setProperty("pressed", bool(pressed))
            changed = True
        if changed:
            repolish_widget(self)

    def enterEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().enterEvent(event)
        if self.isEnabled():
            self._set_state(hovered=True)

    def leaveEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._set_state(hovered=False, pressed=False)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self.isEnabled() and event.button() == QtCore.Qt.LeftButton:
            self._pressed = True
            self._set_state(pressed=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self._pressed and event.button() == QtCore.Qt.LeftButton:
            self._pressed = False
            inside = self.rect().contains(event.pos())
            self._set_state(pressed=False, hovered=inside)
            if self.isEnabled() and inside:
                self.triggered.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

class _TextContextSeparator(QtWidgets.QFrame):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "textContextSeparator")
        self.setFixedHeight(1)

class _TextContextPopup(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__(None, QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setProperty("role", "textContextPopupHost")

        cfg = ui(self)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(*floating_shadow_margins(self))
        root.setSpacing(0)

        self._body = QtWidgets.QFrame(self)
        self._body.setProperty("role", "textContextPopup")
        enable_styled_background(self._body)
        apply_floating_shadow(self._body)

        self._content = QtWidgets.QVBoxLayout(self._body)
        setup_layout(self._content, cfg=cfg, margins=(cfg.inline_spacing, cfg.inline_spacing, cfg.inline_spacing, cfg.inline_spacing), spacing=cfg.option_spacing)

        root.addWidget(self._body)

        self._tracked_window: Optional[QtWidgets.QWidget] = None
        self._widget: Optional[QtWidgets.QWidget] = None
        self._app_filter_installed = False
        self._install_app_filter()

    def _install_app_filter(self) -> None:
        self._app_filter_installed = install_app_event_filter(self, installed=self._app_filter_installed)

    def _bind_window(self, widget: Optional[QtWidgets.QWidget]) -> None:
        self._tracked_window = bind_tracked_window(self, self._tracked_window, widget)

    def _contains_widget(self, widget: Optional[QtWidgets.QWidget]) -> bool:
        return contains_widget_chain(widget, self, self._body)

    def _clear_content(self) -> None:
        while self._content.count():
            item = self._content.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

    def _trigger_action(self, handler: Callable[[], None]) -> None:
        self.hide()
        if callable(handler):
            handler()

    def _add_action(self, label: str, shortcut_text: str, enabled: bool, handler: Callable[[], None]) -> None:
        row = _TextContextActionRow(label=label, shortcut_text=shortcut_text, enabled=enabled, parent=self._body)
        row.triggered.connect(lambda h=handler: self._trigger_action(h))
        self._content.addWidget(row)

    def _rebuild(self, widget: QtWidgets.QWidget) -> None:
        self._clear_content()
        for item in build_text_context_menu(widget):
            if item is None:
                self._content.addWidget(_TextContextSeparator(self._body))
                continue
            label, shortcut_text, enabled, handler = item
            self._add_action(label, shortcut_text, enabled, handler)

    def show_for_widget(self, widget: QtWidgets.QWidget, global_pos: QtCore.QPoint) -> None:
        self._widget = widget
        self._bind_window(widget)
        self._rebuild(widget)
        self.adjustSize()

        geom = self.frameGeometry()
        geom.moveTopLeft(global_pos)

        screen = QtWidgets.QApplication.screenAt(global_pos) or widget.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            if geom.right() > avail.right() - 6:
                geom.moveLeft(max(avail.left() + 6, avail.right() - geom.width() - 6))
            if geom.bottom() > avail.bottom() - 6:
                geom.moveTop(max(avail.top() + 6, avail.bottom() - geom.height() - 6))
            if geom.left() < avail.left() + 6:
                geom.moveLeft(avail.left() + 6)
            if geom.top() < avail.top() + 6:
                geom.moveTop(avail.top() + 6)

        self.move(geom.topLeft())
        self.show()
        self.raise_()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._widget = None

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not self.isVisible():
            return super().eventFilter(obj, event)

        if obj is self._tracked_window and event.type() in {
            QtCore.QEvent.Move,
            QtCore.QEvent.Resize,
            QtCore.QEvent.Hide,
            QtCore.QEvent.WindowDeactivate,
        }:
            self.hide()
        elif event.type() in {QtCore.QEvent.ApplicationDeactivate, QtCore.QEvent.WindowDeactivate}:
            self.hide()
        elif event.type() == QtCore.QEvent.KeyPress and isinstance(event, QtGui.QKeyEvent):
            if event.key() == QtCore.Qt.Key_Escape:
                self.hide()
                event.accept()
                return True
        elif event.type() in {QtCore.QEvent.MouseButtonPress, QtCore.QEvent.Wheel} and isinstance(
            event,
            (QtGui.QMouseEvent, QtGui.QWheelEvent),
        ):
            target = obj if isinstance(obj, QtWidgets.QWidget) else QtWidgets.QApplication.widgetAt(event.globalPos())
            if not self._contains_widget(target):
                self.hide()
        return super().eventFilter(obj, event)

_TEXT_CONTEXT_POPUP: Optional[_TextContextPopup] = None

def text_context_popup() -> _TextContextPopup:
    global _TEXT_CONTEXT_POPUP
    if _TEXT_CONTEXT_POPUP is None:
        _TEXT_CONTEXT_POPUP = _TextContextPopup()
    return _TEXT_CONTEXT_POPUP

def build_text_context_menu(widget: QtWidgets.QWidget) -> list[Any]:
    if not isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return []

    read_only = _text_widget_is_read_only(widget)
    has_selection = _text_widget_has_selection(widget)
    has_content = _text_widget_has_content(widget)
    can_paste = _text_widget_can_paste(widget)

    if read_only:
        return [
            (_text_menu_label("copy"), _text_menu_shortcut(QtGui.QKeySequence.Copy), has_selection, getattr(widget, "copy", None)),
            (
                _text_menu_label("select_all"),
                _text_menu_shortcut(QtGui.QKeySequence.SelectAll),
                has_content,
                getattr(widget, "selectAll", None),
            ),
        ]

    return [
        (_text_menu_label("undo"), _text_menu_shortcut(QtGui.QKeySequence.Undo), _text_widget_can_undo(widget), getattr(widget, "undo", None)),
        (_text_menu_label("redo"), _text_menu_shortcut(QtGui.QKeySequence.Redo), _text_widget_can_redo(widget), getattr(widget, "redo", None)),
        None,
        (_text_menu_label("cut"), _text_menu_shortcut(QtGui.QKeySequence.Cut), has_selection, getattr(widget, "cut", None)),
        (_text_menu_label("copy"), _text_menu_shortcut(QtGui.QKeySequence.Copy), has_selection, getattr(widget, "copy", None)),
        (_text_menu_label("paste"), _text_menu_shortcut(QtGui.QKeySequence.Paste), can_paste, getattr(widget, "paste", None)),
        (_text_menu_label("delete"), _text_menu_shortcut(QtGui.QKeySequence.Delete), has_selection, lambda: _delete_text_selection(widget)),
        None,
        (
            _text_menu_label("select_all"),
            _text_menu_shortcut(QtGui.QKeySequence.SelectAll),
            has_content,
            getattr(widget, "selectAll", None),
        ),
    ]

class _TextContextMenuFilter(QtCore.QObject):
    def __init__(self, widget: QtWidgets.QWidget) -> None:
        super().__init__(widget)
        self._widget = widget

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.ContextMenu and isinstance(event, QtGui.QContextMenuEvent):
            if not isinstance(self._widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
                return False
            text_context_popup().show_for_widget(self._widget, event.globalPos())
            return True
        return super().eventFilter(obj, event)

def install_text_context_menu(widget: QtWidgets.QWidget) -> None:
    if not isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
        return
    current = getattr(widget, "_text_context_menu_filter", None)
    if isinstance(current, _TextContextMenuFilter):
        return

    context_filter = _TextContextMenuFilter(widget)
    widget._text_context_menu_filter = context_filter
    widget.installEventFilter(context_filter)

    if isinstance(widget, QtWidgets.QAbstractScrollArea):
        viewport = widget.viewport()
        if viewport is not None:
            viewport.installEventFilter(context_filter)

class _SpinboxFocusProxy(QtCore.QObject):
    def __init__(self, spinbox: QtWidgets.QAbstractSpinBox) -> None:
        super().__init__(spinbox)
        self._spinbox = spinbox

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in {
            QtCore.QEvent.FocusIn,
            QtCore.QEvent.FocusOut,
            QtCore.QEvent.EnabledChange,
            QtCore.QEvent.Hide,
        }:
            QtCore.QTimer.singleShot(0, self._sync_focus_state)
        return super().eventFilter(obj, event)

    def _sync_focus_state(self) -> None:
        spinbox = self._spinbox
        focus_within = bool(spinbox.isEnabled() and spinbox.hasFocus())
        line_edit = spinbox.lineEdit()
        if line_edit is not None:
            focus_within = focus_within or bool(line_edit.hasFocus())
        if spinbox.property('focusWithin') != focus_within:
            spinbox.setProperty('focusWithin', focus_within)
            repolish_widget(spinbox)

def set_widget_style_role(
    w: QtWidgets.QWidget,
    *,
    chrome: str | None = None,
    ui_role: str | None = None,
) -> None:
    enable_styled_background(w)
    if chrome is not None:
        w.setProperty('chrome', str(chrome))
    if ui_role is not None:
        w.setProperty('role', str(ui_role))

def setup_control(w: QtWidgets.QWidget, *, min_h: int | None = None, min_w: int | None = None) -> None:
    cfg = ui(w)
    height = int(min_h if min_h is not None else cfg.control_min_h)
    width = int(min_w if min_w is not None else cfg.control_min_w)
    w.setMinimumHeight(height)
    w.setMinimumWidth(width)

def setup_button(
    btn: QtWidgets.QAbstractButton,
    *,
    chrome: str | None = 'action',
    min_h: int | None = None,
    min_w: int | None = None,
) -> None:
    set_widget_style_role(btn, chrome=chrome)
    btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
    btn.setFocusPolicy(QtCore.Qt.NoFocus)
    setup_control(btn, min_h=min_h, min_w=min_w)

def setup_combo(cb: QtWidgets.QComboBox, *, min_h: int | None = None, min_w: int | None = None) -> None:
    set_widget_style_role(cb, chrome='field', ui_role='combo')
    cb.setProperty('focusWithin', False)
    cb.setProperty('popupOpen', False)
    setup_control(cb, min_h=min_h, min_w=min_w)
    line_edit = cb.lineEdit()
    if line_edit is not None:
        install_text_context_menu(line_edit)
    sync_visual_state = getattr(cb, 'sync_visual_state', None)
    if callable(sync_visual_state):
        QtCore.QTimer.singleShot(0, sync_visual_state)

def setup_spinbox(sp: QtWidgets.QAbstractSpinBox, *, min_h: int | None = None, min_w: int | None = None) -> None:
    set_widget_style_role(sp, chrome='field', ui_role='spinbox')
    sp.setProperty('focusWithin', False)
    try:
        sp.setFrame(False)
    except Exception:
        pass

    focus_proxy = getattr(sp, '_focus_proxy', None)
    if not isinstance(focus_proxy, _SpinboxFocusProxy):
        focus_proxy = _SpinboxFocusProxy(sp)
        sp._focus_proxy = focus_proxy
        sp.installEventFilter(focus_proxy)
        line_edit = sp.lineEdit()
        if line_edit is not None:
            try:
                line_edit.setFrame(False)
            except Exception:
                pass
            try:
                line_edit.setAttribute(QtCore.Qt.WA_MacShowFocusRect, False)
            except Exception:
                pass
            line_edit.setStyleSheet(
                'QLineEdit {'
                ' background: transparent;'
                ' border: none;'
                ' border-radius: 0px;'
                ' padding: 0px;'
                ' margin: 0px;'
                ' }'
            )
            line_edit.installEventFilter(focus_proxy)
            install_text_context_menu(line_edit)
    setup_control(sp, min_h=min_h, min_w=min_w)

def setup_input(edit: QtWidgets.QLineEdit, *, placeholder: str | None = None, min_h: int | None = None) -> None:
    set_widget_style_role(edit, chrome='field', ui_role='input')
    if placeholder is not None:
        edit.setPlaceholderText(placeholder)
    setup_control(edit, min_h=min_h)
    install_text_context_menu(edit)

def setup_text_editor(
    edit: QtWidgets.QTextEdit | QtWidgets.QPlainTextEdit,
    *,
    placeholder: str | None = None,
) -> None:
    set_widget_style_role(edit, chrome='field', ui_role='textEditor')
    if placeholder is not None:
        edit.setPlaceholderText(placeholder)
    install_text_context_menu(edit)

def setup_label(
    label: QtWidgets.QLabel,
    *,
    role: str = 'fieldLabel',
    buddy: QtWidgets.QWidget | None = None,
) -> QtWidgets.QLabel:
    label.setProperty('role', role)
    if buddy is not None:
        label.setBuddy(buddy)
    return label

def setup_option_checkbox(
    cb: QtWidgets.QCheckBox,
    *,
    min_h: int | None = None,
) -> QtWidgets.QCheckBox:
    cfg = ui(cb)
    row_h = int(min_h if min_h is not None else cfg.option_row_min_h)
    cb.setMinimumHeight(row_h)
    cb.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    return cb

def build_field_stack(
    parent: QtWidgets.QWidget,
    label_text: str,
    content: QtWidgets.QWidget | QtWidgets.QLayout,
    *,
    buddy: QtWidgets.QWidget | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
    cfg = ui(parent)
    host, lay = build_layout_host(
        parent=parent,
        layout="vbox",
        margins=(0, 0, 0, 0),
        spacing=cfg.field_label_gap,
    )

    label = QtWidgets.QLabel(label_text, host)
    setup_label(label, buddy=buddy)
    lay.addWidget(label)

    if isinstance(content, QtWidgets.QLayout):
        lay.addLayout(content)
    else:
        lay.addWidget(content)

    return host, label

def setup_layout(
    layout: QtWidgets.QLayout,
    *,
    cfg: UIConfig | None = None,
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
) -> None:
    cfg = cfg or _DEFAULT_UI
    resolved_margins = tuple(int(v) for v in (margins or (cfg.margin, cfg.margin, cfg.margin, cfg.margin)))
    try:
        layout.setContentsMargins(*resolved_margins)
    except Exception:
        pass
    try:
        layout.setSpacing(int(cfg.spacing if spacing is None else spacing))
    except Exception:
        pass
    if hspacing is not None:
        try:
            layout.setHorizontalSpacing(int(hspacing))  # type: ignore[attr-defined]
        except Exception:
            pass
    if vspacing is not None:
        try:
            layout.setVerticalSpacing(int(vspacing))  # type: ignore[attr-defined]
        except Exception:
            pass
    if isinstance(layout, QtWidgets.QGridLayout):
        for index, stretch in (column_stretches or {}).items():
            try:
                layout.setColumnStretch(int(index), int(stretch))
            except Exception:
                pass

def build_layout_host(
    *,
    parent: QtWidgets.QWidget | None = None,
    layout: Literal["hbox", "vbox", "grid", "form"] = "vbox",
    margins: tuple[int, int, int, int] | None = None,
    spacing: int | None = None,
    hspacing: int | None = None,
    vspacing: int | None = None,
    column_stretches: dict[int, int] | None = None,
    object_name: str | None = None,
) -> tuple[QtWidgets.QWidget, QtWidgets.QLayout]:
    host = QtWidgets.QWidget(parent)
    if object_name:
        host.setObjectName(object_name)

    if layout == "hbox":
        root: QtWidgets.QLayout = QtWidgets.QHBoxLayout(host)
    elif layout == "grid":
        root = QtWidgets.QGridLayout(host)
    elif layout == "form":
        root = QtWidgets.QFormLayout(host)
    else:
        root = QtWidgets.QVBoxLayout(host)

    setup_layout(
        root,
        cfg=ui(parent or host),
        margins=margins,
        spacing=spacing,
        hspacing=hspacing,
        vspacing=vspacing,
        column_stretches=column_stretches,
    )
    return host, root
