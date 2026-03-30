# app/model/download/runtime.py
from __future__ import annotations

import os
import sys
from pathlib import Path

from app.model.download.policy import DownloadPolicy


def _browser_from_hint(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "brave" in text:
        return "brave"
    if "firefox" in text:
        return "firefox"
    if any(marker in text for marker in ("msedge", "microsoftedge", "microsoft-edge", "edgehtml")):
        return "edge"
    if "chrome" in text or "chromium" in text:
        return "chrome"
    return None


def _append_browser(ordered: list[str], seen: set[str], browser: str | None) -> None:
    normalized = str(browser or "").strip().lower()
    if not DownloadPolicy.is_supported_cookie_browser(normalized) or normalized in seen:
        return
    seen.add(normalized)
    ordered.append(normalized)


def _windows_url_association_progid(scheme: str) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    subkey = rf"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{scheme}\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        return ""
    return str(value or "").strip()


def _windows_progid_open_command(prog_id: str) -> str:
    if sys.platform != "win32" or not str(prog_id or "").strip():
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    subkey = rf"{str(prog_id).strip()}\shell\open\command"
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return ""
    return str(value or "").strip()


def detect_windows_default_browser() -> str | None:
    """Return the supported default browser configured for Windows URL associations."""
    if sys.platform != "win32":
        return None

    for scheme in ("https", "http"):
        prog_id = _windows_url_association_progid(scheme)
        browser = _browser_from_hint(prog_id)
        if DownloadPolicy.is_supported_cookie_browser(browser):
            return browser
        command = _windows_progid_open_command(prog_id)
        browser = _browser_from_hint(command)
        if DownloadPolicy.is_supported_cookie_browser(browser):
            return browser
    return None


def _windows_cookie_browser_roots() -> dict[str, tuple[Path, ...]]:
    local_app_data = Path(str(os.environ.get("LOCALAPPDATA") or "").strip())
    roaming_app_data = Path(str(os.environ.get("APPDATA") or "").strip())
    return {
        "chrome": (local_app_data / "Google" / "Chrome" / "User Data",),
        "edge": (local_app_data / "Microsoft" / "Edge" / "User Data",),
        "firefox": (roaming_app_data / "Mozilla" / "Firefox" / "Profiles",),
        "brave": (local_app_data / "BraveSoftware" / "Brave-Browser" / "User Data",),
    }


def _existing_paths(paths: list[Path]) -> tuple[Path, ...]:
    return tuple(path for path in paths if isinstance(path, Path) and path.exists())


def _chromium_cookie_store_paths(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = [
        root / "Default" / "Network" / "Cookies",
        root / "Default" / "Cookies",
    ]
    try:
        profile_dirs = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        profile_dirs = []
    for profile_dir in profile_dirs:
        name = profile_dir.name.lower()
        if name == "default" or name.startswith("profile ") or name.startswith("guest profile"):
            candidates.append(profile_dir / "Network" / "Cookies")
            candidates.append(profile_dir / "Cookies")
    return _existing_paths(candidates)


def _firefox_cookie_store_paths(profile_root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    try:
        profile_dirs = [path for path in profile_root.iterdir() if path.is_dir()]
    except OSError:
        profile_dirs = []
    for profile_dir in profile_dirs:
        candidates.append(profile_dir / "cookies.sqlite")
    return _existing_paths(candidates)


def detect_windows_installed_cookie_browsers() -> tuple[str, ...]:
    """Return supported cookie browsers detected from known Windows profile roots."""
    if sys.platform != "win32":
        return tuple()

    roots_by_browser = _windows_cookie_browser_roots()
    detected: list[str] = []
    for browser in DownloadPolicy.COOKIE_BROWSERS:
        if any(path.exists() for path in roots_by_browser.get(browser, ())):
            detected.append(browser)
    return tuple(detected)


def detect_windows_usable_cookie_browsers() -> tuple[str, ...]:
    """Return supported browsers with a concrete cookie store that yt_dlp can attempt to read."""
    if sys.platform != "win32":
        return tuple()

    roots_by_browser = _windows_cookie_browser_roots()
    detected: list[str] = []
    for browser in DownloadPolicy.COOKIE_BROWSERS:
        roots = roots_by_browser.get(browser, ())
        if browser == "firefox":
            has_store = any(_firefox_cookie_store_paths(root) for root in roots)
        else:
            has_store = any(_chromium_cookie_store_paths(root) for root in roots)
        if has_store:
            detected.append(browser)
    return tuple(detected)


def available_cookie_browsers() -> tuple[str, ...]:
    """Return cookie browsers with a concrete cookie store detected by the Windows runtime."""
    return detect_windows_usable_cookie_browsers()


def resolve_cookie_browser_candidates(policy_browser: str | None) -> tuple[str, ...]:
    """Resolve an ordered browser list for cookies-from-browser attempts."""
    normalized_policy = DownloadPolicy.normalize_cookie_browser_policy(policy_browser)
    usable = set(available_cookie_browsers())
    if DownloadPolicy.is_supported_cookie_browser(normalized_policy):
        return (normalized_policy,) if normalized_policy in usable else tuple()

    ordered: list[str] = []
    seen: set[str] = set()
    default_browser = detect_windows_default_browser()
    if default_browser in usable:
        _append_browser(ordered, seen, default_browser)

    for browser in DownloadPolicy.COOKIE_BROWSERS:
        if browser in usable:
            _append_browser(ordered, seen, browser)
    return tuple(ordered)


def resolve_effective_cookie_browser(policy_browser: str | None) -> str:
    """Resolve the primary browser identifier shown for browser-cookie mode."""
    candidates = resolve_cookie_browser_candidates(policy_browser)
    return candidates[0] if candidates else ""
