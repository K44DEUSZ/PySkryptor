# app/ui/support/language_icons.py
from __future__ import annotations

from PyQt6 import QtGui

from app.core.config.config import AppConfig
from app.ui.support.action_icons import action_icon

_LANGUAGE_FLAGS = {
  'pl': 'pl', 'en': 'gb', 'de': 'de', 'fr': 'fr', 'es': 'es', 'pt': 'pt', 'it': 'it',
  'cs': 'cz', 'sk': 'sk', 'uk': 'ua', 'hu': 'hu', 'el': 'gr', 'tr': 'tr', 'ja': 'jp',
  'ko': 'kr', 'vi': 'vn', 'th': 'th', 'fi': 'fi', 'sv': 'se', 'da': 'dk', 'no': 'no',
  'nl': 'nl', 'bg': 'bg', 'ro': 'ro', 'lt': 'lt', 'lv': 'lv', 'et': 'ee', 'is': 'is',
  'ka': 'ge', 'hy': 'am', 'sq': 'al', 'mk': 'mk', 'sl': 'si', 'hr': 'hr', 'zh': 'cn',
}
_CACHE: dict[str, QtGui.QIcon] = {}


def language_icon(code: str) -> QtGui.QIcon:
  """Return an auxiliary flag without changing the selected language or its regional scope."""
  normalized = str(code or '').lower().replace('_', '-')
  parts = normalized.split('-')
  country = parts[-1] if len(parts) > 1 and len(parts[-1]) == 2 else _LANGUAGE_FLAGS.get(normalized)
  if country:
    path = AppConfig.PATHS.ICONS_DIR / 'flags' / f'{country}.svg'
    if path.is_file():
      key = str(path)
      if key not in _CACHE:
        _CACHE[key] = QtGui.QIcon(str(path))
      return _CACHE[key]
  return action_icon('translate')
