#!/usr/bin/env python3
"""Copy the license files that third-party packages ship in their metadata into <dest>/<package>/.
Used by the Windows/macOS bundle scripts so the LGPL/BSD notices of PySide6, shiboken6 and psutil travel with the app.
usage: collect_licenses.py <dest>"""
import importlib.metadata as md
import os
import shutil
import sys

dest = sys.argv[1]
for pkg in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "psutil"):
    try:
        dist = md.distribution(pkg)
    except md.PackageNotFoundError:
        continue
    n = 0
    for f in dist.files or []:
        name = os.path.basename(str(f)).lower()
        if any(k in name for k in ("licen", "copying", "notice")) and "/" in str(f).replace("\\", "/"):
            src = dist.locate_file(f)
            if os.path.isfile(src):
                out = os.path.join(dest, pkg)
                os.makedirs(out, exist_ok=True)
                shutil.copy2(src, os.path.join(out, os.path.basename(str(f))))
                n += 1
    print(f"{pkg}: {n} license file(s) copied" if n else f"{pkg}: no license file found in its metadata - add it by hand")
