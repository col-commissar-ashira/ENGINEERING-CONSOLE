#!/usr/bin/env python3
"""Render engineering_console/icon.svg into a multi-size Windows .ico with PySide6's own SVG renderer (no extra tools).
usage: make_ico.py <icon.svg> <out.ico>"""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 24, 32, 48, 64, 128, 256)
svg, out = sys.argv[1], sys.argv[2]
app = QGuiApplication([])
renderer = QSvgRenderer(svg)
if not renderer.isValid():
    sys.exit(f"cannot read {svg}")

pngs = []
for px in SIZES:
    img = QImage(px, px, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        sys.exit(f"cannot encode {px}px PNG")
    pngs.append(bytes(buf.data()))

# ICO container with PNG-compressed images (supported since Windows Vista)
header = struct.pack("<HHH", 0, 1, len(pngs))
offset = 6 + 16 * len(pngs)
entries, blobs = b"", b""
for px, data in zip(SIZES, pngs):
    entries += struct.pack("<BBBBHHII", px % 256, px % 256, 0, 0, 1, 32, len(data), offset)   # 256 is stored as 0
    blobs += data
    offset += len(data)
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
with open(out, "wb") as f:
    f.write(header + entries + blobs)
print("wrote", out)
