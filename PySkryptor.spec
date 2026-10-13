# PySkryptor.spec
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

PROJECT_ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(PROJECT_ROOT))
from tools.build_bundle import validate_binary_entries

ICON_PATH = PROJECT_ROOT / 'assets/icons/favicon.ico'
metadata = []
for package in ('PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'yt-dlp', 'yt-dlp-ejs', 'deno', 'numpy', 'babel'):
  metadata += copy_metadata(package)
metadata += collect_data_files('yt_dlp_ejs')

analysis = Analysis(
  [str(PROJECT_ROOT / 'app/main.py')],
  pathex=[str(PROJECT_ROOT)],
  datas=[
    (str(PROJECT_ROOT / 'app/features/settings/defaults.json'), 'app/features/settings'),
    (str(PROJECT_ROOT / 'app/ui/style.qss'), 'app/ui'),
  ]
  + metadata,
  binaries=[],
  hiddenimports=(
    collect_submodules('yt_dlp.extractor')
    + collect_submodules('yt_dlp.downloader')
    + collect_submodules('yt_dlp.postprocessor')
  ),
  excludes=[
    'torch',
    'transformers',
    'tokenizers',
    'sentencepiece',
    'safetensors',
    'accelerate',
    'app.runtime.host_main',
    'app.features.transcription.host_runtime',
    'app.features.translation.host_runtime',
    'PyQt5',
    'PySide6',
    'pytest',
    '_pytest',
    'tests',
  ],
  noarchive=False,
)
allowed_binary_roots = [Path(sys.prefix), Path(sys.base_prefix)]
if sys.platform == 'win32':
  allowed_binary_roots.append(Path(os.environ['SystemRoot']))
validate_binary_entries(analysis.binaries, allowed_binary_roots)
archive = PYZ(analysis.pure)
executable = EXE(
  archive,
  analysis.scripts,
  [],
  exclude_binaries=True,
  name='PySkryptor',
  debug=False,
  strip=False,
  upx=False,
  console=False,
  icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)
COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name='PySkryptor')
