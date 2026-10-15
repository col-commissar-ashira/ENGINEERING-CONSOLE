"""Skin isolation tests: python3 tests/test_skins.py
Proves (1) every existing theme still produces the same styles as before the skin refactor,
(2) only the Apple theme uses the Apple skin, (3) switching away from Apple leaves nothing behind."""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tests", "qt_stub"))
sys.path.insert(0, ROOT)
os.environ["HOME"] = tempfile.mkdtemp()

from engineering_console import app, widgets
from engineering_console.skins import skin_for
from engineering_console.platform_info import MONO

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    ok &= bool(cond)


norm = lambda s: re.sub(r"\s+", " ", s).strip()
gold = json.load(open(os.path.join(ROOT, "tests/golden/classic_stylesheets.json")))
classic_themes = [n for n, t in widgets.THEMES.items() if t["skin"] == "classic"]
# The golden file is the stylesheet the app had before skins existed. 3.0 changed it in exactly one way: the selector
# list gained QSpinBox (needed by the new speed-test dialog). Removing that one word must give back the golden text.
same = all(norm(skin_for(widgets.THEMES[n]).stylesheet(widgets.THEMES[n], app.DENS[d]).replace(MONO, "DejaVu Sans Mono")
                .replace(", QSpinBox", "")) == norm(gold[f"{n}|{d}"]) for n in classic_themes for d in app.DENS)
check(f"{len(classic_themes)} remaining themes x 3 densities: stylesheet identical to pre-refactor (+ QSpinBox only)", same)
check("removed themes are not in the golden comparison set", not {"Amber", "Monochrome"} & set(classic_themes))

T, dp = widgets.THEMES["Engineering"], app.DENS["normal"]
sk = skin_for(T)
M = "DejaVu Sans Mono"
if MONO == M:
    check("header/status/banner styles identical to originals",
          sk.title_style(T, dp) == f"font:bold 22px '{M}'; color:#22d3ee; letter-spacing:4px;"
          and sk.subtitle_style(T, dp) == f"font:11px '{M}'; color:#5d7a93;"
          and sk.clock_style(T, dp) == f"font:bold 16px '{M}'; color:#5d7a93;"
          and sk.status_style(True, T, dp) == f"font:bold 16px '{M}'; color:#ff2d2d;"
          and sk.status_style(False, T, dp) == f"font:bold 14px '{M}'; color:#22d3ee;"
          and sk.banner_style(1, dp) == f"background:#c00000; color:white; font:bold 17px '{M}'; padding:8px; border:2px solid #ff4040; border-radius:6px;")
check("only Apple and Apple Dark select the Apple skin", [n for n, t in widgets.THEMES.items() if t["skin"] == "apple"] == ["Apple", "Apple Dark"])
check("themes saved before skins existed fall back to the original look", skin_for({"bg": "#000"}).name == "classic" and widgets.full_theme({"bg": "#000"})["skin"] == "classic")
check("Apple stylesheet has no classic fonts; classic has no Apple markers",
      MONO not in skin_for(widgets.THEMES["Apple"]).stylesheet(widgets.THEMES["Apple"], dp)
      and "SF Pro" not in sk.stylesheet(T, dp) and "switch_on" not in sk.stylesheet(T, dp))

# ---- run the real window through Apple -> back to each classic theme ----
from tests.mock_backend import MockBackend
from engineering_console import sensors
def fake(interval_ms=1000, root="/sys"):
    be = MockBackend(2); d = be.detect()
    for i, x in enumerate(d): x.index = i
    return d, [be]
sensors.detect_gpus = fake
w = app.MainWindow()
sheets = {}
w.setStyleSheet = lambda s: sheets.__setitem__("last", s)
w.set_theme("Apple")
check("Apple theme activates apple skin + sans font stack",
      w.skin.name == "apple" and all(g.skin is not None for g in w.gauges.values()) and widgets.SANS)
apple_sheet = sheets["last"]
for name in classic_themes:
    w.set_theme(name)
    leak = (w.skin.name != "classic" or any(g.skin is not None for g in w.gauges.values()) or widgets.SANS
            or widgets.FONT_STACK != [MONO] or sheets["last"] == apple_sheet or "SF Pro" in sheets["last"])
    if leak:
        check(f"no Apple leakage after switching to {name}", False)
check("no Apple styling leaks into any existing theme after using Apple", True)
w.set_theme("Apple")
w.save_custom_theme("My Apple")
check("custom theme derived from Apple keeps the Apple skin", skin_for(w.cfg["custom_themes"]["My Apple"]).name == "apple")
w.set_theme("Gold")
for mode in ("analog", "digital"):
    pass
# Apple painters must run for every gauge kind
w.set_theme("Apple")
w.apply_profile("AI / Multi-GPU")
for k, g in w.gauges.items():
    for mode in ("analog", "digital"):
        g.mode = mode
        g.set_value(55.0); g.set_value(None); g.set_value(80.0)
        g.paintEvent(None)
w.gauges["cpu_cores"].cores = [5, 50, 95]; w.gauges["cpu_cores"].paintEvent(None)
check("Apple painting runs for all gauge types (analog, digital, GPU panel, core bank)", True)

# ---- Apple Dark: a real dark mode of the same skin; Apple stays the light default ----
from PySide6.QtGui import QColor
from engineering_console.skins import apple as apple_skin
def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)
L, D = widgets.THEMES["Apple"], widgets.THEMES["Apple Dark"]
check("Apple (light) is unchanged and still the Apple default: light background, dark text", L["bg"] == "#f2f2f7" and L["panel"] == "#ffffff" and L["text"] == "#1d1d1f")
check("Apple Dark: dark background, light text, same skin, same keys as Apple", lum(D["bg"]) < 0.05 and lum(D["text"]) > 0.7 and D["skin"] == "apple" and set(D) == set(L))
check("light and dark Apple palettes differ in every surface colour", all(L[k] != D[k] for k in ("bg", "panel", "edge", "track", "accent", "text", "dim", "needle")))
for nm, T in (("Apple", L), ("Apple Dark", D)):
    check(f"{nm}: text on window and gauge >= 7:1, secondary text >= 4.5:1",
          contrast(T["text"], T["bg"]) >= 7 and contrast(T["text"], T["panel"]) >= 7 and contrast(T["dim"], T["panel"]) >= 4.5)
check("Apple Dark: accent / warning / critical colours >= 3:1 on the gauge panel (WCAG for graphics)",
      all(contrast(D[k], D["panel"]) >= 3 for k in ("accent", "warn", "crit")))
check("dark detection: Apple Dark is dark, Apple is not, an edited light Apple stays light", apple_skin.is_dark(D) and not apple_skin.is_dark(L)
      and not apple_skin.is_dark(dict(L, accent="#ff00ff")))
sk_a = skin_for(L)
sheet_l, sheet_d = sk_a.stylesheet(L, dp), sk_a.stylesheet(D, dp)
check("dark stylesheet uses the dark palette everywhere (window, panels, menus, text) and none of the light surface colours",
      D["bg"] in sheet_d and D["panel"] in sheet_d and D["text"] in sheet_d and L["bg"] not in sheet_d and L["panel"] not in sheet_d and L["text"] not in sheet_d)
check("light stylesheet unchanged by dark mode: no dark colours in it", D["bg"] not in sheet_l and D["panel"] not in sheet_l)
real_exists = apple_skin.os.path.exists
apple_skin.os.path.exists = lambda p: True
css_l, css_d = sk_a._switch_css(L), sk_a._switch_css(D)
apple_skin.os.path.exists = real_exists
check("switches use dark-mode images in dark mode and the original images in light mode", "switch_on_dark.png" in css_d and "switch_off_dark.png" in css_d
      and "_dark" not in css_l and "switch_on.png" in css_l)
check("Apple Dark covers the same widget classes as Apple (spin boxes, combo popups, menus, tables, scrollbars)",
      all(sel in sheet_d for sel in ("QSpinBox", "QComboBox QAbstractItemView", "QMenu", "QTableWidget", "QScrollBar", "QToolTip", "QPushButton", "QCheckBox")))
w.set_theme("Apple Dark")
dark_sheet = sheets["last"]
check("window: Apple Dark activates the apple skin, dark sheet, sans fonts, drop marker follows the accent",
      w.skin.name == "apple" and dark_sheet != apple_sheet and w.theme["bg"] == D["bg"] and w.grid.marker.color == D["accent"] and widgets.SANS)
w.set_theme("Apple")
check("switching Apple Dark -> Apple restores the light sheet exactly", sheets["last"] == apple_sheet and w.theme["bg"] == L["bg"])
for name in classic_themes:
    w.set_theme(name)
    if w.skin.name != "classic" or sheets["last"] in (apple_sheet, dark_sheet) or "SF Pro" in sheets["last"] or widgets.SANS:
        check(f"no Apple/Apple Dark leakage after switching to {name}", False)
        break
else:
    check("no Apple or Apple Dark leakage into any of the 7 other themes", True)
w.set_theme("Apple Dark")
w.apply_profile("AI / Multi-GPU")
for k, g in w.gauges.items():
    for mode in ("analog", "digital"):
        g.mode = mode
        g.set_value(55.0); g.set_value(None); g.set_value(80.0)
        g.paintEvent(None)
w.gauges["cpu_cores"].cores = [5, 50, 95]; w.gauges["cpu_cores"].paintEvent(None)
check("Apple Dark paints every gauge type without exceptions (analog, digital, GPU panel, core bank)", True)
print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
