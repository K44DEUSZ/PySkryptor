# app/platform/startup_check.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_startup_report(path: Path, result: dict[str, Any]) -> None:
  """Save the outcome of an explicitly requested startup diagnostic."""
  path = Path(path).resolve()
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
