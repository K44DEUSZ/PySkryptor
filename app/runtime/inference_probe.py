# app/runtime/inference_probe.py
from __future__ import annotations

from typing import Any

import numpy as np


def probe_transcription(pipeline: Any) -> None:
  """Exercise the encoder and decoder on a short synthetic audio input."""
  pipeline(
    {'array': np.zeros(16000, dtype=np.float32), 'sampling_rate': 16000},
    return_timestamps=False,
    generate_kwargs={'language': 'en', 'task': 'transcribe', 'max_new_tokens': 2},
  )


def probe_translation(tokenizer: Any, model: Any, device: Any) -> None:
  """Exercise tokenization and generation without evaluating translation quality."""
  import torch

  tokenizer.src_lang = 'en'
  inputs = tokenizer('Hello.', return_tensors='pt').to(device)
  with torch.inference_mode():
    model.generate(**inputs, forced_bos_token_id=tokenizer.get_lang_id('pl'), max_new_tokens=2)
