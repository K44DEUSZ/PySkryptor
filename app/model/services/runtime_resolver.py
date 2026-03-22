# app/model/services/runtime_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, TypeAlias

from urllib.parse import urlparse, parse_qs

from app.model.io.media_probe import is_url_source
from app.model.domain.runtime_state import AppRuntimeState
from app.model.helpers.string_utils import normalize_lang_code
from app.model.config.app_config import AppConfig as Config
from app.model.services.ai_models_service import current_transcription_model_cfg, current_translation_model_cfg
from app.model.services.download_service import DownloadService
from app.model.services.settings_service import SettingsCatalog
from app.model.io.file_manager import FileManager
from app.model.domain.entities import TranscriptionSessionRequest

PatchPayload: TypeAlias = dict[str, Any]
EntryPayload: TypeAlias = dict[str, Any]


def is_url(value: str) -> bool:
    """Return True if the input should be treated as a URL source."""
    return bool(is_url_source(value))

def translation_language_codes() -> list[str]:
    """Return supported translation language codes as a sorted list."""
    return sorted(SettingsCatalog.translation_language_codes())

def transcription_language_codes() -> list[str]:
    """Return supported transcription language codes as a sorted list."""
    return sorted(SettingsCatalog.transcription_language_codes())

def transcription_output_modes() -> tuple[dict[str, Any], ...]:
    """Return configured transcription output modes for UI selectors."""
    return SettingsCatalog.transcription_output_modes()

def active_transcription_model_cfg() -> PatchPayload:
    """Return active transcription model metadata for view/runtime summaries."""
    return current_transcription_model_cfg()

def active_translation_model_cfg() -> PatchPayload:
    """Return active translation model metadata for view/runtime summaries."""
    return current_translation_model_cfg()

def available_audio_bitrates(meta: dict[str, Any] | None = None) -> tuple[int, ...]:
    """Return sorted audio bitrate candidates resolved from probe metadata."""
    return tuple(DownloadService.available_audio_bitrates(meta))

def available_video_heights(
    meta: dict[str, Any] | None = None,
    *,
    min_h: int = 0,
    max_h: int = 0,
) -> tuple[int, ...]:
    """Return sorted video height candidates resolved from probe metadata."""
    return tuple(DownloadService.available_video_heights(meta, min_h=min_h, max_h=max_h))

def supported_translation_lang_codes() -> set[str]:
    """Return supported translation language codes as a set."""
    return set(SettingsCatalog.translation_language_codes())

def resolve_translation_target(
    target_code: str,
    *,
    ui_language: str,
    cfg_target: str | None = None,
    supported: Iterable[str] | None = None,
) -> str:
    """Resolve translation target language code."""
    raw = Config.normalize_language_choice_value(target_code)
    cfg = Config.normalize_language_choice_value(cfg_target)
    ui = (ui_language or "").strip().lower()
    sup = set(supported) if supported is not None else supported_translation_lang_codes()

    def _is_auto(v: str) -> bool:
        return (not v) or v in Config.TRANSLATION_TARGET_DEFERRED_VALUES

    if not _is_auto(raw):
        cand = raw
    elif cfg and not _is_auto(cfg):
        cand = cfg
    else:
        cand = ui

    cand = normalize_lang_code(cand, drop_region=True)
    if not cand:
        return ""
    return cand if cand in sup else ""

def resolve_source_language(source_code: str, *, cfg_default: str | None = None) -> str:
    """Resolve a transcription source language to a normalized code or ``auto``."""
    raw = Config.normalize_language_choice_value(source_code)
    if raw and not Config.is_auto_language_value(raw):
        return normalize_lang_code(raw, drop_region=True) or Config.LANGUAGE_AUTO_VALUE

    cfg = Config.normalize_language_choice_value(cfg_default or Config.transcription_default_language())
    if cfg and not Config.is_auto_language_value(cfg):
        return normalize_lang_code(cfg, drop_region=True) or Config.LANGUAGE_AUTO_VALUE

    return Config.LANGUAGE_AUTO_VALUE

def build_files_transcription_patch(
    *,
    translate_after_transcription: bool,
    output_formats: list[str],
    download_audio_only: bool,
    url_keep_audio: bool,
    url_audio_ext: str,
    url_keep_video: bool,
    url_video_ext: str,
) -> PatchPayload:
    """Build a minimal transcription patch for FilesPanel quick options."""
    fmts = [str(x).strip().lower() for x in (output_formats or []) if str(x).strip()]
    if not fmts:
        fmts = list(Config.transcription_output_mode_ids())

    audio_ext = str(url_audio_ext or Config.transcription_url_audio_ext()).strip().lower().lstrip(".")
    video_ext = str(url_video_ext or Config.transcription_url_video_ext()).strip().lower().lstrip(".")

    return {
        "translate_after_transcription": bool(translate_after_transcription),
        "output_formats": fmts,
        "download_audio_only": bool(download_audio_only),
        "url_keep_audio": bool(url_keep_audio),
        "url_audio_ext": audio_ext or Config.transcription_url_audio_ext(),
        "url_keep_video": bool(url_keep_video) and (not bool(download_audio_only)),
        "url_video_ext": video_ext or Config.transcription_url_video_ext(),
    }

def build_files_quick_options_payload(
    *,
    transcription_patch: PatchPayload,
    target_language: str,
) -> dict[str, Any]:
    """Build a SettingsWorker payload for FilesPanel quick options."""
    tgt = Config.normalize_language_choice_value(target_language or Config.LANGUAGE_DEFAULT_UI_VALUE) or Config.LANGUAGE_DEFAULT_UI_VALUE
    return {
        "transcription": dict(transcription_patch or {}),
        "translation": {
            "target_language": tgt,
        },
    }

def build_transcription_session_request(
    *,
    source_language: str,
    target_language: str,
    translate_after_transcription: bool,
    output_formats: list[str],
    download_audio_only: bool,
    url_keep_audio: bool,
    url_audio_ext: str,
    url_keep_video: bool,
    url_video_ext: str,
    ui_language: str,
    cfg_target: str | None = None,
    supported: Iterable[str] | None = None,
) -> TranscriptionSessionRequest:
    """Build a normalized session request for a transcription run."""
    patch = build_files_transcription_patch(
        translate_after_transcription=bool(translate_after_transcription),
        output_formats=list(output_formats or []),
        download_audio_only=bool(download_audio_only),
        url_keep_audio=bool(url_keep_audio),
        url_audio_ext=str(url_audio_ext or ""),
        url_keep_video=bool(url_keep_video),
        url_video_ext=str(url_video_ext or ""),
    )

    tgt_raw = str(target_language or "").strip().lower()
    tgt_resolved = resolve_translation_target(
        tgt_raw,
        ui_language=ui_language,
        cfg_target=cfg_target,
        supported=supported,
    )

    fmts_raw = patch.get("output_formats")
    if isinstance(fmts_raw, (list, tuple)):
        output_mode_ids = tuple(str(mode_id or "").strip().lower() for mode_id in fmts_raw if str(mode_id or "").strip())
    else:
        output_mode_ids = tuple()
    if not output_mode_ids:
        output_mode_ids = tuple(Config.transcription_output_mode_ids())

    return TranscriptionSessionRequest(
        source_language=resolve_source_language(source_language),
        target_language=normalize_lang_code(tgt_resolved, drop_region=True) if tgt_resolved else "",
        translate_after_transcription=bool(patch.get("translate_after_transcription", False)),
        output_formats=output_mode_ids,
        download_audio_only=bool(patch.get("download_audio_only", False)),
        url_keep_audio=bool(patch.get("url_keep_audio", False)),
        url_audio_ext=str(patch.get("url_audio_ext") or Config.transcription_url_audio_ext()).strip().lower(),
        url_keep_video=bool(patch.get("url_keep_video", False)),
        url_video_ext=str(patch.get("url_video_ext") or Config.transcription_url_video_ext()).strip().lower(),
    )

def translation_runtime_available(
    *,
    runtime_state: AppRuntimeState | None = None,
    model_cfg: dict[str, Any] | None = None,
) -> bool:
    """Return True when translation runtime is available for UI actions."""
    if runtime_state is not None and bool(str(runtime_state.translation_error_key or "").strip()):
        return False

    engine_dir = getattr(Config, "TRANSLATION_ENGINE_DIR", None)
    engine_dir_name = str(getattr(engine_dir, "name", "") or "").strip()
    if not engine_dir_name or engine_dir_name == Config.MISSING_VALUE:
        return False

    cfg = model_cfg if isinstance(model_cfg, dict) else Config.translation_model_raw_cfg_dict()
    eng = str(cfg.get("engine_name", "") or "").strip().lower()
    return not Config.is_disabled_engine_name(eng)

@dataclass(frozen=True)
class TranslationRuntime:
    """Resolved translation state for a worker run."""
    enabled: bool
    target_language: str

def build_live_quick_options_payload(
    *,
    mode: str,
    preset: str,
    output_mode: str,
    device_name: str,
    target_language: str,
) -> dict[str, Any]:
    """Build a SettingsWorker payload for LivePanel quick options."""
    m = Config.normalize_live_ui_mode(mode or Config.live_ui_mode())
    p = Config.normalize_live_preset(preset or Config.live_ui_preset())
    out_mode = Config.normalize_live_output_mode(output_mode or Config.live_ui_output_mode())

    dev = str(device_name or "").strip()
    tgt = Config.normalize_language_choice_value(target_language or Config.LANGUAGE_DEFAULT_UI_VALUE) or Config.LANGUAGE_DEFAULT_UI_VALUE

    return {
        "app": {
            "ui": {
                "live": {
                    "mode": m,
                    "preset": p,
                    "output_mode": out_mode,
                    "device_name": dev,
                },
            },
        },
        "translation": {
            "target_language": tgt,
        },
    }

def compute_translation_runtime(
    *,
    requested_enabled: bool,
    target_code: str,
    ui_language: str,
    cfg_target: str | None = None,
    supported: Iterable[str] | None = None,
) -> TranslationRuntime:
    """Compute final translation enablement and target language."""
    if not requested_enabled:
        return TranslationRuntime(False, "")
    tgt = resolve_translation_target(
        target_code,
        ui_language=ui_language,
        cfg_target=cfg_target,
        supported=supported,
    )
    return TranslationRuntime(bool(tgt), tgt)

def is_playlist_url(url: str) -> bool:
    """Return True when the given URL likely points to a playlist."""
    u = str(url or "").strip()
    if not u:
        return False

    parsed = urlparse(u)
    qs = parse_qs(parsed.query or "")
    if qs.get("list"):
        return True
    if "playlist" in (parsed.path or "").lower():
        return True
    if "list=" in (parsed.fragment or ""):
        return True
    return False

def _files_media_supported_extensions() -> list[str]:
    return list(Config.files_media_input_file_exts())

def parse_source_input(raw: str) -> EntryPayload:
    """Parse a raw source input from the Files panel."""
    return FileManager.parse_source_input(
        raw,
        supported_exts=_files_media_supported_extensions(),
    )

def collect_media_files(paths: list[str]) -> list[str]:
    """Collect media files from the given paths for the Files panel."""
    return FileManager.collect_media_files(
        list(paths),
        supported_exts=_files_media_supported_extensions(),
    )

def normalize_source_key(raw: str) -> str:
    """Normalize a user-provided source key (path/URL)."""
    return str(raw or "").strip()

def try_add_source_key(existing: set[str], raw: str) -> tuple[bool, str, bool]:
    """Try to add a source key, returning (ok, normalized_key, duplicate)."""
    key = normalize_source_key(raw)
    if not key:
        return False, "", False
    if key in existing:
        return False, key, True
    existing.add(key)
    return True, key, False

def build_entries(keys: Iterable[str], audio_lang_by_key: dict[str, str | None]) -> list[EntryPayload]:
    """Build worker input entries for the given sources."""
    out: list[EntryPayload] = []
    for k in keys:
        src = normalize_source_key(k)
        if not src:
            continue
        payload: EntryPayload = {"src": src}
        if is_url_source(src):
            lang = audio_lang_by_key.get(src)
            if lang:
                payload["audio_lang"] = lang
        out.append(payload)
    return out
