"""Build one bundle using only the selected Python environment and Windows DLLs."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path


def build_environment() -> dict[str, str]:
  environment = dict(os.environ)
  if sys.platform == 'win32':
    windows_root = Path(os.environ['SystemRoot'])
    paths = [
      Path(sys.executable).parent,
      Path(sys.prefix),
      Path(sys.prefix) / 'Scripts',
      Path(sys.base_prefix),
      windows_root / 'System32',
      windows_root,
    ]
    environment['PATH'] = os.pathsep.join(dict.fromkeys(str(path.resolve()) for path in paths))
  environment.pop('PYTHONPATH', None)
  return environment


def validate_binary_entries(entries, allowed_roots: list[Path]) -> None:
  """Reject native files resolved from unrelated tools or Python installations."""
  roots = [root.resolve() for root in allowed_roots]

  def visit(value):
    if isinstance(value, tuple) and len(value) == 3 and value[2] in {'BINARY', 'EXTENSION'}:
      source = Path(value[1]).resolve()
      if not any(source.is_relative_to(root) for root in roots):
        raise ValueError(f'Binary comes from outside the build environment: {source}')
    elif isinstance(value, (tuple, list)):
      for item in value:
        visit(item)

  visit(entries)


def validate_binary_sources(toc: Path, allowed_roots: list[Path]) -> None:
  """Validate all native entries in a completed PyInstaller analysis."""
  validate_binary_entries(ast.literal_eval(toc.read_text(encoding='utf-8')), allowed_roots)


def build_bundle(spec: Path, distpath: Path, workpath: Path) -> int:
  environment = build_environment()
  environment['PYINSTALLER_CONFIG_DIR'] = str((workpath / '.cache').resolve())
  process = subprocess.Popen(
    [
      sys.executable,
      '-m',
      'PyInstaller',
      '--clean',
      '--noconfirm',
      '--distpath',
      str(distpath),
      '--workpath',
      str(workpath),
      str(spec),
    ],
    env=environment,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding='utf-8',
    errors='replace',
    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
  )
  for line in process.stdout:
    print(line, end='', flush=True)
  code = process.wait()
  if code:
    return code
  roots = [Path(sys.prefix), Path(sys.base_prefix)]
  if sys.platform == 'win32':
    roots.append(Path(os.environ['SystemRoot']))
  validate_binary_sources(workpath / spec.stem / 'Analysis-00.toc', roots)
  return 0


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--spec', required=True, type=Path)
  parser.add_argument('--distpath', required=True, type=Path)
  parser.add_argument('--workpath', required=True, type=Path)
  args = parser.parse_args()
  raise SystemExit(build_bundle(args.spec, args.distpath, args.workpath))
