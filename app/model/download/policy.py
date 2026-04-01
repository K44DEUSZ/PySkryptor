# app/model/download/policy.py
from __future__ import annotations

from typing import Any

from app.model.core.config.policy import LanguagePolicy
from app.model.core.utils.string_utils import is_youtube_url

class DownloadPolicy:
    """Static download/media rules, formats and artifact contracts."""

    DOWNLOAD_PURPOSE_DOWNLOAD: str = "download"
    DOWNLOAD_PURPOSE_TRANSCRIPTION: str = "transcription"

    DOWNLOAD_OPERATION_PLAYLIST: str = "playlist"
    DOWNLOAD_OPERATION_PROBE: str = "probe"
    DOWNLOAD_OPERATION_DOWNLOAD: str = "download"

    DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT: str = "strict_final_ext"
    DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT: str = "work_input"

    DOWNLOAD_DEFAULT_PURPOSE: str = DOWNLOAD_PURPOSE_DOWNLOAD
    DOWNLOAD_DEFAULT_STEM: str = "download"

    DOWNLOAD_AUDIO_DEFAULT_TOKEN: str = "default"
    DOWNLOAD_AUDIO_LANG_AUTO_VALUES: tuple[str, ...] = (DOWNLOAD_AUDIO_DEFAULT_TOKEN, LanguagePolicy.AUTO, "-")

    DOWNLOAD_FALLBACK_AUDIO_SELECTOR: str = "bestaudio/best"
    DOWNLOAD_FALLBACK_VIDEO_SELECTOR: str = "bv*+ba/b"
    URL_DOWNLOAD_DEFAULT_QUALITY: str = "best"
    COOKIE_BROWSER_MODES: tuple[str, ...] = ("none", "from_browser", "from_file")
    COOKIE_BROWSER_AUTO: str = LanguagePolicy.AUTO
    COOKIE_BROWSERS: tuple[str, ...] = ("chrome", "edge", "firefox", "brave")
    COOKIE_BROWSER_POLICIES: tuple[str, ...] = (COOKIE_BROWSER_AUTO, *COOKIE_BROWSERS)

    EXTRACTOR_KEY_GENERIC: str = "generic"
    EXTRACTOR_KEY_YOUTUBE: str = "youtube"
    EXTRACTOR_PROVIDER_STATE_NONE: str = "none"
    EXTRACTOR_PROVIDER_STATE_AVAILABLE: str = "available"
    EXTRACTOR_PROVIDER_STATE_MISSING: str = "missing"
    EXTRACTOR_PROVIDER_STATE_UNAVAILABLE: str = "unavailable"

    EXTRACTOR_ACCESS_MODE_BASIC: str = "basic"
    EXTRACTOR_ACCESS_MODE_ENHANCED: str = "enhanced"
    EXTRACTOR_ACCESS_MODE_DEGRADED: str = "degraded"
    EXTRACTOR_ACCESS_MODE_UNAVAILABLE: str = "unavailable"

    EXTRACTOR_ACCESS_STATE_BASIC_OK: str = "basic_ok"
    EXTRACTOR_ACCESS_STATE_BASIC_LIMITED: str = "basic_limited"
    EXTRACTOR_ACCESS_STATE_ENHANCED_ACTIVE: str = "enhanced_active"
    EXTRACTOR_ACCESS_STATE_ENHANCED_RECOMMENDED: str = "enhanced_recommended"
    EXTRACTOR_ACCESS_STATE_ENHANCED_REQUIRED: str = "enhanced_required"
    EXTRACTOR_ACCESS_STATE_PROVIDER_MISSING: str = "provider_missing"
    EXTRACTOR_ACCESS_STATE_DEGRADED: str = "degraded"
    EXTRACTOR_ACCESS_STATE_UNAVAILABLE: str = "unavailable"

    EXTRACTOR_ACCESS_ACTION_NONE: str = "none"
    EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC: str = "continue_basic"
    EXTRACTOR_ACCESS_ACTION_LIMITED_FORMATS: str = "limited_formats"
    EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED: str = "retry_enhanced"
    EXTRACTOR_ACCESS_ACTION_CONTINUE_DEGRADED: str = "continue_degraded"
    EXTRACTOR_ACCESS_ACTION_INSTALL_PROVIDER: str = "install_provider"

    EXTRACTOR_ACCESS_SCOPE_GENERIC: str = "generic"
    EXTRACTOR_ACCESS_SCOPE_GVS: str = "gvs"
    EXTRACTOR_ACCESS_SCOPE_PLAYER: str = "player"
    EXTRACTOR_ACCESS_SCOPE_SUBS: str = "subs"
    EXTRACTOR_ACCESS_SCOPE_VISITOR_DATA: str = "visitor_data"
    EXTRACTOR_ACCESS_SCOPE_PO_TOKEN: str = "po_token"
    EXTRACTOR_ACCESS_SCOPE_SABR: str = "sabr"

    YOUTUBE_BASIC_PROBE_CLIENTS: tuple[str, ...] = ("default",)
    YOUTUBE_ENHANCED_PROBE_CLIENTS: tuple[str, ...] = ("mweb", "default", "ios", "tv_downgraded")
    YOUTUBE_ENHANCED_CLIENT: str = "mweb"

    DOWNLOAD_UI_DEFAULT_QUALITY: str = "auto"

    FILES_AUDIO_INPUT_EXTENSIONS: tuple[str, ...] = ("wav", "mp3", "flac", "m4a", "ogg", "aac")
    FILES_VIDEO_INPUT_EXTENSIONS: tuple[str, ...] = ("mp4", "webm", "mkv", "mov", "avi")

    DOWNLOAD_AUDIO_FORMAT_PROFILES: dict[str, dict[str, Any]] = {
        "wav": {"selector_exts": ("wav",), "postprocess": "extract_audio", "preferredcodec": "wav"},
        "mp3": {"selector_exts": ("mp3",), "postprocess": "extract_audio", "preferredcodec": "mp3"},
        "flac": {"selector_exts": ("flac",), "postprocess": "extract_audio", "preferredcodec": "flac"},
        "m4a": {"selector_exts": ("m4a", "mp4"), "postprocess": "extract_audio", "preferredcodec": "m4a"},
        "ogg": {"selector_exts": ("ogg", "opus", "webm"), "postprocess": "extract_audio", "preferredcodec": "ogg"},
        "aac": {"selector_exts": ("aac", "m4a", "mp4"), "postprocess": "extract_audio", "preferredcodec": "aac"},
    }
    DOWNLOAD_VIDEO_FORMAT_PROFILES: dict[str, dict[str, Any]] = {
        "mp4": {
            "video_exts": ("mp4",),
            "audio_exts": ("m4a", "mp4", "aac"),
            "strategy": "native_or_merge_or_convert",
            "strict_final_ext": True,
        },
        "webm": {
            "video_exts": ("webm",),
            "audio_exts": ("webm", "opus"),
            "strategy": "native_or_merge_or_convert",
            "strict_final_ext": True,
        },
        "mkv": {
            "video_exts": tuple(),
            "audio_exts": tuple(),
            "strategy": "remux",
            "strict_final_ext": True,
        },
        "mov": {
            "video_exts": ("mov",),
            "audio_exts": ("m4a", "mp4", "aac"),
            "strategy": "native_or_merge_or_convert",
            "strict_final_ext": True,
        },
        "avi": {
            "video_exts": tuple(),
            "audio_exts": tuple(),
            "strategy": "convert",
            "strict_final_ext": True,
        },
    }

    DOWNLOAD_AUDIO_OUTPUT_EXTENSIONS: tuple[str, ...] = tuple(DOWNLOAD_AUDIO_FORMAT_PROFILES.keys())
    DOWNLOAD_VIDEO_OUTPUT_EXTENSIONS: tuple[str, ...] = tuple(DOWNLOAD_VIDEO_FORMAT_PROFILES.keys())

    @classmethod
    def download_audio_format_profile(cls, ext: str) -> dict[str, Any]:
        return dict(cls.DOWNLOAD_AUDIO_FORMAT_PROFILES.get(str(ext or "").strip().lower(), {}))

    @classmethod
    def download_video_format_profile(cls, ext: str) -> dict[str, Any]:
        return dict(cls.DOWNLOAD_VIDEO_FORMAT_PROFILES.get(str(ext or "").strip().lower(), {}))

    @staticmethod
    def _normalize_extensions(raw_extensions: Any) -> tuple[str, ...]:
        return tuple(
            str(ext or "").strip().lower().lstrip(".")
            for ext in tuple(raw_extensions or ())
            if str(ext or "").strip()
        )

    @classmethod
    def download_audio_selector_extensions(cls, ext: str) -> tuple[str, ...]:
        profile = cls.download_audio_format_profile(ext)
        return cls._normalize_extensions(profile.get("selector_exts"))

    @classmethod
    def download_video_target_extensions(cls, ext: str) -> tuple[str, ...]:
        profile = cls.download_video_format_profile(ext)
        return cls._normalize_extensions(profile.get("video_exts"))

    @classmethod
    def download_video_audio_extensions(cls, ext: str) -> tuple[str, ...]:
        profile = cls.download_video_format_profile(ext)
        return cls._normalize_extensions(profile.get("audio_exts"))

    @classmethod
    def resolve_download_contract(
        cls,
        *,
        kind: str,
        purpose: str,
        keep_output: bool,
        ext: str,
    ) -> dict[str, Any]:
        kind_l = str(kind or "").strip().lower()
        purpose_l = LanguagePolicy.normalize_policy_value(purpose) or cls.DOWNLOAD_DEFAULT_PURPOSE
        ext_l = str(ext or "").strip().lower().lstrip(".")

        if kind_l == "audio":
            strict_final_ext = bool(ext_l)
        else:
            strict_final_ext = bool(cls.download_video_format_profile(ext_l).get("strict_final_ext"))

        artifact_policy = cls.DOWNLOAD_ARTIFACT_POLICY_STRICT_FINAL_EXT
        final_ext = ext_l if strict_final_ext else ""

        if purpose_l == cls.DOWNLOAD_PURPOSE_TRANSCRIPTION and not bool(keep_output):
            artifact_policy = cls.DOWNLOAD_ARTIFACT_POLICY_WORK_INPUT
            final_ext = ""

        return {
            "plan_ext": ext_l,
            "final_ext": final_ext,
            "artifact_policy": artifact_policy,
            "strict_final_ext": bool(final_ext),
        }

    @classmethod
    def files_audio_input_file_exts(cls) -> tuple[str, ...]:
        return tuple(f".{x}" for x in cls.FILES_AUDIO_INPUT_EXTENSIONS)

    @classmethod
    def files_video_input_file_exts(cls) -> tuple[str, ...]:
        return tuple(f".{x}" for x in cls.FILES_VIDEO_INPUT_EXTENSIONS)

    @classmethod
    def files_media_input_file_exts(cls) -> tuple[str, ...]:
        extensions = {ext.lower() for ext in cls.files_audio_input_file_exts()}
        extensions |= {ext.lower() for ext in cls.files_video_input_file_exts()}
        return tuple(sorted(extensions))

    @classmethod

    def normalize_cookie_browser_mode(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        return token if token in cls.COOKIE_BROWSER_MODES else cls.COOKIE_BROWSER_MODES[0]

    @classmethod
    def normalize_cookie_browser_policy(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        return token if token in cls.COOKIE_BROWSER_POLICIES else cls.COOKIE_BROWSER_POLICIES[0]

    @classmethod

    def normalize_download_operation(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token in {
            cls.DOWNLOAD_OPERATION_PLAYLIST,
            cls.DOWNLOAD_OPERATION_PROBE,
            cls.DOWNLOAD_OPERATION_DOWNLOAD,
        }:
            return token
        return cls.DOWNLOAD_OPERATION_DOWNLOAD

    @classmethod
    def normalize_extractor_key(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return cls.EXTRACTOR_KEY_GENERIC
        if token == cls.EXTRACTOR_KEY_YOUTUBE or "youtube" in token or token == "youtu" or token == "youtube_tab":
            return cls.EXTRACTOR_KEY_YOUTUBE
        return cls.EXTRACTOR_KEY_GENERIC

    @classmethod
    def normalize_extractor_access_mode(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token in {
            cls.EXTRACTOR_ACCESS_MODE_BASIC,
            cls.EXTRACTOR_ACCESS_MODE_ENHANCED,
            cls.EXTRACTOR_ACCESS_MODE_DEGRADED,
            cls.EXTRACTOR_ACCESS_MODE_UNAVAILABLE,
        }:
            return token
        return cls.EXTRACTOR_ACCESS_MODE_BASIC

    @classmethod
    def extractor_key_for_url(cls, url: str | None) -> str:
        return cls.EXTRACTOR_KEY_YOUTUBE if is_youtube_url(url) else cls.EXTRACTOR_KEY_GENERIC

    @classmethod

    def youtube_enhanced_client(cls) -> str:
        return cls.YOUTUBE_ENHANCED_CLIENT

    @classmethod
    def youtube_basic_probe_clients(cls) -> tuple[str, ...]:
        return cls.YOUTUBE_BASIC_PROBE_CLIENTS

    @classmethod
    def youtube_enhanced_probe_clients(cls) -> tuple[str, ...]:
        return cls.YOUTUBE_ENHANCED_PROBE_CLIENTS

    @classmethod
    def normalize_provider_state(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token in {
            cls.EXTRACTOR_PROVIDER_STATE_NONE,
            cls.EXTRACTOR_PROVIDER_STATE_AVAILABLE,
            cls.EXTRACTOR_PROVIDER_STATE_MISSING,
            cls.EXTRACTOR_PROVIDER_STATE_UNAVAILABLE,
        }:
            return token
        return cls.EXTRACTOR_PROVIDER_STATE_NONE

    @classmethod
    def normalize_extractor_access_scope(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token in {
            cls.EXTRACTOR_ACCESS_SCOPE_GENERIC,
            cls.EXTRACTOR_ACCESS_SCOPE_GVS,
            cls.EXTRACTOR_ACCESS_SCOPE_PLAYER,
            cls.EXTRACTOR_ACCESS_SCOPE_SUBS,
            cls.EXTRACTOR_ACCESS_SCOPE_VISITOR_DATA,
            cls.EXTRACTOR_ACCESS_SCOPE_PO_TOKEN,
            cls.EXTRACTOR_ACCESS_SCOPE_SABR,
        }:
            return token
        return cls.EXTRACTOR_ACCESS_SCOPE_GENERIC

    @classmethod
    def extractor_access_unavailable_states(cls) -> tuple[str, ...]:
        return (
            cls.EXTRACTOR_ACCESS_STATE_PROVIDER_MISSING,
            cls.EXTRACTOR_ACCESS_STATE_ENHANCED_REQUIRED,
            cls.EXTRACTOR_ACCESS_STATE_UNAVAILABLE,
        )

    @classmethod
    def extractor_access_limited_states(cls) -> tuple[str, ...]:
        return (
            cls.EXTRACTOR_ACCESS_STATE_BASIC_LIMITED,
            cls.EXTRACTOR_ACCESS_STATE_ENHANCED_RECOMMENDED,
            cls.EXTRACTOR_ACCESS_STATE_DEGRADED,
        )

    @classmethod
    def extractor_access_limited_actions(cls) -> tuple[str, ...]:
        return (
            cls.EXTRACTOR_ACCESS_ACTION_LIMITED_FORMATS,
            cls.EXTRACTOR_ACCESS_ACTION_RETRY_ENHANCED,
            cls.EXTRACTOR_ACCESS_ACTION_CONTINUE_BASIC,
            cls.EXTRACTOR_ACCESS_ACTION_CONTINUE_DEGRADED,
        )

    @classmethod
    def is_limited_extractor_access_decision(cls, state: Any, action: Any = None) -> bool:
        normalized_state = str(state or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        return (
            normalized_state in cls.extractor_access_limited_states()
            or normalized_action in cls.extractor_access_limited_actions()
        )

    @classmethod
    def is_unavailable_extractor_access_state(cls, state: Any) -> bool:
        normalized_state = str(state or "").strip().lower()
        return normalized_state in cls.extractor_access_unavailable_states()

    @classmethod
    def is_supported_cookie_browser(cls, value: Any) -> bool:
        token = str(value or "").strip().lower()
        return token in cls.COOKIE_BROWSERS

    @classmethod
    def download_ui_default_quality(cls) -> str:
        value = str(cls.DOWNLOAD_UI_DEFAULT_QUALITY or "").strip().lower()
        return value or "auto"

    @classmethod
    def download_default_video_ext(cls) -> str:
        extensions = tuple(
            str(ext or "").strip().lower().lstrip(".") for ext in cls.DOWNLOAD_VIDEO_OUTPUT_EXTENSIONS
        )
        for ext in extensions:
            if ext:
                return ext
        return "mp4"
