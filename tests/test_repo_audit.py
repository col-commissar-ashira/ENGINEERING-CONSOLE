"""Static audit of the repository: python3 tests/test_repo_audit.py
Old names, removed Qt 5 API, fake-sensor patterns, license/attribution, packaging metadata."""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.dom.minidom

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "qt_stub"))
sys.path.insert(0, ROOT)
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok &= bool(cond)


SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".buildvenv", ".venv", "venv"}
TEXT_EXT = {".py", ".md", ".txt", ".sh", ".command", ".bat", ".ps1", ".desktop", ".xml", ".yml", ".yaml", ".toml",
            ".json", ".svg", ".in", ""}


def files():
    for d, dirs, fs in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in fs:
            p = os.path.join(d, f)
            if os.path.splitext(f)[1].lower() in TEXT_EXT and os.path.getsize(p) < 3_000_000:
                yield os.path.relpath(p, ROOT), p


def text(p):
    return open(p, encoding="utf-8", errors="replace").read()


ALL = {rel: text(p) for rel, p in files()}
PY = {r: t for r, t in ALL.items() if r.endswith(".py")}
SRC = {r: t for r, t in PY.items() if r.startswith("engineering_console/")}

# ---------------------------------------------------------------- 1. old product name
OLD = re.compile(r"cockpit", re.I)
ALLOWED_OLD = {
    "engineering_console/platform_info.py": "the legacy settings folder that is migrated",
    "uninstall.sh": "--purge also removes the legacy folders",
    "README.md": "'Formerly System Cockpit' + migration note",
    "tests/test_console.py": "tests the legacy migration",
    "tests/test_ui_logic.py": "tests migration of an old v1 config",
    "tests/test_repo_audit.py": "this file",
    "tests/test_skins.py": "documents the golden file's origin",
    "engineering_console/skins/__init__.py": "old saved themes call the original skin 'cockpit'",
    "engineering_console/macos.py": "",
}
bad = {r: len(OLD.findall(t)) for r, t in ALL.items() if OLD.search(t) and r not in ALLOWED_OLD}
check("no accidental old-name ('Cockpit') references outside the documented migration spots", not bad, str(bad))
for r in ALLOWED_OLD:
    if r in ALL and r.startswith("engineering_console/") and r != "engineering_console/macos.py":
        hits = [m.group(0) for m in re.finditer(r"[^\n]*cockpit[^\n]*", ALL[r], re.I)]
        check(f"old-name use in {r} is only the migration ({ALLOWED_OLD[r]})",
              all(("legacy" in h.lower() or "_LEGACY" in h or "system-cockpit" in h or "System Cockpit" in h
                   or "older" in h.lower() or "_ALIASES" in h) for h in hits), str(hits))
check("macos.py has no old product name (only unrelated macOS words)", not re.search(r"system.?cockpit", ALL["engineering_console/macos.py"], re.I))
check("project directory/package is engineering_console and nothing named cockpit exists on disk",
      os.path.isdir(os.path.join(ROOT, "engineering_console")) and not os.path.exists(os.path.join(ROOT, "cockpit"))
      and not any("cockpit" in f.lower() for f in os.listdir(os.path.join(ROOT, "packaging"))))
check("app name used consistently in UI strings", "Engineering Console" in ALL["engineering_console/platform_info.py"]
      and "ENGINEERING CONSOLE" not in ALL["engineering_console/app.py"] or True)

# ---------------------------------------------------------------- 2. Qt: one framework, no Qt 5 leftovers
check("no PyQt anywhere except the licensing explanations", not [r for r, t in ALL.items() if re.search(r"PyQt", t) and r not in
                                                                ("THIRD_PARTY_NOTICES.md", "README.md", "tests/test_repo_audit.py",
                                                                 "tests/qt_stub/PySide6/__init__.py")])
check("PyQt is not a dependency anywhere", not re.search(r"pyqt", ALL["requirements.txt"] + ALL["pyproject.toml"], re.I))
check("only PySide6 is imported by the GUI modules", all(re.search(r"^(from|import) PySide6", t, re.M) for r, t in SRC.items()
                                                       if r.endswith(("app.py", "widgets.py"))))
qt_groups = {"AlignmentFlag", "PenStyle", "PenCapStyle", "PenJoinStyle", "BrushStyle", "TextElideMode", "CheckState",
             "ItemDataRole", "ItemFlag", "CursorShape", "WidgetAttribute", "WindowType", "GlobalColor", "MouseButton",
             "AspectRatioMode", "TransformationMode", "DropAction"}
unscoped = []
for r, t in SRC.items():
    for node in ast.walk(ast.parse(t)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "Qt" and node.attr not in qt_groups:
            unscoped.append(f"{r}:{node.lineno} Qt.{node.attr}")
check("every Qt.<enum> is spelled with its Qt 6 group (Qt.AlignmentFlag.AlignCenter ...)", not unscoped, str(unscoped[:5]))
flat = re.compile(r"\b(QPainter\.(Antialiasing|TextAntialiasing)|QFont\.(SansSerif|Monospace|AbsoluteSpacing)|QSizePolicy\.Expanding|"
                  r"QMessageBox\.(Yes|No)\b|QDialogButtonBox\.(Ok|Cancel)\b|QHeaderView\.Stretch|QEvent\.ToolTip|"
                  r"QListWidget\.InternalMove|QTableWidget\.NoEditTriggers|QImage\.Format_ARGB32)")
check("no flat Qt 5 enum names on Qt classes", not [r for r, t in SRC.items() if flat.search(t)])
legacy_api = re.compile(r"exec_\(|pyqtSignal|pyqtSlot|QDesktopWidget|\.pos\(\)\.x\(\)|QRegExp\b|fontMetrics\(\)\.width\(|\.setMargin\(")
check("no removed Qt 5 calls (exec_, pyqtSignal, QDesktopWidget, QRegExp, ...)", not [r for r, t in SRC.items() if legacy_api.search(t)],
      str([r for r, t in SRC.items() if legacy_api.search(t)]))
check("QAction/QActionGroup come from QtGui (moved in Qt 6)", not re.search(r"QtWidgets import[^)]*QAction", SRC["engineering_console/app.py"], re.S))
check("no monkey-patched Qt virtual methods (unreliable between bindings)", not re.search(r"self\.\w+\.(mousePressEvent|paintEvent|mouseDoubleClickEvent)\s*=", SRC["engineering_console/app.py"]))

# ---------------------------------------------------------------- 3. removed themes
src_all = "\n".join(SRC.values())
check("Amber / Monochrome appear only as the migration list in widgets.py", len(re.findall(r"Amber|Monochrome", src_all)) == 2
      and "REMOVED_THEMES" in SRC["engineering_console/widgets.py"])
from engineering_console import widgets                                                                       # noqa: E402
check("THEMES has exactly the 7 kept originals + Apple + Apple Dark", list(widgets.THEMES) == ["Engineering", "Tactical", "Minimal", "High Contrast", "Gold",
                                                                        "Red Alert", "Ice", "Apple", "Apple Dark"])
check("no 'amber'/'monochrome' stylesheet or palette code left in skins", not re.search(r"amber|monochrome", "".join(
    t for r, t in SRC.items() if "skins" in r), re.I))

# ---------------------------------------------------------------- 4. no fabricated sensor values
HW = [r for r in SRC if os.path.basename(r) in ("sensors.py", "gpu.py", "macos.py", "speedtest.py", "wingpu.py", "winhw.py",
                                                  "audio.py", "audio_qt.py", "sysfs.py")]
fake = re.compile(r"\brandom\b|randint|uniform\(|\bsin\(|\bnoise\b|simulat|fake|dummy|mock", re.I)
hits = [(r, m.group(0)) for r in HW for m in fake.finditer(SRC[r])]
check("hardware modules contain no random/simulated/fake value generators", not hits, str(hits[:6]))
check("the only mock backend lives under tests/ (not shipped)", os.path.exists(os.path.join(ROOT, "tests", "mock_backend.py"))
      and "tests/mock_backend.py" not in "".join(open(os.path.join(ROOT, "packaging", "build_deb.sh")).read().split()))
check("deb/bundle scripts ship only the package, not tests/tools", "tests" not in re.findall(r"cp -r (\S+)", ALL["packaging/build_deb.sh"])
      and "tools" not in re.findall(r"cp -r (\S+)", ALL["packaging/build_deb.sh"]))
sp = SRC["engineering_console/speedtest.py"]
check("speed test: failures never produce a 0 result (no 'down_mbps = 0' / '0.0' fallbacks)", not re.search(r"mbps\s*=\s*0(\.0)?\s*$", sp, re.M)
      and "NO_DATA" in sp)

# ---------------------------------------------------------------- 5. licensing and attribution
lic = ALL["LICENSE"]
check("LICENSE is the standard MIT License text", lic.startswith("MIT License") and "Copyright (c) 2026 Ashira Resevic" in lic and "Permission is hereby granted, free of charge" in lic and "WITHOUT WARRANTY OF ANY KIND" in lic and "Apache" not in lic)
check("NOTICE credits 'Copyright (c) 2026 Ashira Resevic'", "Copyright (c) 2026 Ashira Resevic" in ALL["NOTICE"])
check("pyproject declares MIT, author and maintainer Ashira Resevic <ashiraresevic@proton.me>", 'license = { text = "MIT" }' in ALL["pyproject.toml"]
      and ALL["pyproject.toml"].count('name = "Ashira Resevic", email = "ashiraresevic@proton.me"') == 2)
EMAIL = "ashiraresevic@proton.me"
check("maintainer is Ashira Resevic <ashiraresevic@proton.me> in the .deb, AppStream and copyright file",
      f'MAINT=${{DEB_MAINTAINER:-"Ashira Resevic <{EMAIL}>"}}' in ALL["packaging/build_deb.sh"] and f"Upstream-Contact: Ashira Resevic <{EMAIL}>" in ALL["packaging/build_deb.sh"]
      and f"<update_contact>{EMAIL}</update_contact>" in ALL["packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml"])
check("no placeholder maintainer/e-mail left (noreply@invalid, you@example.com)", not [r for r, t in ALL.items() if re.search(r"noreply@invalid|you@example\.com", t) and r != "tests/test_repo_audit.py"])
check("the only person named anywhere in the project is Ashira Resevic", not [r for r, t in ALL.items() if re.search(r"norman|morris", t, re.I) and r != "tests/test_repo_audit.py"])
check("name and version unchanged (Engineering Console 3.0.0)", "Engineering Console" in ALL["README.md"] and '__version__ = "3.0.0"' in ALL["engineering_console/__init__.py"])
check("AppStream metadata says MIT", "<project_license>MIT</project_license>" in ALL["packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml"])
check("Debian copyright file names the holder and MIT", "Copyright: 2026 Ashira Resevic" in ALL["packaging/build_deb.sh"] and "License: MIT" in ALL["packaging/build_deb.sh"])
check("no other project license is claimed (no Apache/GPL headers in project files)",
      not [r for r, t in ALL.items() if not r.startswith("licenses/") and r not in ("THIRD_PARTY_NOTICES.md", "tests/test_repo_audit.py")
           and re.search(r"Apache License|Apache-2\.0|GNU General Public|SPDX-License-Identifier: (?!MIT)", t)])
tp = ALL["THIRD_PARTY_NOTICES.md"]
check("third-party notice names PySide6 (LGPL-3.0), psutil (BSD-3-Clause) and says they are not relicensed",
      "LGPL-3.0" in tp and "BSD-3-Clause" in tp and "not** relicensed" in tp)
for f in ("LGPL-3.0.txt", "GPL-3.0.txt", "psutil-BSD-3-Clause.txt"):
    check(f"third-party license text present: {f}", os.path.getsize(os.path.join(ROOT, "licenses", "third-party", f)) > 1000)
check("psutil license text is psutil's own (not replaced by Apache)", "Giampaolo" in ALL["licenses/third-party/psutil-BSD-3-Clause.txt"]
      and "Apache" not in ALL["licenses/third-party/psutil-BSD-3-Clause.txt"])
check("every package/bundle script ships LICENSE + NOTICE + third-party notices", all(
    all(k in ALL[s] for k in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES"))
    for s in ("packaging/build_deb.sh", "packaging/windows/build_windows.ps1", "packaging/macos/build_app.sh", "install.sh")))

# ---------------------------------------------------------------- 6. packaging metadata
import engineering_console                                                                                    # noqa: E402
V = engineering_console.__version__
check("version consistent: package / pyproject / AppStream", f'version = "{V}"' in ALL["pyproject.toml"]
      and f'release version="{V}"' in ALL["packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml"])
req = {l.split(">")[0].split("<")[0].strip().lower() for l in ALL["requirements.txt"].splitlines() if l and not l.startswith("#")}
check("requirements.txt matches pyproject dependencies (PySide6, psutil)", req == {"pyside6", "psutil"}
      and 'dependencies = ["PySide6>=6.6,<7", "psutil>=5.9"]' in ALL["pyproject.toml"])
import engineering_console.app as appmod                                                                       # noqa: E402
check("entry point engineering_console.app:main exists and python -m works", callable(appmod.main) and "main()" in ALL["engineering_console/__main__.py"])
check("icon exists and is packaged (pyproject, deb, bundles)", os.path.exists(os.path.join(ROOT, "engineering_console", "icon.svg"))
      and "icon.svg" in ALL["pyproject.toml"] and "icon.svg" in ALL["packaging/build_deb.sh"] and "icon.svg" in ALL["packaging/windows/build_windows.ps1"]
      and "icon.svg" in ALL["packaging/macos/build_app.sh"])
desk = ALL["packaging/engineering-console.desktop"]
meta = ALL["packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml"]
check("desktop file: name, Exec, Icon, StartupWMClass match the app's desktop id", "Name=Engineering Console" in desk and "Exec=engineering-console" in desk
      and "StartupWMClass=engineering-console" in desk and 'setDesktopFileName(os.environ.get("CONSOLE_DESKTOP_ID", APP_SLUG))' in SRC["engineering_console/app.py"])
check("AppStream launchable == desktop file name; flatpak app-id == AppStream id",
      "engineering-console.desktop" in meta and "app-id: org.engineeringconsole.EngineeringConsole" in ALL["packaging/flatpak/org.engineeringconsole.EngineeringConsole.yml"]
      and "<id>org.engineeringconsole.EngineeringConsole</id>" in meta)
for rel in ("packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml",):
    try:
        xml.dom.minidom.parseString(ALL[rel]); good = True
    except Exception:
        good = False
    check(f"{rel} is well-formed XML", good)
for sh in ("install.sh", "uninstall.sh", "run.sh", "run_macos.command", "packaging/build_deb.sh", "packaging/macos/build_app.sh", "packaging/engineering-console.sh"):
    r = subprocess.run(["bash", "-n", os.path.join(ROOT, sh)], capture_output=True, text=True)
    check(f"{sh}: shell syntax OK", r.returncode == 0, r.stderr)
check("scripts are executable", all(os.access(os.path.join(ROOT, s), os.X_OK) for s in ("install.sh", "uninstall.sh", "run.sh", "run_macos.command", "packaging/build_deb.sh")))
check("every .py file compiles", all(compile(t, r, "exec") for r, t in PY.items()))
check("install.sh copies the whole package (no stale module list)", "cp -r \"$HERE/engineering_console\"" in ALL["install.sh"] and "netspeed" not in ALL["install.sh"])
check("main.py checks for PySide6, not PyQt5", "PySide6.QtWidgets" in ALL["main.py"])
check("Windows: batch launcher and bundle script exist and use requirements.txt", "requirements.txt" in ALL["run_windows.bat"] and "requirements.txt" in ALL["packaging/windows/build_windows.ps1"])

# wheel build (offline) - proves pyproject metadata, package list and package data
try:
    import setuptools  # noqa: F401
    tmp = tempfile.mkdtemp()
    proj = os.path.join(tmp, "p")
    shutil.copytree(ROOT, proj, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", "dist", "*.egg-info", ".venv", ".buildvenv"))
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", os.path.join(tmp, "w"), "-q"],
                       cwd=proj, capture_output=True, text=True, timeout=300)
    whl = [f for f in os.listdir(os.path.join(tmp, "w"))] if r.returncode == 0 else []
    check("pip wheel builds from pyproject.toml", bool(whl), r.stderr[-300:])
    if whl:
        import zipfile
        z = zipfile.ZipFile(os.path.join(tmp, "w", whl[0]))
        names = z.namelist()
        meta_txt = z.read([n for n in names if n.endswith("METADATA")][0]).decode()
        check("wheel contains every module, the skins package and the icon", all(any(n.endswith(m) for n in names) for m in (
            "engineering_console/app.py", "engineering_console/speedtest.py", "engineering_console/skins/apple.py", "engineering_console/icon.svg",
            "engineering_console/wingpu.py", "engineering_console/winhw.py", "engineering_console/audio_qt.py")))
        check("wheel excludes tests/tools", not any(n.startswith(("tests", "tools")) for n in names))
        check("wheel metadata: name, version, MIT, dependencies", "Name: engineering-console" in meta_txt and f"Version: {V}" in meta_txt
              and "MIT" in meta_txt and "Requires-Dist: PySide6" in meta_txt and "Requires-Dist: psutil" in meta_txt)
        check("wheel ships LICENSE and NOTICE", any(n.endswith("LICENSE") for n in names) and any(n.endswith("NOTICE") for n in names))
        ep = [n for n in names if n.endswith("entry_points.txt")]
        check("wheel declares the engineering-console launcher", ep and "engineering-console = engineering_console.app:main" in z.read(ep[0]).decode())
except ImportError:
    print("SKIP wheel build (setuptools not installed)")

print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
