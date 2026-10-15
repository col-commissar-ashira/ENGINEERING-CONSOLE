#!/usr/bin/env python3
"""Render engineering_console/icon.svg into a macOS .icns file (needs macOS: uses iconutil) with PySide6's own SVG
renderer, so no extra tools are required.   usage: make_icns.py <icon.svg> <out.icns>"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

svg, out = sys.argv[1], sys.argv[2]
app = QGuiApplication([])
renderer = QSvgRenderer(svg)
if not renderer.isValid():
    sys.exit(f"cannot read {svg}")
iconset = os.path.join(tempfile.mkdtemp(), "icon.iconset")
os.makedirs(iconset)
for base in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        px = base * scale
        img = QImage(px, px, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        renderer.render(p)
        p.end()
        img.save(os.path.join(iconset, f"icon_{base}x{base}" + ("@2x" if scale == 2 else "") + ".png"))
subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
print("wrote", out)
