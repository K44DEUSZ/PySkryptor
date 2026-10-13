"""Render the existing SVG mark into a Windows icon without stretching it."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtSvg

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_icon(source: Path, destination: Path) -> None:
  renderer = QtSvg.QSvgRenderer(str(source))
  if not renderer.isValid():
    raise ValueError(f'Invalid SVG: {source}')
  view_box = renderer.viewBoxF()
  images = []
  for size in ICON_SIZES:
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    extent = size * 0.92
    scale = min(extent / view_box.width(), extent / view_box.height())
    width, height = view_box.width() * scale, view_box.height() * scale
    renderer.render(painter, QtCore.QRectF((size - width) / 2, (size - height) / 2, width, height))
    painter.end()
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, 'PNG'):
      raise RuntimeError(f'Could not encode icon at {size}px')
    images.append(bytes(data))
  offset = 6 + 16 * len(images)
  entries = []
  for size, data in zip(ICON_SIZES, images):
    entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
    offset += len(data)
  destination.parent.mkdir(parents=True, exist_ok=True)
  destination.write_bytes(struct.pack('<HHH', 0, 1, len(images)) + b''.join(entries) + b''.join(images))


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--source', required=True, type=Path)
  parser.add_argument('--output', required=True, type=Path)
  args = parser.parse_args()
  build_icon(args.source, args.output)
