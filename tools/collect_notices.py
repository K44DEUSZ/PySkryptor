"""Collect license files for distributions present in one PyInstaller analysis."""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path


def source_paths(toc: Path) -> set[Path]:
  paths = set()

  def visit(value):
    if isinstance(value, str) and Path(value).is_file():
      paths.add(Path(value).resolve())
    elif isinstance(value, (tuple, list)):
      for item in value:
        visit(item)

  visit(ast.literal_eval(toc.read_text(encoding='utf-8')))
  return paths


def collect(toc: Path, output: Path) -> None:
  included = source_paths(toc)
  records = []
  for distribution in importlib.metadata.distributions():
    files = list(distribution.files or [])
    if not any(Path(distribution.locate_file(file)).resolve() in included for file in files):
      continue
    name = distribution.metadata['Name']
    destination = output / (name + '-' + distribution.version)
    license_files = []
    for file in files:
      relative = Path(file)
      if not any(part.lower().startswith(('license', 'notice', 'copying', 'copyright')) for part in relative.parts):
        continue
      source = Path(distribution.locate_file(file))
      if source.is_file():
        safe_parts = [part for part in relative.parts if part not in ('..', '.', '/')]
        target = destination.joinpath(*safe_parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        license_files.append(str(target.relative_to(output)))
    if not license_files:
      upstream_files = (
        Path(__file__).resolve().parents[1] / 'third_party/licenses' / (name + '-' + distribution.version)
      )
      if upstream_files.is_dir():
        for source in upstream_files.iterdir():
          if source.is_file():
            target = destination / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            license_files.append(str(target.relative_to(output)))
    records.append(
      {
        'name': name,
        'version': distribution.version,
        'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),
        'project_urls': distribution.metadata.get_all('Project-URL') or [],
        'license_files': license_files,
      }
    )
  python_license = Path(sys.base_prefix) / 'LICENSE.txt'
  if python_license.is_file():
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, output / 'PYTHON-LICENSE.txt')
  output.mkdir(parents=True, exist_ok=True)
  (output / 'inventory.json').write_text(
    json.dumps(
      {'python': sys.version, 'packages': sorted(records, key=lambda record: record['name'].lower())},
      indent=2,
      ensure_ascii=False,
    )
    + '\n',
    encoding='utf-8',
  )


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--toc', required=True, type=Path)
  parser.add_argument('--output', required=True, type=Path)
  args = parser.parse_args()
  collect(args.toc, args.output)
