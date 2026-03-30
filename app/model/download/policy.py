# app/model/download/policy.py
from __future__ import annotations

from typing import Any

from app.model.core.config.policy import LanguagePolicy
from app.model.core.utils.string_utils import is_youtube_url


class DownloadPolicy:
    """Static download/media rules, formats and artifact contracts."""

    DOWNLOAD_PURPOSE_DOWNLOAD: str = "download"
    DOWNLOAD_PURPOSE_TRANSCRIPTION: str = "transcription"

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

    YOUTUBE_GENERIC_PROBE_CLIENTS: tuple[str, ...] = ("default",)
    YOUTUBE_ADVANCED_PROBE_CLIENTS: tuple[str, ...] = ("default", "ios", "tv_downgraded", "mweb")
    YOUTUBE_DOWNLOAD_CLIENTS: tuple[str, ...] = ("default",)

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
    def is_download_audio_auto_value(cls, value: Any) -> bool:
        token = str(value or "").strip().lower()
        allowed = {str(item or "").strip().lower() for item in cls.DOWNLOAD_AUDIO_LANG_AUTO_VALUES}
        return (not token) or token in allowed

    @classmethod
    def normalize_cookie_browser_mode(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        return token if token in cls.COOKIE_BROWSER_MODES else cls.COOKIE_BROWSER_MODES[0]

    @classmethod
    def normalize_cookie_browser_policy(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        return token if token in cls.COOKIE_BROWSER_POLICIES else cls.COOKIE_BROWSER_POLICIES[0]

    @classmethod
    def is_authenticated_cookie_mode(cls, value: Any) -> bool:
        return cls.normalize_cookie_browser_mode(value) in {"from_browser", "from_file"}

    @classmethod
    def is_advanced_probe_mode_for_url(cls, *, url: str, browser_cookies_mode: Any) -> bool:
        return is_youtube_url(url) and cls.is_authenticated_cookie_mode(browser_cookies_mode)

    @classmethod
    def probe_clients_for_url(cls, *, url: str, browser_cookies_mode: Any) -> tuple[str, ...]:
        if not is_youtube_url(url):
            return ("default",)
        if cls.is_authenticated_cookie_mode(browser_cookies_mode):
            return cls.youtube_advanced_probe_clients()
        return cls.youtube_generic_probe_clients()

    @classmethod
    def should_collect_probe_variants_for_url(cls, *, url: str, browser_cookies_mode: Any) -> bool:
        return cls.is_advanced_probe_mode_for_url(url=url, browser_cookies_mode=browser_cookies_mode)

    @classmethod
    def youtube_generic_probe_clients(cls) -> tuple[str, ...]:
        return cls.YOUTUBE_GENERIC_PROBE_CLIENTS

    @classmethod
    def youtube_advanced_probe_clients(cls) -> tuple[str, ...]:
        return cls.YOUTUBE_ADVANCED_PROBE_CLIENTS

    @classmethod
    def youtube_download_clients(cls) -> tuple[str, ...]:
        return cls.YOUTUBE_DOWNLOAD_CLIENTS

    @classmethod
    def is_youtube_download_client(cls, value: Any) -> bool:
        token = str(value or "").strip().lower()
        return token in cls.YOUTUBE_DOWNLOAD_CLIENTS

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
