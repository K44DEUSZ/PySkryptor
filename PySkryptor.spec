# PySkryptor.spec
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


PROJECT_ROOT = Path(SPECPATH).resolve()
APP_NAME = "PySkryptor"
ENGINE_HOST_NAME = "AIModelHost"
ICON_PATH = PROJECT_ROOT / "assets" / "icons" / "favicon.ico"

GUI_ENTRYPOINT = PROJECT_ROOT / "app" / "main.py"
ENGINE_ENTRYPOINT = PROJECT_ROOT / "app" / "model" / "engines" / "host_main.py"

DEFAULTS_DATA = (str(PROJECT_ROOT / "app" / "model" / "settings" / "defaults.json"), "app/model/settings")
STYLE_DATA = (str(PROJECT_ROOT / "app" / "view" / "style.qss"), "app/view")
LICENSE_DATA = (str(PROJECT_ROOT / "LICENSE"), ".")
THIRD_PARTY_NOTICES_DATA = (str(PROJECT_ROOT / "THIRD_PARTY_NOTICES.txt"), ".")

GUI_METADATA_PACKAGES = (
    "torch",
    "yt_dlp",
    "numpy",
    "babel",
)

ENGINE_DATA_PACKAGES = (
    "transformers",
    "tokenizers",
    "sentencepiece",
    "safetensors",
    "charset_normalizer",
    "requests",
    "urllib3",
)

ENGINE_METADATA_PACKAGES = (
    "torch",
    "transformers",
    "numpy",
    "babel",
    "requests",
    "urllib3",
    "charset-normalizer",
    "sentencepiece",
    "tokenizers",
    "safetensors",
)

TEST_LIKE_EXCLUDES = [
    "_pytest",
    "fsspec.conftest",
    "pytest",
    "pytest_subtests",
    "sympy.testing",
    "sympy.testing.runtests",
    "sympy.testing.runtests_pytest",
    "tests",
]


def _metadata_entries(*package_names: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for package_name in package_names:
        entries.extend(copy_metadata(package_name))
    return entries


def _package_data_entries(*package_names: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for package_name in package_names:
        entries.extend(collect_data_files(package_name, include_py_files=False))
    return entries


def _filter_test_like_datas(entries):
    filtered = []
    for src, dest in entries:
        lower_src = str(src).lower()
        lower_dest = str(dest).lower()
        if "pytest-" in lower_src or "pytest-" in lower_dest:
            continue
        filtered.append((src, dest))
    return filtered


GUI_DATAS = _filter_test_like_datas(
    [
        DEFAULTS_DATA,
        STYLE_DATA,
        LICENSE_DATA,
        THIRD_PARTY_NOTICES_DATA,
    ]
    + _metadata_entries(*GUI_METADATA_PACKAGES)
)

ENGINE_DATAS = _filter_test_like_datas(
    [
        DEFAULTS_DATA,
    ]
    + _package_data_entries(*ENGINE_DATA_PACKAGES)
    + _metadata_entries(*ENGINE_METADATA_PACKAGES)
)

GUI_HIDDENIMPORTS = sorted(
    set(
        collect_submodules("yt_dlp.extractor")
        + collect_submodules("yt_dlp.downloader")
        + collect_submodules("yt_dlp.postprocessor")
        + [
            "babel",
            "numpy",
            "torch",
            "yt_dlp",
        ]
    )
)

ENGINE_HIDDENIMPORTS = sorted(
    set(
        collect_submodules("transformers.generation")
        + collect_submodules("transformers.models.auto")
        + collect_submodules("transformers.models.whisper")
        + collect_submodules("transformers.models.m2m_100")
        + collect_submodules("sentencepiece")
        + collect_submodules("tokenizers")
        + collect_submodules("safetensors")
        + [
            "numpy",
            "requests",
            "urllib3",
            "charset_normalizer",
            "torch",
            "transformers",
            "transformers.pipelines",
            "transformers.models.auto.processing_auto",
            "transformers.models.whisper",
            "transformers.models.m2m_100",
            "sentencepiece",
            "sentencepiece._sentencepiece",
            "tokenizers",
            "safetensors",
            "safetensors._safetensors_rust",
        ]
    )
)

ENGINE_BINARIES = (
    collect_dynamic_libs("torch")
    + collect_dynamic_libs("sentencepiece")
    + collect_dynamic_libs("tokenizers")
    + collect_dynamic_libs("safetensors")
)

gui_analysis = Analysis(
    [str(GUI_ENTRYPOINT)],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=GUI_DATAS,
    hiddenimports=GUI_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=TEST_LIKE_EXCLUDES
    + [
        "app.model.engines.host_main",
        "app.model.transcription.host_runtime",
        "app.model.translation.host_runtime",
        "transformers",
        "tokenizers",
        "sentencepiece",
        "safetensors",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

engine_analysis = Analysis(
    [str(ENGINE_ENTRYPOINT)],
    pathex=[str(PROJECT_ROOT)],
    binaries=ENGINE_BINARIES,
    datas=ENGINE_DATAS,
    hiddenimports=ENGINE_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=TEST_LIKE_EXCLUDES
    + [
        "PyQt5",
        "app.main",
        "app.view",
        "app.controller",
        "yt_dlp",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

gui_pyz = PYZ(gui_analysis.pure)
engine_pyz = PYZ(engine_analysis.pure)

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)

engine_exe = EXE(
    engine_pyz,
    engine_analysis.scripts,
    [],
    exclude_binaries=True,
    name=ENGINE_HOST_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    gui_exe,
    engine_exe,
    gui_analysis.binaries,
    gui_analysis.zipfiles,
    gui_analysis.datas,
    engine_analysis.binaries,
    engine_analysis.zipfiles,
    engine_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
