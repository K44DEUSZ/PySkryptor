# app/model/engines/service.py
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from app.model.core.config.config import AppConfig
from app.model.core.config.config import ConfigError
from app.model.core.domain.errors import AppError
from app.model.engines.registry import ModelRegistry
from app.model.engines.resolution import EngineResolver

_LOG = logging.getLogger(__name__)


class ModelNotInstalledError(AppError):
    """Raised when a required local model directory is missing."""

    def __init__(self, key: str, path: Path) -> None:
        super().__init__(str(key), {"path": str(path)})


def _enrich_model_cfg(
    model_cfg: dict[str, Any],
    *,
    task: str,
    resolved_name: str = "",
) -> dict[str, Any]:
    cfg = dict(model_cfg) if isinstance(model_cfg, dict) else {}
    engine_name = str(
        resolved_name or EngineResolver.active_engine_name(task=task) or cfg.get("engine_name", "") or ""
    ).strip()
    if not engine_name or engine_name == AppConfig.MISSING_VALUE:
        return cfg

    cfg["engine_name"] = engine_name
    desc = EngineResolver.local_model_descriptor(engine_name)
    if not desc:
        return cfg

    cfg["engine_model_type"] = str(desc.get("model_type", "") or "")
    cfg["engine_signature"] = str(desc.get("signature", "") or "")
    return cfg


def _raw_model_cfg_for_task(*, task: str) -> dict[str, Any]:
    task_id = str(task or "").strip().lower()
    if task_id == "translation":
        return AppConfig.translation_model_raw_cfg_dict()
    return AppConfig.transcription_model_raw_cfg_dict()


def _normalize_model_task(task: str) -> str:
    task_id = str(task or "").strip().lower()
    if task_id in ("transcription", "translation"):
        return task_id
    raise ValueError(f"Unsupported model task: {task}")


def _current_model_cfg(*, task: str) -> dict[str, Any]:
    task_id = _normalize_model_task(task)
    return _enrich_model_cfg(_raw_model_cfg_for_task(task=task_id), task=task_id)


def _is_disabled_engine_name(name: str) -> bool:
    return ModelRegistry.is_disabled_engine_name(name)


def _require_dir(path: Path, *, error_key: str) -> None:
    if path.exists() and path.is_dir() and path.name != AppConfig.MISSING_VALUE:
        return
    raise ModelNotInstalledError(error_key, path)


def _resolve_torch_device(device_id: str) -> Any:
    import torch

    wanted = str(device_id or "cpu").strip().lower()
    if wanted in ("cuda", "gpu"):
        wanted = "cuda:0"
    if wanted.startswith("cuda") and torch.cuda.is_available():
        try:
            return torch.device(wanted)
        except (RuntimeError, TypeError, ValueError):
            return torch.device("cuda")
    return torch.device("cpu")


def _resolve_torch_dtype(dtype_id: str, device: Any) -> Any:
    import torch

    if getattr(device, "type", "cpu") != "cuda":
        return torch.float32

    name = str(dtype_id or "auto").strip().lower()
    if name in ("float16", "fp16", "half"):
        return torch.float16
    if name in ("bfloat16", "bf16"):
        bf16_supported = False
        if hasattr(torch.cuda, "is_bf16_supported"):
            try:
                bf16_supported = bool(torch.cuda.is_bf16_supported())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                bf16_supported = False
        if bf16_supported:
            return torch.bfloat16
        _LOG.warning("Requested bfloat16 is not supported on the active CUDA device. Falling back to float16.")
        return torch.float16
    if name in ("float32", "fp32"):
        return torch.float32
    return torch.float16


def _resolve_torch_device_dtype() -> tuple[Any, Any]:
    device = _resolve_torch_device(getattr(AppConfig, "DEVICE_ID", "cpu"))
    dtype = _resolve_torch_dtype(getattr(AppConfig, "DTYPE_ID", "float32"), device)
    return device, dtype


def _apply_fp32_math_mode(torch_module: Any, mode: str) -> None:
    normalized = str(mode or "ieee").strip().lower()
    precision_mode = "tf32" if normalized == "tf32" else "ieee"

    try:
        torch_module.backends.cuda.matmul.fp32_precision = precision_mode
        torch_module.backends.cudnn.fp32_precision = precision_mode
        torch_module.backends.cudnn.conv.fp32_precision = precision_mode
        torch_module.backends.cudnn.rnn.fp32_precision = precision_mode
    except (AttributeError, RuntimeError, TypeError, ValueError) as ex:
        _LOG.debug("FP32 math mode tuning skipped. detail=%s", ex)


def _cpu_model_name() -> str | None:
    try:
        out = subprocess.check_output(["wmic", "cpu", "get", "name"], stderr=subprocess.DEVNULL, text=True)
        lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
        return lines[1] if len(lines) >= 2 else None
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
        return None


class _TranscriptionModelLoader:
    """Load and cache the transcription pipeline."""

    def __init__(self) -> None:
        self._pipeline: Any = None

    @property
    def pipeline(self) -> Any | None:
        return self._pipeline

    @staticmethod
    def is_enabled() -> bool:
        if AppConfig.SETTINGS is None:
            return False
        engine_name = AppConfig.transcription_model_engine_name()
        return not _is_disabled_engine_name(engine_name)

    def ensure_ready(self) -> Any | None:
        if AppConfig.SETTINGS is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        engine_name = AppConfig.transcription_model_engine_name()
        if _is_disabled_engine_name(engine_name):
            self._pipeline = None
            return None

        model_path = Path(AppConfig.PATHS.TRANSCRIPTION_ENGINE_DIR)
        _require_dir(model_path, error_key="error.model.transcription_missing")

        device, dtype = _resolve_torch_device_dtype()

        low_cpu_mem_usage = bool(AppConfig.engine_low_cpu_mem_usage())
        use_safetensors = bool(AppConfig.USE_SAFETENSORS)

        _LOG.info("Loading transcription model '%s' from '%s'.", engine_name, model_path)
        _LOG.debug(
            (
                "Transcription model load parameters. engine_name=%s device=%s dtype=%s "
                "low_cpu_mem_usage=%s use_safetensors=%s"
            ),
            engine_name,
            str(device),
            getattr(dtype, "__str__", lambda: str(dtype))(),
            low_cpu_mem_usage,
            use_safetensors,
        )

        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(model_path),
            local_files_only=True,
            dtype=dtype,
            low_cpu_mem_usage=low_cpu_mem_usage,
            use_safetensors=use_safetensors,
        )
        try:
            model = model.to(device)
        except (RuntimeError, TypeError, ValueError):
            model = model.to("cpu")

        processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)

        dev = getattr(model, "device", device)
        device_index = int(getattr(dev, "index", 0) or 0) if getattr(dev, "type", "cpu") == "cuda" else -1

        self._pipeline = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=device_index,
            dtype=None if device_index == -1 else dtype,
        )
        _LOG.info("Transcription engine ready.")
        return self._pipeline


class _TranslationModelLoader:
    """Ensure the translation worker is ready."""

    @staticmethod
    def is_enabled() -> bool:
        if AppConfig.SETTINGS is None:
            return False
        engine_name = AppConfig.translation_model_engine_name()
        return not _is_disabled_engine_name(engine_name)

    @staticmethod
    def ensure_ready() -> bool:
        if AppConfig.SETTINGS is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        engine_name = AppConfig.translation_model_engine_name()
        if _is_disabled_engine_name(engine_name):
            return False

        model_path = Path(AppConfig.PATHS.TRANSLATION_ENGINE_DIR)
        _require_dir(model_path, error_key="error.model.translation_missing")

        from app.model.translation.service import TranslationService

        _LOG.info("Loading translation model '%s' from '%s'.", engine_name, model_path)
        return TranslationService().warmup(log=None)


class AIModelsService:
    """Centralized model readiness service (transcription + translation)."""

    @staticmethod
    def apply_engine_runtime(engine: dict[str, Any]) -> None:
        """Resolve and apply runtime device/dtype flags for the current engine settings."""

        try:
            import torch

            has_cuda = bool(torch.cuda.is_available())
            getattr(AppConfig, "HAS_CUDA")
            AppConfig.HAS_CUDA = has_cuda

            bf16_supported = False
            if has_cuda and hasattr(torch.cuda, "is_bf16_supported"):
                try:
                    bf16_supported = bool(torch.cuda.is_bf16_supported())
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    bf16_supported = False
            AppConfig.BF16_SUPPORTED = bool(bf16_supported)

            pref_dev = str((engine or {}).get("preferred_device", "auto") or "auto").strip().lower()
            pref_prec = str((engine or {}).get("precision", "auto") or "auto").strip().lower()
            fp32_math_mode = str((engine or {}).get("fp32_math_mode", "ieee") or "ieee").strip().lower()
            if fp32_math_mode not in ("ieee", "tf32"):
                fp32_math_mode = "ieee"

            if pref_dev in ("cpu",):
                device_id = "cpu"
            elif pref_dev.startswith("cuda") or pref_dev in ("cuda", "gpu"):
                device_id = pref_dev if pref_dev.startswith("cuda") else "cuda:0"
                if not has_cuda:
                    device_id = "cpu"
            else:
                device_id = "cuda:0" if has_cuda else "cpu"

            device = _resolve_torch_device(device_id)
            dtype = _resolve_torch_dtype(pref_prec, device)

            AppConfig.DEVICE_ID = str(device)
            AppConfig.DTYPE_ID = (
                "float16" if dtype is torch.float16 else ("bfloat16" if dtype is torch.bfloat16 else "float32")
            )

            if getattr(device, "type", "cpu") == "cuda" and has_cuda:
                try:
                    props = torch.cuda.get_device_properties(int(getattr(device, "index", 0) or 0))
                    name = getattr(props, "name", None)
                    major = int(getattr(props, "major", 0))
                    AppConfig.DEVICE_KIND = "GPU"
                    AppConfig.DEVICE_MODEL = str(name) if name else None
                    AppConfig.DEVICE_FRIENDLY_NAME = f"GPU ({name})" if name else "GPU"
                    AppConfig.TF32_SUPPORTED = bool(major >= 8)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    AppConfig.DEVICE_KIND = "GPU"
                    AppConfig.DEVICE_MODEL = None
                    AppConfig.DEVICE_FRIENDLY_NAME = "GPU"
                    AppConfig.TF32_SUPPORTED = False
            else:
                AppConfig.DEVICE_KIND = "CPU"
                cpu_name = _cpu_model_name()
                AppConfig.DEVICE_MODEL = cpu_name
                AppConfig.DEVICE_FRIENDLY_NAME = f"CPU ({cpu_name})" if cpu_name else "CPU"
                AppConfig.TF32_SUPPORTED = False

            AppConfig.TF32_ENABLED = bool(
                fp32_math_mode == "tf32"
                and bool(AppConfig.TF32_SUPPORTED)
                and getattr(device, "type", "cpu") == "cuda"
                and dtype is torch.float32
            )

            if getattr(device, "type", "cpu") == "cuda":
                _apply_fp32_math_mode(torch, "tf32" if AppConfig.TF32_ENABLED else "ieee")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            _LOG.exception("Engine runtime setup failed.")
            AppConfig.DEVICE_ID = "cpu"
            AppConfig.DTYPE_ID = "float32"
            AppConfig.DEVICE_KIND = "CPU"
            AppConfig.DEVICE_MODEL = None
            AppConfig.DEVICE_FRIENDLY_NAME = "CPU"
            AppConfig.HAS_CUDA = False
            AppConfig.BF16_SUPPORTED = False
            AppConfig.TF32_SUPPORTED = False
            AppConfig.TF32_ENABLED = False

    def __init__(self) -> None:
        self._loaders: dict[str, Any] = {
            "transcription": _TranscriptionModelLoader(),
            "translation": _TranslationModelLoader(),
        }

    @staticmethod
    def current_model_cfg(task: str) -> dict[str, Any]:
        """Return the active model configuration for a task with runtime metadata."""
        return _current_model_cfg(task=task)

    def _loader_for_task(self, task: str) -> Any:
        return self._loaders[_normalize_model_task(task)]

    def is_enabled(self, task: str) -> bool:
        return bool(self._loader_for_task(task).is_enabled())

    def ensure_ready(self, task: str) -> Any:
        return self._loader_for_task(task).ensure_ready()
