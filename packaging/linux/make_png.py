#!/usr/bin/env python3
"""Render engineering_console/icon.svg to a PNG with PySide6's own SVG renderer (no extra tools).
usage: make_png.py <icon.svg> <out.png> [size]"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

svg, out = sys.argv[1], sys.argv[2]
px = int(sys.argv[3]) if len(sys.argv) > 3 else 256
app = QGuiApplication([])
r = QSvgRenderer(svg)
if not r.isValid():
    sys.exit(f"cannot read {svg}")
img = QImage(px, px, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
r.render(p)
p.end()
if not img.save(out):
    sys.exit(f"cannot write {out}")
print("wrote", out)
