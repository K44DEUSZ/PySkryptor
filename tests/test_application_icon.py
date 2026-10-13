import struct
from pathlib import Path

from PyQt6 import QtGui, QtSvg

from tools.build_icon import ICON_SIZES, build_icon

ROOT = Path(__file__).resolve().parents[1]


def test_windows_icon_frames_preserve_svg_proportions_and_transparency(qt_app, tmp_path):
  source = ROOT / 'assets/icons/app_icon_dark.svg'
  output = tmp_path / 'app.ico'
  build_icon(source, output)
  data = output.read_bytes()
  assert struct.unpack_from('<HHH', data) == (0, 1, len(ICON_SIZES))
  view_box = QtSvg.QSvgRenderer(str(source)).viewBoxF()
  for index, size in enumerate(ICON_SIZES):
    entry = struct.unpack_from('<BBBBHHII', data, 6 + 16 * index)
    image = QtGui.QImage.fromData(data[entry[7]:entry[7] + entry[6]], 'PNG')
    assert image.width() == size and image.height() == size and image.hasAlphaChannel()
    points = [(x, y) for x in range(size) for y in range(size) if image.pixelColor(x, y).alpha() > 128]
    width = max(x for x, _ in points) - min(x for x, _ in points) + 1
    height = max(y for _, y in points) - min(y for _, y in points) + 1
    assert abs(width / height - view_box.width() / view_box.height()) <= 0.12
    assert height >= size * 0.8
    assert image.pixelColor(0, 0).alpha() == 0
