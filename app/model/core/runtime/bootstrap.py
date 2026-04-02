# app/model/core/runtime/bootstrap.py
from __future__ import annotations

import sys
from pathlib import Path


def resolve_runtime_roots(module_file: str | Path, *, unfrozen_parent_index: int) -> tuple[Path, Path]:
    """Return the bundle and install roots for frozen and source runs."""

    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
        install_root = Path(sys.executable).resolve().parent
        return bundle_root, install_root

    root = Path(module_file).resolve().parents[int(unfrozen_parent_index)]
    return root, root


def build_startup_labels() -> dict[str, str]:
    """Build the localized startup-stage labels used by runtime warmup."""

    from app.model.core.runtime.localization import tr

    return {
        "asr": tr("loading.stage.transcription_model"),
        "translation": tr("loading.stage.translation_model"),
        "init": tr("loading.stage.init"),
        "dirs": tr("loading.stage.dirs"),
        "ffmpeg": tr("loading.stage.ffmpeg"),
    }
