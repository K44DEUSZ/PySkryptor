# app/ui/support/theme_runtime.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtSvg, QtWidgets

from app.core.config.config import AppConfig
from app.ui.ui_config import _DEFAULT_UI, UIConfig, _coerce_cfg, ui


def _windows_dark_mode() -> bool:
  if sys.platform != "win32":
    return False
  try:
    settings = QtCore.QSettings(
      "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
      QtCore.QSettings.Format.NativeFormat,
    )
    value = settings.value("AppsUseLightTheme", 1)
    return str(value).strip() in {"0", "false", "False"}
  except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
    return False


def system_theme_key(app: QtWidgets.QApplication | None = None) -> str:
  """Return the current system theme preference."""
  app = app or QtWidgets.QApplication.instance()
  if _windows_dark_mode():
    return "dark"
  try:
    pal = app.palette() if app is not None else QtWidgets.QApplication.palette()
    return "dark" if pal.color(QtGui.QPalette.ColorRole.Window).lightness() < 128 else "light"
  except (AttributeError, RuntimeError, TypeError, ValueError):
    return "light"


def active_theme_key(theme: str | None = None, *, app: QtWidgets.QApplication | None = None) -> str:
  """Resolve the effective theme key for the current application state."""
  resolved = str(theme or "").strip().lower()
  if resolved in {"light", "dark"}:
    return resolved

  app = app or QtWidgets.QApplication.instance()
  app_theme = str(app.property("theme") if app is not None else "").strip().lower()
  if app_theme in {"light", "dark"}:
    return app_theme

  return system_theme_key(app)


def apply_windows_dark_titlebar(w: QtWidgets.QWidget, theme: str | None = None) -> None:
  """Apply the native title bar theme and window shape on supported Windows builds."""
  if sys.platform != "win32":
    return
  resolved = active_theme_key(theme)
  try:
    import ctypes

    hwnd = int(w.winId())
    dwm = ctypes.windll.dwmapi
    set_window_attribute = getattr(dwm, "DwmSetWindowAttribute", None)
    if set_window_attribute is None:
      return
    set_window_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    set_window_attribute.restype = ctypes.c_long
    value = ctypes.c_int(1 if resolved == 'dark' else 0)
    for attr in (20, 19):
      try:
        if set_window_attribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
          break
      except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        continue
    corner_preference = ctypes.c_int(1 if ui(w).radius_l == 0 else 2)
    set_window_attribute(hwnd, 33, ctypes.byref(corner_preference), ctypes.sizeof(corner_preference))
  except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
    return


def _parse_hex_color(value: str) -> QtGui.QColor:
  return QtGui.QColor(str(value or "").strip())


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
  """Return palette colors used to seed the application palette."""
  resolved = active_theme_key(theme)
  return {
    'window': theme_color(resolved, '@APP_BG@'),
    "highlight": theme_color(resolved, "@CONTEXT_SELECTED_BG@"),
    "link": theme_color(resolved, "@BORDER_ACTIVE@"),
    "link_visited": theme_color(resolved, "@BORDER_PRESSED@"),
    "highlighted_text": theme_color(resolved, "@TEXT_ON_SELECTION@"),
  }


def floating_shadow_color(theme: str | None = None, *, app: QtWidgets.QApplication | None = None) -> QtGui.QColor:
  """Return the shadow color used by floating surfaces."""
  resolved = active_theme_key(theme, app=app)
  return theme_color(resolved, "@FLOATING_SHADOW@")


def spectrum_palette(
  state: str,
  *,
  theme: str | None = None,
  app: QtWidgets.QApplication | None = None,
) -> SpectrumPalette:
  """Return the live spectrum palette for the given runtime state."""
  resolved_theme = active_theme_key(theme, app=app)
  state_key = str(state or "").strip().lower() or "idle"

  background = _color_with_alpha(theme_color(resolved_theme, "@CONTROL_BG@"))
  border = _color_with_alpha(theme_color(resolved_theme, "@BORDER_DEFAULT@"))
  track = _color_with_alpha(theme_color(resolved_theme, "@CONTROL_BG_HOVER@"))
  bar = _color_with_alpha(theme_color(resolved_theme, "@ACTIVE_BG_HOVER@"))

  if state_key == "active":
    bar = _color_with_alpha(theme_color(resolved_theme, "@ACTIVE_BG_STRONG@"))
  elif state_key == "paused":
    border = _color_with_alpha(theme_color(resolved_theme, "@BORDER_SUBTLE@"))
    track = _color_with_alpha(theme_color(resolved_theme, "@CONTROL_BG_DISABLED@"))
    bar = _color_with_alpha(theme_color(resolved_theme, "@TEXT_DISABLED@"), 180)
  elif state_key == "disabled":
    background = _color_with_alpha(theme_color(resolved_theme, "@CONTROL_BG_DISABLED@"))
    border = _color_with_alpha(theme_color(resolved_theme, "@BORDER_SUBTLE@"))
    track = _color_with_alpha(theme_color(resolved_theme, "@CONTROL_BG_DISABLED@"))
    bar = _color_with_alpha(theme_color(resolved_theme, "@TEXT_DISABLED@"), 132)
  elif state_key == "error":
    border = _color_with_alpha(theme_color(resolved_theme, "@TEXT_ERROR@"))
    bar = _color_with_alpha(theme_color(resolved_theme, "@TEXT_ERROR@"))

  return SpectrumPalette(background=background, border=border, track=track, bar=bar)


_DESKTOP_PALETTES = {
  'light': {
    'background': '#FAFAF8', 'surface': '#FFFFFF', 'surface_low': '#F2F2F0', 'surface_high': '#EAEAE8',
    'primary': '#41775A', 'primary_container': '#E8E8E6', 'on_primary_container': '#252525',
    'text': '#252525', 'text_muted': '#6B6B6B', 'disabled': '#999999',
    'outline': '#CFCFCD', 'outline_subtle': '#E1E1DF', 'error': '#B23B44', 'secondary': '#7155BA',
    'shadow': '#14000000', 'surface_edge': '#FFFFFF', 'canvas_end': '#F7F7F5', 'logo_base': '#6F8E76',
    'primary_action': '#41775A', 'on_primary_action': '#FFFFFF', 'success': '#41784A',
  },
  'dark': {
    'background': '#131415', 'surface': '#212325', 'surface_low': '#101113', 'surface_high': '#2D3033',
    'primary': '#43C47A', 'primary_container': '#303538', 'on_primary_container': '#F1F1F1',
    'text': '#ECECEC', 'text_muted': '#B0B5B7', 'disabled': '#747A7D',
    'outline': '#4B5255', 'outline_subtle': '#343B3E', 'error': '#F4A3AE', 'secondary': '#B6A0F9',
    'shadow': '#28000000', 'surface_edge': '#42494C', 'canvas_end': '#191B1D', 'logo_base': '#38A964',
    'primary_action': '#43C47A', 'on_primary_action': '#132219', 'success': '#8FCBA4',
  },
}


_SURFACE_LAYERS = {
  'light': {
    'PANEL_TOP': '#FFFFFF', 'PANEL_BOTTOM': '#FFFFFF', 'SIDEBAR': '#F2F2F0', 'FIELD': '#F7F7F5',
    'HOVER': '#EAEAE8', 'NAV': '#E8E8E6', 'BORDER': '#E1E1DF', 'EDGE': '#E1E1DF',
    'ROW': 'transparent', 'HEADER': '#F7F7F5', 'POPUP': '#FFFFFF', 'CAPTION': '#F2F2F0',
  },
  'dark': {
    'PANEL_TOP': '#212325', 'PANEL_BOTTOM': '#1C1E20', 'SIDEBAR': '#101113', 'FIELD': '#1B1E20',
    'HOVER': '#2D3235', 'NAV': '#303538', 'BORDER': '#343B3E', 'EDGE': '#42494C',
    'ROW': 'transparent', 'HEADER': '#25292C', 'POPUP': '#25292C', 'CAPTION': '#101113',
  },
}


def _theme_tokens() -> dict[str, dict[str, str]]:
  """Derive shared semantic colors and quiet desktop surfaces."""
  themes = {}
  for theme, colors in _DESKTOP_PALETTES.items():
    tokens = {
      '@APP_BG@': colors['background'], '@PANEL_BG@': colors['surface'],
      '@POPUP_BG@': colors['surface_high'], '@HEADER_BG@': colors['surface_low'],
      '@CONTROL_BG@': colors['surface_high'], '@CONTROL_BG_HOVER@': colors['primary_container'],
      '@CONTROL_BG_DISABLED@': colors['surface_low'], '@FIELD_BG@': colors['surface_high'],
      '@FIELD_BG_HOVER@': colors['primary_container'], '@FIELD_BG_ACTIVE@': colors['primary_container'],
      '@CONTEXT_HOVER_BG@': colors['surface_high'], '@CONTEXT_SELECTED_BG@': colors['primary_container'],
      '@ACTIVE_BG@': colors['primary_container'], '@ACTIVE_BG_HOVER@': colors['primary_container'],
      '@ACTIVE_BG_STRONG@': colors['primary'],
      '@PRIMARY_ACTION_BG@': colors['primary_action'], '@PRIMARY_ACTION_TEXT@': colors['on_primary_action'],
      '@BRAND_ACCENT@': '#CEFF49', '@BORDER_DEFAULT@': colors['outline'],
      '@BORDER_SUBTLE@': colors['outline_subtle'], '@BORDER_MUTED@': colors['outline'],
      '@BORDER_GRID@': colors['outline_subtle'], '@BORDER_ACTIVE@': colors['primary'],
      '@BORDER_PRESSED@': colors['primary'], '@TEXT_PRIMARY@': colors['text'],
      '@TEXT_SECONDARY@': colors['text_muted'], '@TEXT_DISABLED@': colors['disabled'],
      '@TEXT_SUCCESS@': colors['success'], '@TEXT_ERROR@': colors['error'],
      '@TEXT_ON_ACTIVE@': colors['on_primary_action'], '@TEXT_ON_SELECTION@': colors['on_primary_container'],
      '@PROGRESS_FILL@': colors['primary'], '@SCROLLBAR_TRACK@': colors['surface_low'],
      '@SCROLLBAR_HANDLE@': colors['outline'], '@SCROLLBAR_HANDLE_HOVER@': colors['text_muted'],
      '@FLOATING_SHADOW@': colors['shadow'], '@SIDEBAR_BG@': colors['surface_low'],
      '@NAV_ACTIVE_BG@': colors['primary_container'], '@NAV_ACTIVE_TEXT@': colors['on_primary_container'],
      '@TRANSLATION_ACCENT@': colors['secondary'], '@SURFACE_EDGE@': colors['surface_edge'],
      '@CANVAS_END@': colors['canvas_end'], '@LOGO_BASE@': colors['logo_base'],
    }
    tokens.update({f'@LAYER_{name}@': value for name, value in _SURFACE_LAYERS[theme].items()})
    action_color = QtGui.QColor(colors['primary_action'])
    tokens['@PRIMARY_ACTION_HOVER@'] = (action_color.lighter(110) if theme == 'dark'
                                      else action_color.darker(108)).name()
    icons = {
      'INFO': 'info', 'ARROW_DOWN': 'arrow_down', 'ARROW_UP': 'arrow_up',
      'CHECKBOX_UNCHECKED': 'checkbox_unchecked', 'CHECKBOX_UNCHECKED_HOVER': 'checkbox_unchecked',
      'CHECKBOX_CHECKED': 'checkbox_checked', 'CHECKBOX_UNCHECKED_DISABLED': 'checkbox_unchecked',
      'CHECKBOX_CHECKED_DISABLED': 'checkbox_checked', 'RADIO_UNCHECKED': 'radio_unchecked',
      'RADIO_UNCHECKED_HOVER': 'radio_unchecked', 'RADIO_CHECKED': 'radio_checked',
      'RADIO_UNCHECKED_DISABLED': 'radio_unchecked', 'RADIO_CHECKED_DISABLED': 'radio_checked',
    }
    for token, name in icons.items():
      suffix = '_hover' if token.endswith('_HOVER') else '_disabled' if token.endswith('_DISABLED') else ''
      tokens[f'@ICON_{token}@'] = f'@ASSETS@/icons/{name}_{theme}{suffix}.svg'
    themes[theme] = tokens
  return themes


_THEME_STYLE_TOKENS = _theme_tokens()


def theme_style_tokens(theme: str) -> dict[str, str]:
  """Resolve stylesheet tokens for the selected theme."""
  key = "dark" if str(theme or "").strip().lower() == "dark" else "light"
  assets = AppConfig.PATHS.ASSETS_DIR.resolve().as_posix()
  out: dict[str, str] = {}
  for token, value in _THEME_STYLE_TOKENS[key].items():
    out[token] = str(value).replace("@ASSETS@", assets)
  return out


def _ui_style_tokens(cfg: UIConfig) -> dict[str, str]:
  radius_l = max(int(cfg.radius_l), 0)
  radius_m = max(int(cfg.radius_m), 0)
  radius_s = max(int(cfg.radius_s), 0)
  pad_x_m = max(int(cfg.pad_x_m), 0)
  pad_x_l = max(int(cfg.pad_x_l), 0)
  pad_y_s = max(int(cfg.pad_y_s), 0)
  pad_y_m = max(int(cfg.pad_y_m), 0)
  pad_y_l = max(int(cfg.pad_y_l), 0)
  tab_min_h = max(int(cfg.control_min_h) - 2, 24)
  scroll_handle_min = max(int(cfg.option_row_min_h), 20)
  return {
    "@RADIUS_L@": f"{radius_l}px",
    "@RADIUS_M@": f"{radius_m}px",
    "@RADIUS_S@": f"{radius_s}px",
    "@RADIUS_PILL@": f"{max(cfg.radius_pill, 0)}px",
    "@SPACE_GROUPBOX_TOP@": f"{max(int(cfg.space_l) * 2 + 2, 0)}px",
    "@GROUPBOX_PADDING@": f"{pad_y_l * 2}px {pad_x_m}px {pad_x_m}px {pad_x_m}px",
    "@PAD_BUTTON@": f"{pad_y_s}px {pad_x_l}px",
    "@PAD_FIELD@": f"{pad_y_s}px {pad_x_m}px",
    "@PAD_FIELD_INLINE@": f"0px {pad_x_m}px",
    "@PAD_FIELD_TOOL@": f"0px {max(pad_x_m + 24, 0)}px 0px {pad_x_m}px",
    "@PAD_FIELD_CHROME@": f"0px {max(pad_x_m + 26, 0)}px 0px {pad_x_m}px",
    "@PAD_BLOCK@": f"{pad_x_m}px",
    "@PAD_POPUP@": f"{pad_y_m}px {pad_x_l}px",
    "@PAD_SECTION@": f"{pad_y_l}px {pad_x_m}px",
    "@PAD_TOOLTIP@": f"{pad_y_m}px {max(int(cfg.margin), 0)}px",
    "@PAD_MENU@": f"{pad_y_m}px",
    "@PAD_CHECK_ROW@": f"{pad_y_m}px 0px",
    "@SPACE_TAB_TOP@": f"{max(int(cfg.margin), 0)}px",
    "@TAB_MIN_HEIGHT@": f"{tab_min_h}px",
    "@PAD_TAB@": f"{pad_x_m}px {max(pad_x_l + 2, 0)}px",
    "@SCROLL_HANDLE_MIN@": f"{scroll_handle_min}px",
    "@TABLE_CHECK_INDICATOR_SIZE@": f"{max(int(cfg.table_check_indicator_size), 14)}px",
  }


def theme_color(theme: str, token: str) -> QtGui.QColor:
  """Resolve a themed color token to a QColor."""
  value = theme_style_tokens(theme).get(str(token or "").strip(), "#000000")
  return _parse_hex_color(value)


def render_theme_stylesheet(
  styles_dir: Path,
  theme_pref: str,
  *,
  app: QtWidgets.QApplication | None = None,
) -> tuple[str, str]:
  """Render the application stylesheet for the effective theme."""
  pref = str(theme_pref or "auto").strip().lower()
  theme = pref if pref in {"light", "dark"} else system_theme_key(app)
  cfg = _coerce_cfg(app.property("ui_config") if app is not None else None)
  ui_cfg = cfg if cfg is not None else _DEFAULT_UI
  qss_path = Path(styles_dir) / "style.qss"
  if not qss_path.exists():
    return theme, ""
  try:
    qss = qss_path.read_text(encoding="utf-8")
  except OSError:
    return theme, ""
  for token, value in theme_style_tokens(theme).items():
    qss = qss.replace(token, value)
  for token, value in _ui_style_tokens(ui_cfg).items():
    qss = qss.replace(token, value)
  qss = qss.replace("@ASSETS@", AppConfig.PATHS.ASSETS_DIR.resolve().as_posix())
  return theme, qss


def _resolve_app_icon_path(theme: str | None = None) -> Path | None:
  resolved = active_theme_key(theme)
  candidates = [
    AppConfig.PATHS.ICONS_DIR / f"app_icon_{resolved}.svg",
    AppConfig.PATHS.ICONS_DIR / "app_icon_light.svg",
    AppConfig.PATHS.ICONS_DIR / "app_icon_dark.svg",
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
  """Return the themed application icon."""
  path = _resolve_app_icon_path(theme)
  return _render_svg_icon(path) if path is not None else QtGui.QIcon()


def status_icon(name: str, *, theme: str | None = None) -> QtGui.QIcon:
  """Return a themed status icon by logical name."""
  resolved = active_theme_key(theme)
  candidates = [
    AppConfig.PATHS.ICONS_DIR / f"{name}_{resolved}.svg",
    AppConfig.PATHS.ICONS_DIR / f"{name}.svg",
  ]
  for path in candidates:
    if path.exists():
      return _render_svg_icon(path)
  return QtGui.QIcon()


def logo_svg_path(theme: str | None = None) -> Path | None:
  """Return the preferred themed logo path when available."""
  resolved = active_theme_key(theme)
  candidates = [
    AppConfig.PATHS.IMAGES_DIR / f"logo_{resolved}.svg",
    AppConfig.PATHS.IMAGES_DIR / "logo_light.svg",
    AppConfig.PATHS.IMAGES_DIR / "logo_dark.svg",
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
    self._bounds = (360, 140)
    if object_name:
      self.setObjectName(object_name)
    self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    self.setScaledContents(False)
    self.update_for_bounds(360, 140)

  def update_for_bounds(self, max_w: int, max_h: int) -> None:
    """Resize the rendered logo to fit the requested bounds."""
    width = max(1, int(max_w))
    height = max(1, int(max_h))
    self._bounds = (width, height)
    if not self._renderer.isValid():
      self.setText("")
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
    self._renderer.render(painter, QtCore.QRectF(0, 0, draw_w, draw_h))
    painter.end()
    self.setPixmap(pm)
    self.setFixedSize(draw_w, draw_h)
    self.updateGeometry()

  def changeEvent(self, event: QtCore.QEvent) -> None:
    super().changeEvent(event)
    if event.type() not in {QtCore.QEvent.Type.StyleChange, QtCore.QEvent.Type.PaletteChange}:
      return
    if not hasattr(self, '_renderer'):
      return
    path = logo_svg_path()
    if path is not None and path != self._path:
      self._path = path
      self._renderer.load(str(path))
    self.update_for_bounds(*self._bounds)

  def event(self, event: QtCore.QEvent) -> bool:
    result = super().event(event)
    if event.type() == QtCore.QEvent.Type.DevicePixelRatioChange and hasattr(self, '_renderer'):
      self.update_for_bounds(*self._bounds)
    return result


def apply_application_theme(preference: str, *, animate: bool = True) -> str:
  """Apply UI appearance immediately without restarting models or capture."""
  app = QtWidgets.QApplication.instance()
  if not isinstance(app, QtWidgets.QApplication):
    return active_theme_key(preference)
  theme, stylesheet = render_theme_stylesheet(AppConfig.PATHS.STYLES_DIR, preference, app=app)
  app.setProperty('themePreference', str(preference or 'auto'))
  if theme == str(app.property('theme') or ''):
    return theme
  windows = [widget for widget in app.topLevelWidgets() if isinstance(widget, QtWidgets.QMainWindow)]
  covers = []
  if animate:
    for window in windows:
      if window.isVisible():
        cover = QtWidgets.QLabel(window)
        cover.setPixmap(window.grab())
        cover.setGeometry(window.rect())
        cover.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        covers.append(cover)
  app.setProperty('theme', theme)
  app.setStyleSheet(stylesheet)
  palette = app.palette()
  colors = app_palette_colors(theme)
  for role, key in (
    (QtGui.QPalette.ColorRole.Window, 'window'),
    (QtGui.QPalette.ColorRole.Highlight, 'highlight'),
    (QtGui.QPalette.ColorRole.Link, 'link'),
    (QtGui.QPalette.ColorRole.LinkVisited, 'link_visited'),
    (QtGui.QPalette.ColorRole.HighlightedText, 'highlighted_text'),
  ):
    palette.setColor(role, colors[key])
  app.setPalette(palette)
  app.setWindowIcon(app_icon(theme))
  for window in windows:
    window.setWindowIcon(app_icon(theme))
    apply_windows_dark_titlebar(window, theme)
  for cover in covers:
    effect = QtWidgets.QGraphicsOpacityEffect(cover)
    cover.setGraphicsEffect(effect)
    animation = QtCore.QPropertyAnimation(effect, b'opacity', cover)
    animation.setDuration(ui(cover).theme_animation_ms)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.finished.connect(cover.deleteLater)
    cover.show()
    cover.raise_()
    animation.start()
  return theme
