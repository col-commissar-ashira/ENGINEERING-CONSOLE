"""UI logic smoke test with a fake Qt (see tests/qt_stub). python3 tests/test_ui_logic.py"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tests", "qt_stub"))
sys.path.insert(0, ROOT)
home = tempfile.mkdtemp()
os.environ["HOME"] = home
os.makedirs(home + "/.config/system-cockpit")
# an OLD v1 config (single-GPU key names, no version) must migrate cleanly
json.dump({"visible": ["cpu_util", "gpu_util", "vram_pct"], "order": ["gpu_util", "cpu_util"], "pinned": ["gpu_temp"],
           "thresholds": {"gpu_temp": [70, 80]}, "theme": "Tactical", "first_run_done": True, "compact": True},
          open(home + "/.config/system-cockpit/config.json", "w"))

from engineering_console import sensors, app, widgets
from tests.mock_backend import MockBackend

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    ok &= bool(cond)


cfg, existed = app.load_cfg()
check("legacy keys migrated", "gpu0_util" in cfg["visible"] and "gpu0_vram_pct" in cfg["visible"] and cfg["pinned"] == ["gpu0_temp"]
      and "gpu0_temp" in cfg["thresholds"])
check("v1 upgrade adds audio+speed gauges", {"audio_out", "audio_in", "speed_test"} <= set(cfg["visible"]) and cfg["speed_auto"] == "launch")
check("balanced columns 1/2/4/8/10/12", [app.balanced_cols(n) for n in (1, 2, 4, 8, 10, 12)] == [1, 2, 2, 4, 5, 4])
check("10 tiles -> 5x2", app.balanced_cols(10) == 5 and -(-10 // 5) == 2)
check("all 9 themes complete (7 original + Apple + Apple Dark)", len(widgets.THEMES) == 9 and all({k for k, _ in widgets.COLOR_KEYS} <= set(t) for t in widgets.THEMES.values()))

def fake(interval_ms=1000, root="/sys"):
    be = MockBackend(10); d = be.detect()
    for i, x in enumerate(d): x.index = i
    return d, [be]
sensors.detect_gpus = fake

w = app.MainWindow()
check("window built with 10 GPU panels", sum(isinstance(g, widgets.GpuPanel) for g in w.gauges.values()) == 10)
for _ in range(3):
    w.on_poll()
check("poll feeds GPU panels", w.gauges["gpu4_panel"].value is not None or "gpu4_panel" not in w.vis_keys)
for name in w.builtin_profiles():
    w.apply_profile(name)
    check(f"profile '{name}' applies ({len(w.vis_keys)} gauges)", len(w.vis_keys) > 0 and all(k in w.gauges for k in w.vis_keys))
w.apply_profile("AI / Multi-GPU")
check("AI profile shows all 10 GPUs at once", all(f"gpu{i}_panel" in w.vis_keys for i in range(10)) and w.cfg["cols"] == -1)
for d in ("lowres", "compact", "normal", "auto"):
    w.set_density(d)
check("density modes cycle", True)
w.set_cols(5); w.set_gauge_size("S"); w.toggle_mode(); w.toggle_mode(); w.toggle_temp(); w.toggle_temp()
g = w.gauges["gpu0_temp"]
g.set_temp_unit(False)          # default config is Fahrenheit; start from Celsius
g.set_value(100.0)
g.set_temp_unit(True)
check("temp gauge converts 100C -> 212F, scale 32..212", abs(g.value - 212) < 1e-6 and abs(g.lo - 32) < 1e-6 and abs(g.hi - 212) < 1e-6 and g.unit == "°F")
g.set_temp_unit(False)
check("...and back to C", abs(g.value - 100) < 1e-6 and g.unit == "°C")
w.save_custom_theme("My Theme")
check("custom theme saved", "My Theme" in w.cfg["custom_themes"])
w.set_theme("Gold"); w.set_theme("Red Alert"); w.set_theme("My Theme")
w.cfg["profiles"]["Mine"] = {"visible": ["cpu_util"], "cols": 1}
w.apply_profile("Mine")
check("user profile applies", w.vis_keys == {"cpu_util"})
# red alert: hard limit on one GPU, then clear
seen = []
orig_set = w.set_alert
w.set_alert = lambda on, reasons: (seen.append(list(reasons)), orig_set(on, reasons))[1]
T0 = time.monotonic() + 1000; w._a_since = w._a_clear = None; w.alert_active = False
t = {k: None for k in w.mby}; t["gpu6_temp"] = 95.0; t["load1"] = 0.0
w.check_alert(t, T0); w.check_alert(t, T0 + 1.5)
check("red alert fires on one GPU at 95C and names GPU6", w.alert_active and any("GPU6" in r for rs in seen for r in rs))
t["gpu6_temp"] = 60.0
w.check_alert(t, T0 + 10); w.check_alert(t, T0 + 16)
check("red alert clears after calm period", not w.alert_active)
t.update(cpu_util=99.0, gpu0_util=99.0, ram_pct=95.0)
w.check_alert(t, T0 + 100); w.check_alert(t, T0 + 104)
check("red alert fires when 3 'maxed' conditions hold", w.alert_active)
# paint every gauge type through the fake painter (catches NameError/TypeError in paint code)
w.apply_profile("AI / Multi-GPU")
for k in list(w.gauges):
    g = w.gauges[k]
    for mode in ("analog", "digital"):
        g.mode = mode
        g.set_value(55.0); g.set_value(None); g.set_value(70.0)
        g.paintEvent(None)
        g.tooltip_text()
w.gauges["cpu_cores"].cores = [10, 90, 50]; w.gauges["cpu_cores"].paintEvent(None)
check("all gauges/panels paint without exceptions", True)
try:
    w.closeEvent(None)
except AttributeError:
    pass            # fake QMainWindow has no real closeEvent; our own save/close code ran before it
print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
