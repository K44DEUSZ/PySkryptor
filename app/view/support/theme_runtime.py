# app/view/support/theme_runtime.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets, QtSvg

from app.model.config.app_config import AppConfig as Config
from app.view.ui_config import UIConfig, _DEFAULT_UI, _coerce_cfg

def _windows_dark_mode() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        settings = QtCore.QSettings(
            'HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize',
            QtCore.QSettings.Format.NativeFormat,
        )
        value = settings.value('AppsUseLightTheme', 1)
        return str(value).strip() in {'0', 'false', 'False'}
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False

def system_theme_key(app: QtWidgets.QApplication | None = None) -> str:
    app = app or QtWidgets.QApplication.instance()
    if _windows_dark_mode():
        return 'dark'
    try:
        pal = app.palette() if app is not None else QtWidgets.QApplication.palette()
        return 'dark' if pal.color(QtGui.QPalette.Window).lightness() < 128 else 'light'
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return 'light'

def active_theme_key(theme: str | None = None, *, app: QtWidgets.QApplication | None = None) -> str:
    resolved = str(theme or '').strip().lower()
    if resolved in {'light', 'dark'}:
        return resolved

    app = app or QtWidgets.QApplication.instance()
    app_theme = str(app.property('theme') if app is not None else '').strip().lower()
    if app_theme in {'light', 'dark'}:
        return app_theme

    return system_theme_key(app)

def apply_windows_dark_titlebar(w: QtWidgets.QWidget, theme: str | None = None) -> None:
    if sys.platform != 'win32':
        return
    resolved = active_theme_key(theme)
    if resolved != 'dark':
        return
    try:
        import ctypes

        hwnd = int(w.winId())
        dwm = ctypes.windll.dwmapi
        set_window_attribute = getattr(dwm, "DwmSetWindowAttribute", None)
        if set_window_attribute is None:
            return
        value = ctypes.c_int(1)
        for attr in (20, 19):
            try:
                set_window_attribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
                break
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                continue
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return

def _parse_hex_color(value: str) -> QtGui.QColor:
    return QtGui.QColor(str(value or '').strip())

def _color_with_alpha(color: QtGui.QColor, alpha: int | None = None) -> QtGui.QColor:
    out = QtGui.QColor(color)
    if alpha is not None:
        out.setAlpha(max(0, min(255, int(alpha))))
    return out

@dataclass(frozen=True)
class SpectrumPalette:
    """Resolved color set used by the live audio spectrum widget."""
    background: QtGui.QColor
    border: QtGui.QColor
    track: QtGui.QColor
    bar: QtGui.QColor

def app_palette_colors(theme: str) -> dict[str, QtGui.QColor]:
    resolved = active_theme_key(theme)
    return {
        'highlight': theme_color(resolved, '@ACTIVE_BG_STRONG@'),
        'link': theme_color(resolved, '@BORDER_ACTIVE@'),
        'link_visited': theme_color(resolved, '@BORDER_PRESSED@'),
        'highlighted_text': theme_color(resolved, '@TEXT_ON_ACTIVE@'),
    }

def floating_shadow_color(theme: str | None = None, *, app: QtWidgets.QApplication | None = None) -> QtGui.QColor:
    resolved = active_theme_key(theme, app=app)
    return theme_color(resolved, '@FLOATING_SHADOW@')

def spectrum_palette(
    state: str,
    *,
    theme: str | None = None,
    app: QtWidgets.QApplication | None = None,
) -> SpectrumPalette:
    resolved_theme = active_theme_key(theme, app=app)
    state_key = str(state or '').strip().lower() or 'idle'

    background = _color_with_alpha(theme_color(resolved_theme, '@CONTROL_BG@'))
    border = _color_with_alpha(theme_color(resolved_theme, '@BORDER_DEFAULT@'))
    track = _color_with_alpha(theme_color(resolved_theme, '@CONTROL_BG_HOVER@'))
    bar = _color_with_alpha(theme_color(resolved_theme, '@ACTIVE_BG_HOVER@'))

    if state_key == 'active':
        bar = _color_with_alpha(theme_color(resolved_theme, '@ACTIVE_BG_STRONG@'))
    elif state_key == 'paused':
        border = _color_with_alpha(theme_color(resolved_theme, '@BORDER_SUBTLE@'))
        track = _color_with_alpha(theme_color(resolved_theme, '@CONTROL_BG_DISABLED@'))
        bar = _color_with_alpha(theme_color(resolved_theme, '@TEXT_DISABLED@'), 180)
    elif state_key == 'disabled':
        background = _color_with_alpha(theme_color(resolved_theme, '@CONTROL_BG_DISABLED@'))
        border = _color_with_alpha(theme_color(resolved_theme, '@BORDER_SUBTLE@'))
        track = _color_with_alpha(theme_color(resolved_theme, '@CONTROL_BG_DISABLED@'))
        bar = _color_with_alpha(theme_color(resolved_theme, '@TEXT_DISABLED@'), 132)
    elif state_key == 'error':
        border = _color_with_alpha(theme_color(resolved_theme, '@TEXT_ERROR@'))
        bar = _color_with_alpha(theme_color(resolved_theme, '@TEXT_ERROR@'))

    return SpectrumPalette(background=background, border=border, track=track, bar=bar)

def _theme_tokens() -> dict[str, dict[str, str]]:
    return {
        'light': {
            '@APP_BG@': '#F4F7F4',
            '@PANEL_BG@': '#FFFFFF',
            '@POPUP_BG@': '#FBFCFB',
            '@HEADER_BG@': '#EEF3EE',
            '@CONTROL_BG@': '#FFFFFF',
            '@CONTROL_BG_HOVER@': '#F2F6F2',
            '@CONTROL_BG_DISABLED@': '#F2F5F2',
            '@FIELD_BG@': '#EFF3EF',
            '@FIELD_BG_HOVER@': '#F4F7F4',
            '@FIELD_BG_ACTIVE@': '#EDF5E5',
            '@CONTEXT_HOVER_BG@': '#14384038',
            '@CONTEXT_SELECTED_BG@': '#2E70A82E',
            '@ACTIVE_BG@': '#2470A82E',
            '@ACTIVE_BG_HOVER@': '#3070A82E',
            '@ACTIVE_BG_STRONG@': '#70A82E',
            '@BORDER_DEFAULT@': '#D4DDD4',
            '@BORDER_SUBTLE@': '#E1E6E1',
            '@BORDER_MUTED@': '#CAD3CA',
            '@BORDER_GRID@': '#D5DDD5',
            '@BORDER_ACTIVE@': '#70A82E',
            '@BORDER_PRESSED@': '#5E9326',
            '@TEXT_PRIMARY@': '#3A3F3A',
            '@TEXT_SECONDARY@': '#667066',
            '@TEXT_DISABLED@': '#9AA39A',
            '@TEXT_SUCCESS@': '#4E7821',
            '@TEXT_ERROR@': '#B8473F',
            '@TEXT_ON_ACTIVE@': '#FFFFFF',
            '@TEXT_ON_SELECTION@': '#1F241F',
            '@PROGRESS_FILL@': '#4E7821',
            '@SCROLLBAR_TRACK@': '#0C3A3F3A',
            '@SCROLLBAR_HANDLE@': '#2D3A3F3A',
            '@SCROLLBAR_HANDLE_HOVER@': '#413A3F3A',
            '@FLOATING_SHADOW@': '#24000000',
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
            '@APP_BG@': '#121513',
            '@PANEL_BG@': '#171B18',
            '@POPUP_BG@': '#1A201B',
            '@HEADER_BG@': '#141816',
            '@CONTROL_BG@': '#1A1F1B',
            '@CONTROL_BG_HOVER@': '#202720',
            '@CONTROL_BG_DISABLED@': '#141816',
            '@FIELD_BG@': '#151A16',
            '@FIELD_BG_HOVER@': '#1B211C',
            '@FIELD_BG_ACTIVE@': '#1E271B',
            '@CONTEXT_HOVER_BG@': '#16E6EFE0',
            '@CONTEXT_SELECTED_BG@': '#3770A82E',
            '@ACTIVE_BG@': '#2270A82E',
            '@ACTIVE_BG_HOVER@': '#2E70A82E',
            '@ACTIVE_BG_STRONG@': '#5E9326',
            '@BORDER_DEFAULT@': '#2B332D',
            '@BORDER_SUBTLE@': '#232B25',
            '@BORDER_MUTED@': '#425045',
            '@BORDER_GRID@': '#364037',
            '@BORDER_ACTIVE@': '#70A82E',
            '@BORDER_PRESSED@': '#5E9326',
            '@TEXT_PRIMARY@': '#E6EFE0',
            '@TEXT_SECONDARY@': '#AAB6A4',
            '@TEXT_DISABLED@': '#707B72',
            '@TEXT_SUCCESS@': '#8FCD48',
            '@TEXT_ERROR@': '#F08F86',
            '@TEXT_ON_ACTIVE@': '#F5FAEF',
            '@TEXT_ON_SELECTION@': '#F5FAEF',
            '@PROGRESS_FILL@': '#4E7821',
            '@SCROLLBAR_TRACK@': '#0AE6EFE0',
            '@SCROLLBAR_HANDLE@': '#1AE6EFE0',
            '@SCROLLBAR_HANDLE_HOVER@': '#28E6EFE0',
            '@FLOATING_SHADOW@': '#38000000',
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
    assets = Config.PATHS.ASSETS_DIR.resolve().as_posix()
    out: dict[str, str] = {}
    for token, value in _THEME_STYLE_TOKENS[key].items():
        out[token] = str(value).replace('@ASSETS@', assets)
    return out

def _ui_style_tokens(cfg: UIConfig) -> dict[str, str]:
    radius_l = max(int(cfg.radius_l), 2)
    radius_m = max(int(cfg.radius_m), 2)
    radius_s = max(int(cfg.radius_s), 2)
    pad_x_m = max(int(cfg.pad_x_m), 0)
    pad_x_l = max(int(cfg.pad_x_l), 0)
    pad_y_s = max(int(cfg.pad_y_s), 0)
    pad_y_m = max(int(cfg.pad_y_m), 0)
    pad_y_l = max(int(cfg.pad_y_l), 0)
    tab_min_h = max(int(cfg.control_min_h) - 2, 24)
    scroll_handle_min = max(int(cfg.option_row_min_h), 20)
    return {
        '@RADIUS_L@': f'{radius_l}px',
        '@RADIUS_M@': f'{radius_m}px',
        '@RADIUS_S@': f'{radius_s}px',
        '@SPACE_GROUPBOX_TOP@': f'{max(int(cfg.space_l) * 2 + 2, 0)}px',
        '@GROUPBOX_PADDING@': f'{pad_y_l * 2}px {pad_x_m}px {pad_x_m}px {pad_x_m}px',
        '@PAD_BUTTON@': f'{pad_y_s}px {pad_x_l}px',
        '@PAD_FIELD@': f'{pad_y_s}px {pad_x_m}px',
        '@PAD_FIELD_INLINE@': f'0px {pad_x_m}px',
        '@PAD_FIELD_TOOL@': f'0px {max(pad_x_m + 24, 0)}px 0px {pad_x_m}px',
        '@PAD_FIELD_CHROME@': f'0px {max(pad_x_m + 26, 0)}px 0px {pad_x_m}px',
        '@PAD_BLOCK@': f'{pad_x_m}px',
        '@PAD_POPUP@': f'{pad_y_m}px {pad_x_l}px',
        '@PAD_SECTION@': f'{pad_y_l}px {pad_x_m}px',
        '@PAD_TOOLTIP@': f'{pad_y_m}px {max(int(cfg.margin), 0)}px',
        '@PAD_MENU@': f'{pad_y_m}px',
        '@PAD_CHECK_ROW@': f'{pad_y_m}px 0px',
        '@SPACE_TAB_TOP@': f'{max(int(cfg.margin), 0)}px',
        '@TAB_MIN_HEIGHT@': f'{tab_min_h}px',
        '@PAD_TAB@': f'{pad_x_m}px {max(pad_x_l + 2, 0)}px',
        '@SCROLL_HANDLE_MIN@': f'{scroll_handle_min}px',
        '@TABLE_CHECK_INDICATOR_SIZE@': f'{max(int(cfg.table_check_indicator_size), 14)}px',
    }

def theme_color(theme: str, token: str) -> QtGui.QColor:
    value = theme_style_tokens(theme).get(str(token or '').strip(), '#000000')
    return _parse_hex_color(value)

def render_theme_stylesheet(
    styles_dir: Path,
    theme_pref: str,
    *,
    app: QtWidgets.QApplication | None = None,
) -> tuple[str, str]:
    pref = str(theme_pref or 'auto').strip().lower()
    theme = pref if pref in {'light', 'dark'} else system_theme_key(app)
    cfg = _coerce_cfg(app.property('ui_config') if app is not None else None)
    ui_cfg = cfg if cfg is not None else _DEFAULT_UI
    qss_path = Path(styles_dir) / 'style.qss'
    if not qss_path.exists():
        return theme, ''
    try:
        qss = qss_path.read_text(encoding='utf-8')
    except OSError:
        return theme, ''
    for token, value in theme_style_tokens(theme).items():
        qss = qss.replace(token, value)
    for token, value in _ui_style_tokens(ui_cfg).items():
        qss = qss.replace(token, value)
    qss = qss.replace('@ASSETS@', Config.PATHS.ASSETS_DIR.resolve().as_posix())
    return theme, qss

def _resolve_app_icon_path(theme: str | None = None) -> Path | None:
    resolved = active_theme_key(theme)
    candidates = [
        Config.PATHS.ICONS_DIR / f'app_icon_{resolved}.svg',
        Config.PATHS.ICONS_DIR / 'app_icon_light.svg',
        Config.PATHS.ICONS_DIR / 'app_icon_dark.svg',
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
        pm.fill(QtCore.Qt.GlobalColor.transparent)
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
    resolved = active_theme_key(theme)
    candidates = [
        Config.PATHS.ICONS_DIR / f'{name}_{resolved}.svg',
        Config.PATHS.ICONS_DIR / f'{name}.svg',
    ]
    for path in candidates:
        if path.exists():
            return _render_svg_icon(path)
    return QtGui.QIcon()

def logo_svg_path(theme: str | None = None) -> Path | None:
    resolved = active_theme_key(theme)
    candidates = [
        Config.PATHS.IMAGES_DIR / f'logo_{resolved}.svg',
        Config.PATHS.IMAGES_DIR / 'logo_light.svg',
        Config.PATHS.IMAGES_DIR / 'logo_dark.svg',
    ]
    for path in candidates:
        if path.exists():
            return path
    return None

class LogoSvgLabel(QtWidgets.QLabel):
    """Label that renders an SVG logo scaled to the available bounds."""
    def __init__(self, path: Path, *, object_name: str | None = None, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self._renderer = QtSvg.QSvgRenderer(str(self._path))
        if object_name:
            self.setObjectName(object_name)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
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
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pm)
        self._renderer.render(painter)
        painter.end()
        self.setPixmap(pm)
        self.setFixedSize(draw_w, draw_h)
        self.updateGeometry()
