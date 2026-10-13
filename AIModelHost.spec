# AIModelHost.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

PROJECT_ROOT = Path(SPECPATH).resolve()
data = [(str(PROJECT_ROOT / 'app/features/settings/defaults.json'), 'app/features/settings')]
for package in ('torch', 'transformers', 'accelerate', 'numpy', 'babel', 'sentencepiece', 'tokenizers', 'safetensors'):
  data += copy_metadata(package)
for package in ('transformers', 'tokenizers', 'sentencepiece', 'safetensors'):
  data += collect_data_files(package, include_py_files=False)
hidden = []
for package in (
  'transformers.models.auto',
  'transformers.models.whisper',
  'transformers.models.m2m_100',
  'transformers.generation',
  'sentencepiece',
  'tokenizers',
  'safetensors',
):
  hidden += collect_submodules(package)

analysis = Analysis(
  [str(PROJECT_ROOT / 'app/runtime/host_main.py')],
  pathex=[str(PROJECT_ROOT)],
  datas=data,
  binaries=[],
  hiddenimports=sorted(set(hidden + ['transformers.pipelines', 'transformers.models.auto.processing_auto'])),
  excludes=[
    'PyQt5',
    'PyQt6',
    'PySide6',
    'app.main',
    'app.bootstrap',
    'app.ui',
    'app.application',
    'yt_dlp',
    'pytest',
    '_pytest',
    'tests',
    'sympy.testing',
    'fsspec.conftest',
    'tensorflow',
    'jax',
    'torchaudio',
    'torchvision',
  ],
  noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
  archive,
  analysis.scripts,
  [],
  exclude_binaries=True,
  name='AIModelHost',
  debug=False,
  strip=False,
  upx=False,
  console=True,
)
COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name='AIModelHost')
