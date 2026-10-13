# app/runtime/model_families.py
from __future__ import annotations

MODEL_ROLES = {'whisper': 'transcription', 'm2m_100': 'translation'}


def model_role(family: str) -> str:
  """Return the supported inference role for a Transformers model family."""
  return MODEL_ROLES.get(str(family).strip().lower(), '')
