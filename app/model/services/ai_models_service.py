# app/model/services/ai_models_service.py
from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple, TYPE_CHECKING

from app.model.config.app_config import AppConfig as Config, ConfigError
from app.model.helpers.errors import AppError

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    import torch


# ----- Errors -----
class ModelNotInstalledError(AppError):
    """Raised when a required local model directory is missing."""

    def __init__(self, key: str, path: Path) -> None:
        super().__init__(key=str(key), params={"path": str(path)})


# ----- Local model catalog -----
def _read_json_dict(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def normalize_model_type(model_type: str) -> str:
    return str(model_type or "").strip().lower()


def task_for_model_type(model_type: str) -> str:
    norm = normalize_model_type(model_type)
    if norm in Config.TRANSCRIPTION_MODEL_TYPES:
        return "transcription"
    if norm in Config.TRANSLATION_MODEL_TYPES:
        return "translation"
    return ""


def model_signature(config_data: Dict[str, Any]) -> str:
    if not isinstance(config_data, dict) or not config_data:
        return ""

    stable = {
        k: v
        for k, v in config_data.items()
        if str(k) not in ("_name_or_path", "transformers_version")
    }
    try:
        payload = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except Exception:
        return ""
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest().lower()


def local_model_descriptor(model_name: str) -> Dict[str, Any]:
    name = str(model_name or "").strip()
    if not name or name.startswith("__"):
        return {}

    model_dir = Config.AI_MODELS_DIR / name
    if not model_dir.exists() or not model_dir.is_dir():
        return {}

    cfg_path = model_dir / Config.MODEL_CONFIG_FILE
    if not cfg_path.exists() or not cfg_path.is_file():
        return {}

    cfg = _read_json_dict(cfg_path)
    model_type = normalize_model_type(cfg.get("model_type", ""))
    task = task_for_model_type(model_type)
    signature = model_signature(cfg)

    return {
        "name": model_dir.name,
        "path": model_dir,
        "config_path": cfg_path,
        "model_type": model_type,
        "task": task,
        "signature": signature,
    }


def local_model_descriptors() -> Tuple[Dict[str, Any], ...]:
    if not Config.AI_MODELS_DIR.exists() or not Config.AI_MODELS_DIR.is_dir():
        return tuple()

    out: list[Dict[str, Any]] = []
    for p in sorted(Config.AI_MODELS_DIR.iterdir(), key=lambda item: item.name.lower()):
        if not p.is_dir() or p.name.startswith("__"):
            continue
        desc = local_model_descriptor(p.name)
        if desc:
            out.append(desc)
    return tuple(out)


def local_models_for_task(task: str) -> Tuple[Dict[str, Any], ...]:
    wanted = str(task or "").strip().lower()
    return tuple(d for d in local_model_descriptors() if str(d.get("task", "")) == wanted)


def local_model_names_for_task(task: str) -> Tuple[str, ...]:
    return tuple(str(d.get("name", "")) for d in local_models_for_task(task) if d.get("name"))


def autoselect_engine_name(*, task: str) -> str:
    for desc in local_models_for_task(task):
        name = str(desc.get("name", "")).strip()
        if name:
            return name
    return ""


def resolve_engine_name(model_cfg: Dict[str, Any], *, task: str) -> str:
    cfg = model_cfg if isinstance(model_cfg, dict) else {}
    raw = str(cfg.get("engine_name", "none") or "none").strip()
    low = raw.lower()

    if is_disabled_engine_name(low):
        return Config.MISSING_VALUE
    if low == "auto":
        pick = autoselect_engine_name(task=task)
        return pick if pick else Config.MISSING_VALUE

    desc = local_model_descriptor(raw)
    if desc and str(desc.get("task", "")) == str(task or "").strip().lower():
        return str(desc.get("name") or raw)

    sig = str(cfg.get("engine_signature", "") or "").strip().lower()
    model_type = normalize_model_type(cfg.get("engine_model_type", ""))
    matches: list[str] = []
    for cand in local_models_for_task(task):
        cand_type = normalize_model_type(cand.get("model_type", ""))
        cand_sig = str(cand.get("signature", "") or "").strip().lower()
        if model_type and cand_type != model_type:
            continue
        if sig and cand_sig != sig:
            continue
        matches.append(str(cand.get("name", "")).strip())

    if sig and len(matches) == 1:
        return matches[0]
    return Config.MISSING_VALUE


# ----- Runtime helpers -----
def is_disabled_engine_name(name: str) -> bool:
    n = str(name or "").strip().lower()
    return (not n) or n in ("none", "off", "disabled")


def _require_dir(path: Path, *, error_key: str) -> None:
    if path.exists() and path.is_dir() and path.name != Config.MISSING_VALUE:
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
        except Exception:
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
        return torch.bfloat16
    if name in ("float32", "fp32"):
        return torch.float32
    return torch.float16


def resolve_torch_device_dtype() -> Tuple[Any, Any]:
    device = _resolve_torch_device(getattr(Config, "DEVICE_ID", "cpu"))
    dtype = _resolve_torch_dtype(getattr(Config, "DTYPE_ID", "float32"), device)
    return device, dtype


def _cpu_model_name() -> str | None:
    sysname = platform.system().lower()
    try:
        if sysname.startswith("win"):
            cmd = ["wmic", "cpu", "get", "name"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
            lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
            return lines[1] if len(lines) >= 2 else None
        if sysname == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], stderr=subprocess.DEVNULL, text=True)
            return str(out or "").strip() or None
        if Path("/proc/cpuinfo").exists():
            txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            for line in txt.splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[-1].strip() or None
    except Exception:
        return None
    return None


class TranscriptionModelLoader:
    """Load and cache the transcription pipeline."""

    def __init__(self) -> None:
        self._pipeline: Any = None

    @property
    def pipeline(self) -> Any | None:
        return self._pipeline

    def is_enabled(self) -> bool:
        snap = Config.SETTINGS
        if snap is None:
            return False
        model_cfg = snap.model.get("transcription_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()
        return not is_disabled_engine_name(engine_name)

    def ensure_ready(self) -> Any | None:
        snap = Config.SETTINGS
        if snap is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        model_cfg = snap.model.get("transcription_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()
        if is_disabled_engine_name(engine_name):
            self._pipeline = None
            return None

        model_path = Path(Config.TRANSCRIPTION_ENGINE_DIR)
        _require_dir(model_path, error_key="error.model.transcription_missing")

        device, dtype = resolve_torch_device_dtype()

        low_cpu_mem_usage = bool(Config.ENGINE_LOW_CPU_MEM_USAGE)
        use_safetensors = bool(Config.USE_SAFETENSORS)

        _LOG.info("Loading transcription model '%s' from '%s'.", engine_name, model_path)
        _LOG.debug(
            "Transcription model load parameters. engine_name=%s device=%s dtype=%s low_cpu_mem_usage=%s use_safetensors=%s",
            engine_name,
            str(device),
            getattr(dtype, "__str__", lambda: str(dtype))(),
            low_cpu_mem_usage,
            use_safetensors,
        )

        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
        import torch

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(model_path),
            local_files_only=True,
            dtype=dtype,
            low_cpu_mem_usage=low_cpu_mem_usage,
            use_safetensors=use_safetensors,
        )
        try:
            model = model.to(device)
        except Exception:
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


class TranslationModelLoader:
    """Ensure the translation worker is ready."""

    def is_enabled(self) -> bool:
        snap = Config.SETTINGS
        if snap is None:
            return False
        model_cfg = snap.model.get("translation_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()
        return not is_disabled_engine_name(engine_name)

    def ensure_ready(self) -> bool:
        snap = Config.SETTINGS
        if snap is None:
            raise ConfigError("error.runtime.settings_not_initialized")

        model_cfg = snap.model.get("translation_model", {}) if isinstance(snap.model, dict) else {}
        engine_name = str(model_cfg.get("engine_name", "none") or "none").strip()
        if is_disabled_engine_name(engine_name):
            return False

        model_path = Path(Config.TRANSLATION_ENGINE_DIR)
        _require_dir(model_path, error_key="error.model.translation_missing")

        from app.model.services.translation_service import TranslationService

        _LOG.info("Loading translation model '%s' from '%s'.", engine_name, model_path)
        return bool(TranslationService().warmup(log=None))


class AIModelsService:
    """Centralized model readiness service (transcription + translation)."""

    @staticmethod
    def apply_engine_runtime(engine: dict) -> None:
        """Resolve and apply runtime device/dtype flags for the current engine settings."""

        try:
            import torch

            has_cuda = bool(torch.cuda.is_available())
            getattr(Config, "HAS_CUDA")
            Config.HAS_CUDA = has_cuda

            bf16_supported = False
            if has_cuda and hasattr(torch.cuda, "is_bf16_supported"):
                try:
                    bf16_supported = bool(torch.cuda.is_bf16_supported())
                except Exception:
                    bf16_supported = False
            Config.BF16_SUPPORTED = bool(bf16_supported)

            pref_dev = str((engine or {}).get("preferred_device", "auto") or "auto").strip().lower()
            pref_prec = str((engine or {}).get("precision", "auto") or "auto").strip().lower()
            allow_tf32 = bool((engine or {}).get("allow_tf32", False))

            device_id = "cpu"
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

            Config.DEVICE_ID = str(device)
            Config.DTYPE_ID = "float16" if dtype is torch.float16 else ("bfloat16" if dtype is torch.bfloat16 else "float32")

            if getattr(device, "type", "cpu") == "cuda" and has_cuda:
                try:
                    props = torch.cuda.get_device_properties(int(getattr(device, "index", 0) or 0))
                    name = getattr(props, "name", None)
                    major = int(getattr(props, "major", 0))
                    Config.DEVICE_KIND = "GPU"
                    Config.DEVICE_MODEL = str(name) if name else None
                    Config.DEVICE_FRIENDLY_NAME = f"GPU ({name})" if name else "GPU"
                    Config.TF32_SUPPORTED = bool(major >= 8)
                except Exception:
                    Config.DEVICE_KIND = "GPU"
                    Config.DEVICE_MODEL = None
                    Config.DEVICE_FRIENDLY_NAME = "GPU"
                    Config.TF32_SUPPORTED = False
            else:
                Config.DEVICE_KIND = "CPU"
                cpu_name = _cpu_model_name()
                Config.DEVICE_MODEL = cpu_name
                Config.DEVICE_FRIENDLY_NAME = f"CPU ({cpu_name})" if cpu_name else "CPU"
                Config.TF32_SUPPORTED = False

            Config.TF32_ENABLED = bool(
                allow_tf32
                and bool(Config.TF32_SUPPORTED)
                and getattr(device, "type", "cpu") == "cuda"
                and dtype is torch.float32
            )

            if getattr(device, "type", "cpu") == "cuda":
                try:
                    torch.backends.cuda.matmul.allow_tf32 = bool(Config.TF32_ENABLED)
                    torch.backends.cudnn.allow_tf32 = bool(Config.TF32_ENABLED)
                    torch.set_float32_matmul_precision("medium")
                except Exception:
                    pass
        except Exception:
            _LOG.exception("Engine runtime setup failed.")
            Config.DEVICE_ID = "cpu"
            Config.DTYPE_ID = "float32"
            Config.DEVICE_KIND = "CPU"
            Config.DEVICE_MODEL = None
            Config.DEVICE_FRIENDLY_NAME = "CPU"
            Config.HAS_CUDA = False
            Config.BF16_SUPPORTED = False
            Config.TF32_SUPPORTED = False
            Config.TF32_ENABLED = False

    def __init__(self) -> None:
        self.transcription = TranscriptionModelLoader()
        self.translation = TranslationModelLoader()

    def ensure_transcription_ready(self) -> Any | None:
        return self.transcription.ensure_ready()

    def ensure_translation_ready(self) -> bool:
        return self.translation.ensure_ready()
