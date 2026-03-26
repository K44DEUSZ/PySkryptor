# app/model/runtime_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, TypeAlias

from app.model.config.app_config import AppConfig as Config
from app.model.config.language_policy import LanguagePolicy
from app.model.config.model_registry import ModelRegistry
from app.model.config.runtime_profiles import RuntimeProfiles
from app.model.domain.entities import TranscriptionSessionRequest
from app.model.helpers.string_utils import normalize_lang_code
from app.model.services.settings_service import SettingsCatalog

PatchPayload: TypeAlias = dict[str, Any]
EntryPayload: TypeAlias = dict[str, Any]


def translation_language_codes() -> list[str]:
    """Return supported translation language codes as a sorted list."""
    return sorted(SettingsCatalog.translation_language_codes())


def transcription_language_codes() -> list[str]:
    """Return supported transcription language codes as a sorted list."""
    return sorted(SettingsCatalog.transcription_language_codes())


def transcription_output_modes() -> tuple[dict[str, Any], ...]:
    """Return configured transcription output modes for UI selectors."""
    return SettingsCatalog.transcription_output_modes()


def supported_translation_lang_codes() -> set[str]:
    """Return supported translation language codes as a set."""
    return set(SettingsCatalog.translation_language_codes())


def resolve_source_language_for_run(
    tab_name: str,
    source_code: str,
    *,
    supported: Iterable[str] | None = None,
) -> str:
    """Resolve a source language selection into a concrete source language value."""
    raw = LanguagePolicy.normalize_panel_source_language_selection(source_code)
    supported_codes = set(supported) if supported is not None else set(transcription_language_codes())

    if LanguagePolicy.is_preferred(raw):
        resolved = Config.resolve_default_source_language_for_tab(tab_name)
    elif LanguagePolicy.is_auto(raw):
        resolved = LanguagePolicy.AUTO
    else:
        resolved = normalize_lang_code(raw, drop_region=False) or LanguagePolicy.AUTO

    if LanguagePolicy.is_auto(resolved):
        return LanguagePolicy.AUTO
    if supported_codes:
        return resolved if resolved in supported_codes else LanguagePolicy.AUTO
    return resolved or LanguagePolicy.AUTO


def resolve_target_language_for_run(
    tab_name: str,
    target_code: str,
    *,
    ui_language: str,
    supported: Iterable[str] | None = None,
) -> str:
    """Resolve a target language selection into a concrete translation target."""
    raw = LanguagePolicy.normalize_panel_target_language_selection(target_code)
    supported_codes = set(supported) if supported is not None else supported_translation_lang_codes()

    if LanguagePolicy.is_preferred(raw):
        resolved = Config.resolve_default_target_language_for_tab(tab_name, ui_language=ui_language)
    elif LanguagePolicy.is_default_ui(raw):
        resolved = normalize_lang_code(ui_language, drop_region=True) or LanguagePolicy.DEFAULT_UI
    else:
        resolved = normalize_lang_code(raw, drop_region=True)

    resolved = normalize_lang_code(resolved, drop_region=True)
    if not resolved:
        return ""
    return resolved if (not supported_codes or resolved in supported_codes) else ""


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


def build_tab_last_used_language_payload(
    *,
    tab_name: str,
    source_language: str | None = None,
    target_language: str | None = None,
) -> dict[str, Any]:
    """Build a minimal settings payload for per-tab last-used language values."""
    tab = str(tab_name or "").strip().lower()
    tab_cfg: dict[str, Any] = {}
    if source_language is not None:
        tab_cfg["last_used_source_language"] = LanguagePolicy.normalize_last_used_source_language(source_language)
    if target_language is not None:
        tab_cfg["last_used_target_language"] = LanguagePolicy.normalize_last_used_target_language(target_language)
    if not tab_cfg:
        return {}
    return {"app": {"ui": {tab: tab_cfg}}}


def build_files_quick_options_payload(
    *,
    transcription_patch: PatchPayload,
    source_language_selection: str,
    target_language_selection: str,
) -> dict[str, Any]:
    """Build a SettingsWorker payload for FilesPanel quick options."""
    payload: dict[str, Any] = {"transcription": dict(transcription_patch or {})}

    source_sel = LanguagePolicy.normalize_panel_source_language_selection(source_language_selection)
    target_sel = LanguagePolicy.normalize_panel_target_language_selection(target_language_selection)
    last_used_patch = build_tab_last_used_language_payload(
        tab_name="files",
        source_language=None if LanguagePolicy.is_preferred(source_sel) else source_sel,
        target_language=None if LanguagePolicy.is_preferred(target_sel) else target_sel,
    )
    if last_used_patch:
        payload.update(last_used_patch)
    return payload


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

    fmts_raw = patch.get("output_formats")
    if isinstance(fmts_raw, (list, tuple)):
        output_mode_ids = tuple(str(mode_id or "").strip().lower() for mode_id in fmts_raw if str(mode_id or "").strip())
    else:
        output_mode_ids = tuple()
    if not output_mode_ids:
        output_mode_ids = tuple(Config.transcription_output_mode_ids())

    src = LanguagePolicy.AUTO if LanguagePolicy.is_auto(source_language) else (normalize_lang_code(source_language, drop_region=False) or LanguagePolicy.AUTO)
    tgt = normalize_lang_code(target_language, drop_region=True) if str(target_language or "").strip() else ""

    return TranscriptionSessionRequest(
        source_language=src,
        target_language=tgt,
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
    translation_error_key: str | None = None,
    model_cfg: dict[str, Any] | None = None,
) -> bool:
    """Return True when translation runtime is available for UI actions."""
    if bool(str(translation_error_key or "").strip()):
        return False

    engine_dir = getattr(getattr(Config, "PATHS", None), "TRANSLATION_ENGINE_DIR", None)
    engine_dir_name = str(getattr(engine_dir, "name", "") or "").strip()
    if not engine_dir_name or engine_dir_name == Config.MISSING_VALUE:
        return False

    cfg = model_cfg if isinstance(model_cfg, dict) else Config.translation_model_raw_cfg_dict()
    eng = str(cfg.get("engine_name", "") or "").strip().lower()
    return not ModelRegistry.is_disabled_engine_name(eng)


@dataclass(frozen=True)
class TranslationRuntime:
    """Resolved translation state for a worker run."""

    enabled: bool
    target_language: str


def build_live_quick_options_payload(
    *,
    mode: str,
    profile: str,
    output_mode: str,
    device_name: str,
    source_language_selection: str,
    target_language_selection: str,
) -> dict[str, Any]:
    """Build a SettingsWorker payload for LivePanel quick options."""
    m = RuntimeProfiles.normalize_live_ui_mode(mode or Config.live_ui_mode())
    p = RuntimeProfiles.normalize_live_profile(profile or Config.live_ui_profile())
    out_mode = RuntimeProfiles.normalize_live_output_mode(output_mode or Config.live_ui_output_mode())

    dev = str(device_name or "").strip()
    payload: dict[str, Any] = {
        "app": {
            "ui": {
                "live": {
                    "mode": m,
                    "profile": p,
                    "output_mode": out_mode,
                    "device_name": dev,
                },
            },
        },
    }

    source_sel = LanguagePolicy.normalize_panel_source_language_selection(source_language_selection)
    target_sel = LanguagePolicy.normalize_panel_target_language_selection(target_language_selection)
    tab_cfg = payload["app"]["ui"]["live"]
    if not LanguagePolicy.is_preferred(source_sel):
        tab_cfg["last_used_source_language"] = LanguagePolicy.normalize_last_used_source_language(source_sel)
    if not LanguagePolicy.is_preferred(target_sel):
        tab_cfg["last_used_target_language"] = LanguagePolicy.normalize_last_used_target_language(target_sel)
    return payload


def compute_translation_runtime(
    *,
    requested_enabled: bool,
    target_code: str,
    ui_language: str,
    tab_name: str = "live",
    supported: Iterable[str] | None = None,
) -> TranslationRuntime:
    """Compute final translation enablement and target language."""
    if not requested_enabled:
        return TranslationRuntime(False, "")
    tgt = resolve_target_language_for_run(
        tab_name,
        target_code,
        ui_language=ui_language,
        supported=supported,
    )
    return TranslationRuntime(bool(tgt), tgt)
