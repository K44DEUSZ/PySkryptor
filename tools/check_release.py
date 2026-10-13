"""Check the actual GUI executable, including startup and orderly shutdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def check_release(root: Path, *, require_models: bool, profile_dir: Path | None = None) -> dict:
  root = root.resolve()
  with (root / 'PySkryptor.exe').open('rb') as stream:
    executable_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
  with tempfile.TemporaryDirectory(prefix='pyskryptor-startup-check-') as temporary:
    report = Path(temporary) / 'startup.json'
    command = [str(root / 'PySkryptor.exe'), '--check-startup', str(report)]
    if profile_dir is not None:
      command.extend(['--check-startup-profile', str(profile_dir.resolve())])
    process = subprocess.run(
      command,
      cwd=root,
      capture_output=True,
      text=True,
      errors='replace',
      timeout=150,
      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    result = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else {}
    if process.returncode or not result.get('complete') or not result.get('shutdown_completed'):
      raise RuntimeError(f'GUI startup check failed: exit={process.returncode}, report={result}')
    if not result.get('main_window_visible') or result.get('qt_platform') != 'windows':
      raise RuntimeError(f'Native Windows main window was not created: {result}')
    if require_models and not all(result.get(role, {}).get('ready') for role in ('transcription', 'translation')):
      raise RuntimeError(f'Models were not ready in the GUI startup check: {result}')
    result['executable_sha256'] = executable_sha256
    return result


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--root', required=True, type=Path)
  parser.add_argument('--require-models', action='store_true')
  parser.add_argument('--profile-dir', type=Path)
  args = parser.parse_args()
  print(json.dumps(
    check_release(args.root, require_models=args.require_models, profile_dir=args.profile_dir),
    ensure_ascii=False, indent=2,
  ))
