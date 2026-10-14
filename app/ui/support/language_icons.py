# app/ui/support/language_icons.py
from __future__ import annotations

from PyQt6 import QtGui

from app.core.config.config import AppConfig
from app.ui.support.action_icons import action_icon

_LANGUAGE_FLAGS = {
  'af': 'za', 'am': 'et', 'ar': 'sa', 'as': 'in', 'ast': 'es', 'az': 'az',
  'ba': 'ru', 'be': 'by', 'bg': 'bg', 'bn': 'bd', 'bo': 'cn', 'br': 'fr', 'bs': 'ba',
  'ca': 'es-ct', 'ceb': 'ph', 'cs': 'cz', 'cy': 'gb-wls', 'da': 'dk', 'de': 'de',
  'el': 'gr', 'en': 'gb', 'es': 'es', 'et': 'ee', 'eu': 'es-pv',
  'fa': 'ir', 'ff': 'sn', 'fi': 'fi', 'fil': 'ph', 'fo': 'fo', 'fr': 'fr', 'fy': 'nl',
  'ga': 'ie', 'gd': 'gb-sct', 'gl': 'es-ga', 'gu': 'in',
  'ha': 'ng', 'haw': 'us', 'he': 'il', 'hi': 'in', 'hr': 'hr', 'ht': 'ht', 'hu': 'hu', 'hy': 'am',
  'id': 'id', 'ig': 'ng', 'ilo': 'ph', 'is': 'is', 'it': 'it', 'iw': 'il',
  'ja': 'jp', 'jv': 'id', 'jw': 'id', 'ka': 'ge', 'kk': 'kz', 'km': 'kh', 'kn': 'in', 'ko': 'kr',
  'la': 'va', 'lb': 'lu', 'lg': 'ug', 'ln': 'cd', 'lo': 'la', 'lt': 'lt', 'lv': 'lv',
  'mg': 'mg', 'mi': 'nz', 'mk': 'mk', 'ml': 'in', 'mn': 'mn', 'mr': 'in', 'ms': 'my', 'mt': 'mt', 'my': 'mm',
  'ne': 'np', 'nl': 'nl', 'nn': 'no', 'no': 'no', 'ns': 'za', 'nso': 'za',
  'oc': 'fr', 'or': 'in', 'pa': 'in', 'pl': 'pl', 'ps': 'af', 'pt': 'pt',
  'ro': 'ro', 'ru': 'ru', 'sa': 'in', 'sd': 'pk', 'si': 'lk', 'sk': 'sk', 'sl': 'si', 'sn': 'zw',
  'so': 'so', 'sq': 'al', 'sr': 'rs', 'ss': 'sz', 'su': 'id', 'sv': 'se', 'sw': 'tz',
  'ta': 'in', 'te': 'in', 'tg': 'tj', 'th': 'th', 'tk': 'tm', 'tl': 'ph', 'tn': 'bw', 'tr': 'tr', 'tt': 'ru',
  'uk': 'ua', 'ur': 'pk', 'uz': 'uz', 'vi': 'vn', 'wo': 'sn', 'xh': 'za',
  'yi': 'il', 'yo': 'ng', 'yue': 'hk', 'zh': 'cn', 'zh-hans': 'cn', 'zh-hant': 'tw', 'zu': 'za',
}
_CACHE: dict[str, QtGui.QIcon] = {}


def language_icon(code: str) -> QtGui.QIcon:
  """Return an auxiliary flag without changing the selected language or its regional scope."""
  normalized = str(code or '').strip().lower().replace('_', '-')
  parts = normalized.split('-')
  region = next((part for part in reversed(parts[1:]) if len(part) == 2 and part.isalpha()), None)
  for country in (region, _LANGUAGE_FLAGS.get(normalized), _LANGUAGE_FLAGS.get(parts[0])):
    if not country:
      continue
    path = AppConfig.PATHS.ICONS_DIR / 'flags' / f'{country}.svg'
    if path.is_file():
      key = str(path)
      if key not in _CACHE:
        _CACHE[key] = QtGui.QIcon(str(path))
      return _CACHE[key]
  return action_icon('translate')
