"""Record hashes for the assembled binaries, resources and license files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def write_manifest(root: Path) -> None:
  root = root.resolve()
  manifest = root / 'release_manifest.json'
  files = []
  for path in sorted(root.rglob('*')):
    if not path.is_file() or path == manifest:
      continue
    if path.is_symlink():
      raise ValueError(f'Build contains a symbolic link: {path}')
    with path.open('rb') as stream:
      digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    files.append({'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size, 'sha256': digest})
  manifest.write_text(json.dumps({'schema_version': 1, 'files': files}, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--root', required=True, type=Path)
  args = parser.parse_args()
  write_manifest(args.root)
