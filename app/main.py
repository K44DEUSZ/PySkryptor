# app/main.py
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ''):
  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.platform.startup_check import write_startup_report


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument('--check-startup', type=Path, metavar='REPORT')
  parser.add_argument('--check-startup-profile', type=Path, metavar='DIRECTORY')
  options, qt_arguments = parser.parse_known_args(argv)
  if options.check_startup_profile is not None and options.check_startup is None:
    parser.error('--check-startup-profile requires --check-startup')
  try:
    from app.bootstrap import run
  except Exception as error:
    if options.check_startup is None:
      raise
    write_startup_report(
      options.check_startup,
      {
        'complete': False,
        'stage': 'imports',
        'error': str(error),
        'error_type': type(error).__name__,
      },
    )
    return 1
  return run(
    [sys.argv[0], *qt_arguments], startup_report=options.check_startup,
    startup_profile=options.check_startup_profile,
  )


if __name__ == '__main__':
  raise SystemExit(main())
