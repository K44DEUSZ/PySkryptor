# app/controller/support/runtime_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, List

from urllib.parse import urlparse, parse_qs

from app.model.io.media_probe import is_url_source
from app.model.helpers.string_utils import normalize_lang_code
from app.model.config.app_config import AppConfig as Config
from app.model.services.settings_service import SettingsCatalog
from app.model.io.file_manager import FileManager


# ----- Source helpers -----

def is_url(value: str) -> bool:
    """Return True if the input should be treated as a URL source."""
    return bool(is_url_source(value))


# ----- Language resolution -----

def translation_language_codes() -> List[str]:
    """Return supported translation language codes as a sorted list."""
    return sorted(SettingsCatalog.translation_language_codes())


def transcription_language_codes() -> List[str]:
    """Return supported transcription language codes as a sorted list."""
    return sorted(SettingsCatalog.transcription_language_codes())


def supported_translation_lang_codes() -> set[str]:
    """Return supported translation language codes as a set."""
    return set(SettingsCatalog.translation_language_codes())


def resolve_translation_target(
    target_code: str,
    *,
    ui_language: str,
    cfg_target: str | None = None,
    supported: Optional[Iterable[str]] = None,
) -> str:
    """Resolve translation target language code."""
    raw = (target_code  or "").strip().lower()
    cfg = (cfg_target   or "").strip().lower()
    ui  = (ui_language  or "").strip().lower()
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


# ----- Settings patches -----

def build_files_transcription_patch(
    *,
    translate_after_transcription: bool,
    output_formats: list[str],
    download_audio_only: bool,
    url_keep_audio: bool,
    url_audio_ext: str,
    url_keep_video: bool,
    url_video_ext: str,
) -> Dict[str, Any]:
    """Build a minimal transcription patch for FilesPanel quick options."""
    fmts = [str(x).strip().lower() for x in (output_formats or []) if str(x).strip()]
    if not fmts:
        fmts = ["txt"]

    aext = str(url_audio_ext or "m4a").strip().lower().lstrip(".") or "m4a"
    vext = str(url_video_ext or "mp4").strip().lower().lstrip(".") or "mp4"

    return {
        "translate_after_transcription": bool(translate_after_transcription),
        "output_formats":               fmts,
        "download_audio_only":          bool(download_audio_only),
        "url_keep_audio":              bool(url_keep_audio),
        "url_audio_ext":               aext,
        "url_keep_video":              bool(url_keep_video) and (not bool(download_audio_only)),
        "url_video_ext":               vext,
    }


def build_files_quick_options_payload(
    *,
    transcription_patch: Dict[str, Any],
    source_language: str,
    target_language: str,
) -> Dict[str, Any]:
    """Build a SettingsWorker payload for FilesPanel quick options."""
    src = str(source_language or Config.LANGUAGE_AUTO_VALUE).strip().lower() or Config.LANGUAGE_AUTO_VALUE
    tgt = str(target_language or Config.LANGUAGE_AUTO_VALUE).strip().lower() or Config.LANGUAGE_AUTO_VALUE
    return {
        "transcription": dict(transcription_patch or {}),
        "translation": {
            "source_language": src,
            "target_language": tgt,
        },
    }


@dataclass(frozen=True)
class TranslationRuntime:
    """Resolved translation state for a worker run."""
    enabled: bool
    target_language: str


def build_live_quick_options_payload(
    *,
    mode: str,
    preset: str,
    device_name: str,
    show_source: bool,
    source_language: str,
    target_language: str,
) -> Dict[str, Any]:
    """Build a SettingsWorker payload for LivePanel quick options."""
    m = str(mode or "transcribe").strip().lower()
    if m not in ("transcribe", "transcribe_translate"):
        m = "transcribe"

    p = str(preset or "balanced").strip().lower()
    if p not in ("low_latency", "balanced", "high_context"):
        p = "balanced"

    dev = str(device_name or "").strip()
    src = str(source_language or Config.LANGUAGE_AUTO_VALUE).strip().lower() or Config.LANGUAGE_AUTO_VALUE
    tgt = str(target_language or Config.LANGUAGE_AUTO_VALUE).strip().lower() or Config.LANGUAGE_AUTO_VALUE

    return {
        "app": {
            "ui": {
                "live": {
                    "mode": m,
                    "preset": p,
                    "device_name": dev,
                    "show_source": bool(show_source),
                },
            },
        },
        "translation": {
            "source_language": src,
            "target_language": tgt,
        },
    }


def compute_translation_runtime(
    *,
    requested_enabled: bool,
    target_code: str,
    ui_language: str,
    cfg_target: str | None = None,
    supported: Optional[Iterable[str]] = None,
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


# ----- URL helpers -----

def is_playlist_url(url: str) -> bool:
    """Return True when the given URL likely points to a playlist."""
    u = str(url or "").strip()
    if not u:
        return False

    try:
        parsed = urlparse(u)
        qs = parse_qs(parsed.query or "")
        if qs.get("list"):
            return True
        if "playlist" in (parsed.path or "").lower():
            return True
        if "list=" in (parsed.fragment or ""):
            return True
    except Exception:
        pass
    return False


# ----- Source helpers -----

def _files_media_supported_exts() -> List[str]:
    return list(Config.files_media_input_file_exts())


def parse_source_input(raw: str) -> Dict[str, Any]:
    """Parse a raw source input from the Files panel."""
    return FileManager.parse_source_input(
        raw,
        supported_exts=_files_media_supported_exts(),
    )


def collect_media_files(paths: List[str]) -> List[str]:
    """Collect media files from the given paths for the Files panel."""
    return FileManager.collect_media_files(
        list(paths),
        supported_exts=_files_media_supported_exts(),
    )


def normalize_source_key(raw: str) -> str:
    """Normalize a user-provided source key (path/URL)."""
    return str(raw or "").strip()


def try_add_source_key(existing: set[str], raw: str) -> tuple[bool, str, bool]:
    """Try add a source key, returning (ok, normalized_key, duplicate)."""
    key = normalize_source_key(raw)
    if not key:
        return False, "", False
    if key in existing:
        return False, key, True
    existing.add(key)
    return True, key, False


def build_entries(keys: Iterable[str], audio_lang_by_key: Dict[str, str | None]) -> list[Dict[str, Any]]:
    """Build worker input entries for the given sources."""
    out: list[Dict[str, Any]] = []
    for k in keys:
        src = normalize_source_key(k)
        if not src:
            continue
        payload: Dict[str, Any] = {"src": src}
        if is_url_source(src):
            lang = audio_lang_by_key.get(src)
            if lang:
                payload["audio_lang"] = lang
        out.append(payload)
    return out
