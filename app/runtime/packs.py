# app/runtime/packs.py
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config.config import AppConfig


@dataclass(frozen=True)
class RuntimePack:
  """One independent inference installation and its process entry point."""

  name: str
  executable: Path
  frozen: bool

  def command(self, role: str) -> list[str]:
    command = [str(self.executable)]
    if not self.frozen:
      command += ['-m', 'app.runtime.host_main']
    command += [
      '--engine',
      role,
      '--install-root',
      str(AppConfig.PATHS.INSTALL_ROOT_DIR),
      '--data-root',
      str(AppConfig.PATHS.DATA_DIR),
    ]
    if self.name == 'cpu':
      command += ['--device', 'cpu']
    return command


def available_runtime_packs() -> list[RuntimePack]:
  """Prefer an installed NVIDIA pack, retaining a separate CPU fallback."""
  engine = AppConfig.SETTINGS.engine if AppConfig.SETTINGS is not None else {}
  preferred = str(engine.get('preferred_device') or 'auto').lower()
  if getattr(sys, 'frozen', False):
    root = AppConfig.PATHS.INSTALL_ROOT_DIR / 'runtimes'
    cpu = RuntimePack('cpu', root / 'cpu/AIModelHost.exe', True)
    cuda = RuntimePack('cuda', root / 'cuda/AIModelHost.exe', True)
  else:
    cpu_python = os.environ.get('PYSKRYPTOR_CPU_PYTHON')
    cuda_python = os.environ.get('PYSKRYPTOR_CUDA_PYTHON')
    if not cpu_python and not cuda_python:
      return [RuntimePack('development', Path(sys.executable), False)]
    cpu = RuntimePack('cpu', Path(cpu_python or sys.executable), False)
    cuda = RuntimePack('cuda', Path(cuda_python or '__missing__'), False)
  return ([cuda] if preferred != 'cpu' and cuda.executable.is_file() else []) + [cpu]
