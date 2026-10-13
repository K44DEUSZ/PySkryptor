# app/platform/hardware_summary.py
from __future__ import annotations

import platform
import sys


def processor_name() -> str:
  """Read the processor identity without importing an inference library."""
  if sys.platform == 'win32':
    try:
      import winreg

      with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
        return str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).strip()
    except (OSError, ValueError):
      pass
  return platform.processor().strip()
