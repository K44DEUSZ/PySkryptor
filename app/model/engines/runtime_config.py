# app/model/engines/runtime_config.py
from __future__ import annotations

import logging
import subprocess
from typing import Any

from app.model.core.config.config import AppConfig

_LOG = logging.getLogger(__name__)


def resolve_torch_device(device_id: str) -> Any:
    """Resolve the configured torch device with safe CUDA fallback handling."""

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


def resolve_torch_dtype(dtype_id: str, device: Any) -> Any:
    """Resolve the configured torch dtype for the active device."""

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


def resolve_torch_device_dtype() -> tuple[Any, Any]:
    """Resolve the configured torch device and dtype pair."""

    device = resolve_torch_device(AppConfig.DEVICE_ID)
    dtype = resolve_torch_dtype(AppConfig.DTYPE_ID, device)
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


def apply_engine_runtime(engine: dict[str, Any]) -> None:
    """Apply runtime device and precision flags derived from settings."""

    try:
        import torch

        has_cuda = bool(torch.cuda.is_available())
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

        device = resolve_torch_device(device_id)
        dtype = resolve_torch_dtype(pref_prec, device)

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
        _LOG.error("Engine runtime setup failed.", exc_info=True)
        AppConfig.DEVICE_ID = "cpu"
        AppConfig.DTYPE_ID = "float32"
        AppConfig.DEVICE_KIND = "CPU"
        AppConfig.DEVICE_MODEL = None
        AppConfig.DEVICE_FRIENDLY_NAME = "CPU"
        AppConfig.HAS_CUDA = False
        AppConfig.BF16_SUPPORTED = False
        AppConfig.TF32_ENABLED = False
        AppConfig.TF32_SUPPORTED = False
