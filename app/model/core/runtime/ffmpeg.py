# app/model/core/runtime/ffmpeg.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _resolve_ffmpeg_bin_dir(config_cls: Any) -> Path:
    base_dir = Path(config_cls.PATHS.FFMPEG_DIR)
    bin_dir = base_dir / "bin"
    if bin_dir.exists():
        return bin_dir

    configured = config_cls.PATHS.FFMPEG_BIN_DIR
    if configured not in (None, ""):
        configured_path = Path(configured)
        if configured_path.exists():
            return configured_path

    return base_dir


def resolve_ffmpeg_tool(config_cls: Any, name: str) -> str:
    """Return the configured FFmpeg tool path or fall back to PATH lookup."""
    tool = str(name or "").strip()
    if not tool:
        return ""

    exe = f"{tool}.exe"
    candidate = _resolve_ffmpeg_bin_dir(config_cls) / exe
    return str(candidate) if candidate.exists() else exe


def setup_ffmpeg_runtime(config_cls: Any) -> None:
    """Expose FFmpeg binaries from the configured runtime paths."""
    bin_dir = _resolve_ffmpeg_bin_dir(config_cls)
    config_cls.PATHS.FFMPEG_BIN_DIR = bin_dir

    bin_dir_str = str(bin_dir)
    env_path = os.environ.get("PATH", "")
    if bin_dir_str not in env_path.split(os.pathsep):
        os.environ["PATH"] = bin_dir_str + os.pathsep + env_path

    os.environ.setdefault("FFMPEG_LOCATION", bin_dir_str)

    ffmpeg_exe = Path(resolve_ffmpeg_tool(config_cls, "ffmpeg"))
    ffprobe_exe = Path(resolve_ffmpeg_tool(config_cls, "ffprobe"))
    if ffmpeg_exe.exists():
        os.environ.setdefault("FFMPEG_BINARY", str(ffmpeg_exe))
        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", str(ffmpeg_exe))
    if ffprobe_exe.exists():
        os.environ.setdefault("FFPROBE_BINARY", str(ffprobe_exe))
