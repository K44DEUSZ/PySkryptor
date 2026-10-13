# app/ui/support/action_icons.py
from __future__ import annotations

from PyQt6 import QtGui, QtWidgets

from app.core.config.config import AppConfig
from app.ui.support.theme_runtime import _render_svg_icon, active_theme_key

_ACTION_ICONS = {
  "common.browse": "folder",
  "controls.add": "add",
  "controls.open_source": "link",
  "controls.refresh_status": "refresh",
  "download.open_folder": "folder",
  "files.add_files": "file",
  "files.add_folder": "folder",
  "files.clear": "clear",
  "files.open_output": "folder",
  "files.remove_selected": "delete",
  "live.controls.clear": "clear",
  "live.controls.pause": "pause",
  "live.controls.refresh": "refresh",
  "live.controls.save_transcript": "save",
  "live.controls.start": "play",
  "live.controls.stop": "stop",
  "settings.app.logging.open_folder": "folder",
  "settings.buttons.restore_defaults": "refresh",
  "settings.buttons.save": "save",
  "settings.buttons.undo": "back",
  "tabs.about": "info",
  "tabs.downloader": "download",
  "tabs.files": "file",
  "tabs.live": "mic",
  "tabs.models": "models",
  "tabs.settings": "settings",
}

_ICON_CACHE: dict[tuple[str, str], QtGui.QIcon] = {}
_ICON_KEYS: dict[int, str] = {}


def action_icon(key: str, *, theme: str | None = None) -> QtGui.QIcon:
  """Return an original SVG action icon for the current UI theme."""
  name = _ACTION_ICONS.get(key, key)
  variant = active_theme_key(theme)
  cache_key = (name, variant)
  if cache_key not in _ICON_CACHE:
    icon = _render_svg_icon(AppConfig.PATHS.ICONS_DIR / 'actions' / f'{name}_{variant}.svg')
    _ICON_CACHE[cache_key] = icon
    if not icon.isNull():
      _ICON_KEYS[icon.cacheKey()] = name
  return _ICON_CACHE[cache_key]


def _primary_action_icon(name: str) -> QtGui.QIcon:
  theme = active_theme_key()
  cache_key = (f'{name}:primary', theme)
  if cache_key not in _ICON_CACHE:
    contrast = 'light' if theme == 'dark' else 'dark'
    icon = QtGui.QIcon(action_icon(name, theme=contrast))
    disabled = action_icon(name, theme=theme)
    for size in (16, 18, 20, 24, 32, 48):
      icon.addPixmap(disabled.pixmap(size, size), QtGui.QIcon.Mode.Disabled)
    _ICON_CACHE[cache_key] = icon
    _ICON_KEYS[icon.cacheKey()] = name
  return _ICON_CACHE[cache_key]


def refresh_action_icons(root: QtWidgets.QWidget) -> None:
  """Refresh existing SVG buttons for their theme and action contrast."""
  for button in root.findChildren(QtWidgets.QAbstractButton):
    name = _ICON_KEYS.get(button.icon().cacheKey())
    if name:
      if button.property('actionRole') == 'primary':
        button.setIcon(_primary_action_icon(name))
      else:
        button.setIcon(action_icon(name))
  containers = root.findChildren(QtWidgets.QTabWidget)
  if isinstance(root, QtWidgets.QTabWidget):
    containers.append(root)
  for tabs in containers:
    for index in range(tabs.count()):
      name = _ICON_KEYS.get(tabs.tabIcon(index).cacheKey())
      if name:
        tabs.setTabIcon(index, action_icon(name))
