# app/features/transcription/host_runtime.py
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.config.config import AppConfig
from app.features.transcription.chunking import pcm16le_bytes_to_float32
from app.features.transcription.errors import TranscriptionError
from app.features.transcription.runtime import call_backend_with_fallbacks, transcribe_wav
from app.features.transcription.whisper import (
  build_whisper_generate_kwargs,
  can_detect_language_from_audio,
  classify_audio_signal,
  detect_language_from_backend_runtime,
  extract_detected_language_from_result,
  whisper_prompt_ids_from_text,
)
from app.features.transcripts.writer import TextPostprocessor
from app.runtime.inference_probe import probe_transcription
from app.runtime.runtime_config import resolve_torch_device_dtype, use_cpu_runtime


@dataclass
class _LoadedTranscriptionRuntime:
  """Cached transcription runtime reused across host requests."""

  pipeline: Any
  model_path: str


class TranscriptionHostRuntime:
  """Dedicated ASR runtime owned by the engine host."""

  def __init__(self) -> None:
    self._loaded: _LoadedTranscriptionRuntime | None = None
    self._smoke_passed = False
    self._postprocessor = TextPostprocessor()

  @staticmethod
  def _transcription_error(key: str, **params: Any) -> TranscriptionError:
    return TranscriptionError(key, **params)

  @staticmethod
  def _load_pipeline() -> Any:
    model_path = Path(AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR)
    if not model_path.exists() or not model_path.is_dir():
      raise TranscriptionError("error.model.transcription_missing", path=str(model_path))

    device, dtype = resolve_torch_device_dtype()
    use_safetensors = True if (model_path / 'model.safetensors').exists() else None

    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    model = AutoModelForSpeechSeq2Seq.from_pretrained(
      str(model_path),
      local_files_only=True,
      dtype=dtype,
      use_safetensors=use_safetensors,
      weights_only=True,
      trust_remote_code=False,
    )
    try:
      model = model.to(device)
    except (RuntimeError, TypeError, ValueError):
      import torch

      model = model.to(device="cpu", dtype=torch.float32)
      AppConfig.DEVICE_ID = "cpu"
      AppConfig.DTYPE_ID = "float32"
      AppConfig.DEVICE_KIND = "CPU"
      AppConfig.DEVICE_FRIENDLY_NAME = "CPU"

    processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
    processor.tokenizer.clean_up_tokenization_spaces = False
    resolved_device = getattr(model, "device", device)
    device_index = (
      int(getattr(resolved_device, "index", 0) or 0) if getattr(resolved_device, "type", "cpu") == "cuda" else -1
    )

    return pipeline(
      "automatic-speech-recognition",
      model=model,
      tokenizer=processor.tokenizer,
      feature_extractor=processor.feature_extractor,
      device=device_index,
      dtype=None if device_index == -1 else dtype,
    )

  def _ensure_pipeline(self) -> Any:
    model_path = str(AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR)
    if self._loaded is not None and self._loaded.model_path == model_path:
      return self._loaded.pipeline
    pipeline = self._load_pipeline()
    self._loaded = _LoadedTranscriptionRuntime(pipeline=pipeline, model_path=model_path)
    return pipeline

  def warmup(self) -> None:
    self._smoke_passed = False
    pipeline = self._ensure_pipeline()
    fallback = False
    try:
      probe_transcription(pipeline)
    except RuntimeError:
      if getattr(getattr(pipeline, 'device', None), 'type', 'cpu') != 'cuda':
        raise
      fallback = True
    if fallback:
      self._loaded = None
      pipeline = None
      use_cpu_runtime()
      probe_transcription(self._ensure_pipeline())
    self._smoke_passed = True

  def health(self) -> dict[str, Any]:
    return {
      "role": "transcription",
      "ready": bool(self._loaded is not None and self._smoke_passed),
      "model_path": str(AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR),
    }

  def transcribe_wav(
    self,
    payload: dict[str, Any],
    *,
    progress_cb: Callable[[int], None] | None = None,
  ) -> dict[str, Any]:
    pipeline = self._ensure_pipeline()
    merged_text, segments, detected_language = transcribe_wav(
      backend=pipeline,
      wav_path=Path(str(payload.get("wav_path") or "")),
      key=str(payload.get("key") or ""),
      chunk_len_s=int(payload.get("chunk_length_s") or 0),
      stride_len_s=int(payload.get("stride_length_s") or 0),
      want_timestamps=bool(payload.get("want_timestamps")),
      ignore_warning=bool(payload.get("ignore_warning")),
      progress_cb=progress_cb,
      cancel_check=lambda: False,
      require_language=bool(payload.get("require_language")),
      source_language=str(payload.get("source_language") or ""),
      runtime_profile=payload.get("runtime_profile") if isinstance(payload.get("runtime_profile"), dict) else {},
      postprocessor=self._postprocessor,
      error_factory=self._transcription_error,
    )
    return {
      "merged_text": merged_text,
      "segments": segments,
      "detected_language": detected_language,
    }

  def recognize_audio(self, payload: dict[str, Any]) -> dict[str, Any]:
    pipeline = self._ensure_pipeline()
    audio_b64 = str(payload.get("audio_b64") or "")
    audio_bytes = base64.b64decode(audio_b64.encode("ascii")) if audio_b64 else b""
    audio = pcm16le_bytes_to_float32(audio_bytes)
    if audio.size == 0:
      return {"text": "", "detected_language": ""}

    sample_rate = int(payload.get("sample_rate") or AppConfig.ASR_SAMPLE_RATE)
    source_language = str(payload.get("source_language") or "")
    previous_text = str(payload.get("previous_text") or "")
    runtime_profile = payload.get("runtime_profile") if isinstance(payload.get("runtime_profile"), dict) else {}
    signal_kind = str(
      payload.get("signal_kind") or classify_audio_signal(audio, sr=sample_rate, profile=runtime_profile)
    )
    ignore_warning = bool(payload.get("ignore_warning"))
    require_language = bool(payload.get("require_language"))

    prompt_ids = whisper_prompt_ids_from_text(backend=pipeline, text=previous_text) if previous_text else None
    generate_kwargs = build_whisper_generate_kwargs(
      profile=runtime_profile,
      source_language=source_language,
      prompt_ids=prompt_ids,
      signal_kind=signal_kind,
    )

    result = call_backend_with_fallbacks(
      backend=pipeline,
      payload={"raw": audio, "sampling_rate": sample_rate},
      generate_kwargs=generate_kwargs,
      normalized_lang=source_language,
      ignore_warning=ignore_warning,
      want_timestamps=False,
      require_language=require_language,
      error_factory=self._transcription_error,
    )
    if not isinstance(result, dict):
      result = {"text": str(result)}

    detected_language = extract_detected_language_from_result(result)
    if (
      not detected_language
      and require_language
      and can_detect_language_from_audio(
        audio,
        sr=sample_rate,
        signal_kind=signal_kind,
        profile=runtime_profile,
      )
    ):
      detected_language = detect_language_from_backend_runtime(
        backend=pipeline,
        audio=audio,
        sr=sample_rate,
      )

    return {
      "text": str(result.get("text") or ""),
      "detected_language": str(detected_language or ""),
    }
