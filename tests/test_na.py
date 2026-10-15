"""N/A handling: why-overlay, auto-hide, real error reasons. Headless (fake Qt in tests/qt_stub). python3 tests/test_na.py"""
import json
import logging
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tests", "qt_stub"))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME
ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok &= bool(cond)


from engineering_console import app, nainfo, sensors, widgets, logsetup, platform_info    # noqa: E402
from tests.mock_backend import MockBackend                                                # noqa: E402
import psutil                                                                             # noqa: E402

sensors.detect_gpus = lambda interval_ms=1000, root="/sys": (lambda be: ([setattr(x, "index", i) or x for i, x in enumerate(be.detect())], [be]))(MockBackend(2))

# ---------------------------------------------------------------- headline classification (uses the app's real reason texts)
cases = [
    (sensors.NA_DEFAULT["cpu_power"].replace("Intel RAPL", "Intel RAPL"), None),
    ("Needs administrator access: Tools > Enable advanced Mac sensors", "permission"),
    ("No audio data: allow it in System Settings > Privacy & Security > Microphone", "permission"),
    ("LibreHardwareMonitor is not running (start it as administrator)", "missing"),
    ("No CPU temperature sensor in /sys/class/hwmon (try: sudo apt install lm-sensors)", "missing"),
    ("Intel RAPL energy counter not readable (kernel restricts it to root). Run install.sh --enable-rapl", "missing"),
    ("Hardware sensor reader failed: boom", "error"),
    ("Read failed (OSError: boom)", "error"),
    ("Starting audio meter...", "waiting"),
    ("No test yet (right-click > Run speed test now)", "waiting"),
    ("NVIDIA driver tools missing: install the proprietary driver so nvidia-smi is available", "missing"),
    ("Kernel exposes no fan tachometer on this laptop", "unsupported"),
    ("", "unsupported"),
]
for text, want in cases:
    if want is None:
        continue
    k, title = nainfo.classify(text)
    check(f"classify {text[:48]!r} -> {want}", k == want, f"{k}/{title}")
check("headlines are short, upper-case phrases", all(t.isupper() and len(t) <= 28 for _, t in [nainfo.classify(c) for c, _ in cases]))
check("every kind has a headline", {k for k, *_ in nainfo.KINDS} | {nainfo.FALLBACK[0]} == {"error", "permission", "missing", "waiting", "unsupported"})

# ---------------------------------------------------------------- wrapping
m = lambda s: len(s) * 7
lines = nainfo.wrap_by_width("Intel RAPL energy counter not readable (kernel restricts it to root)", m, 140, 6)
check("wrap: every line fits the width and no words are lost", all(m(l) <= 140 for l in lines) and " ".join(lines) == "Intel RAPL energy counter not readable (kernel restricts it to root)", str(lines))
lines = nainfo.wrap_by_width("word " * 60, m, 140, 3)
check("wrap: too much text is cut with '...' on the last line, never overflows", len(lines) == 3 and lines[-1].endswith("...") and all(m(l) <= 140 for l in lines), str(lines))
check("wrap: a single very long word is hard-cut to fit", all(m(l) <= 70 for l in nainfo.wrap_by_width("x" * 100, m, 70, 20)))
check("wrap: empty text gives no lines", nainfo.wrap_by_width("", m, 100, 3) == [])
check("nainfo is Qt-free", "PySide6" not in open(os.path.join(ROOT, "engineering_console", "nainfo.py")).read())

# ---------------------------------------------------------------- overlay on the gauge
theme = widgets.THEMES["Engineering"]
mdef = [x for x in sensors.METRICS if x.key == "cpu_temp"][0]
g = widgets.Gauge(mdef, theme)
g.set_value(None); g.reason = "Needs administrator access"
check("overlay active when there is no value and the option is on", g.na_overlay_active())
g.set_value(55.0)
check("overlay off as soon as data returns", not g.na_overlay_active())
g.set_value(None); g.na_overlay = False
check("overlay off when the user chose Plain N/A", not g.na_overlay_active())
g.na_overlay = True
cores = widgets.Gauge([x for x in sensors.METRICS if x.key == "cpu_cores"][0], theme); cores.set_value(None)
check("the per-core bank never gets the overlay (it draws its own cores)", not cores.na_overlay_active())
check("GPU panels keep their own per-field reasons (no overlay)", widgets.GpuPanel.supports_na_overlay is False)
frames = [g.tick() for _ in range(6)]
check("overlay animates at about half the 33 ms timer rate (every other frame repaints)", frames == [False, True, False, True, False, True], str(frames))
for name, th in widgets.THEMES.items():
    gg = widgets.Gauge(mdef, th)
    from engineering_console.skins import skin_for
    gg.skin = skin_for(th) if th["skin"] == "apple" else None      # the app only gives Apple themes a skin object
    for mode in ("analog", "digital"):
        gg.mode = mode
        gg.set_value(None)
        for reason in ("", "Read failed (OSError: boom)", "x " * 400):
            gg.reason = reason
            gg.paintEvent(None)
check("overlay paints in all 9 themes, both modes, with empty / error / enormous reasons", True)

# ---------------------------------------------------------------- auto-hide
os.makedirs(platform_info.config_dir(), exist_ok=True)
w = app.MainWindow()
w.cfg["first_run_done"] = True            # the real app sets this after the welcome dialog; without it every new window re-picks a profile
w.apply_profile("Default")
shown = w.ordered_visible(hide_na=True)
user_visible = list(w.cfg["visible"])
w.set_na_mode("hide")
keys = [k for k in w.vis_keys if k not in app.NA_NEVER_HIDE and k != "cpu_cores" and not isinstance(w.gauges[k], widgets.GpuPanel)]
check("there are gauges to test auto-hide with", len(keys) >= 3, str(keys))
na, other = keys[0], keys[1]
base = {k: 1.0 for k in w.gauges}
vals = dict(base); vals[na] = None
for k in app.NA_NEVER_HIDE:
    if k in vals:
        vals[k] = None
t0 = 1000.0
w._update_auto_hide(vals, t0)
check("a gauge that just went N/A is NOT hidden yet (sensors start N/A; grace period)", na not in w.auto_hidden)
w._update_auto_hide(vals, t0 + app.NA_HIDE_AFTER_S - 1)
check("still visible just before the grace period ends", na not in w.auto_hidden)
w._update_auto_hide(vals, t0 + app.NA_HIDE_AFTER_S + 1)
check("after 20 s without data the gauge steps aside", na in w.auto_hidden and na not in w.ordered_visible(hide_na=True))
check("auto-hide never touches the user's selection, order or pins", w.cfg["visible"] == user_visible and na in w.ordered_visible() and na in w.vis_keys)
check("gauges that need to be visible to work are exempt (speed test, audio meters)",
      not (w.auto_hidden & app.NA_NEVER_HIDE) and all(k in w.ordered_visible(hide_na=True) for k in app.NA_NEVER_HIDE if k in w.vis_keys))
check("the per-core bank and GPU panels are never auto-hidden", "cpu_cores" not in w.auto_hidden and not any(isinstance(w.gauges[k], widgets.GpuPanel) for k in w.auto_hidden))
w.cfg["profiles"]["Probe"] = {"visible": w.ordered_visible()}
w.save_profile = None
check("a profile saved while a gauge is auto-hidden still contains it", na in w.ordered_visible())
w.on_reorder(w.ordered_visible(hide_na=True)[-1], 0)
check("dragging while something is auto-hidden reorders what you see and keeps the hidden gauge's slot", na in w.cfg["order"] and na in w.ordered_visible())
vals[na] = 42.0
w._update_auto_hide(vals, t0 + app.NA_HIDE_AFTER_S + 2)
check("the moment data returns the gauge comes back by itself (no gauge-selection visit)", na not in w.auto_hidden and na in w.ordered_visible(hide_na=True))
vals[na] = None
w._update_auto_hide(vals, t0 + 100)
w._update_auto_hide(vals, t0 + 100 + app.NA_HIDE_AFTER_S + 1)
check("a gauge that goes N/A again gets a fresh grace period", na in w.auto_hidden)
w.set_na_mode("overlay")
check("switching to the overlay mode brings every hidden gauge back", not w.auto_hidden and na in w.ordered_visible(hide_na=True)
      and all(g.na_overlay for g in w.gauges.values()))
w.set_na_mode("plain")
check("plain mode switches the overlay off on every gauge", not any(g.na_overlay for g in w.gauges.values()))
w.set_na_mode("hide"); w._update_auto_hide(vals, t0 + 300); w._update_auto_hide(vals, t0 + 400)
w.save_cfg()
w2 = app.MainWindow()
check("the chosen mode persists, the transient hidden set does not", w2.cfg["na_mode"] == "hide" and not w2.auto_hidden and na in w2.ordered_visible(hide_na=True))
json.dump({"na_mode": "sparkly"}, open(os.path.join(platform_info.config_dir(), "config.json"), "w"))
check("an invalid saved mode falls back to the overlay", app.load_cfg()[0]["na_mode"] == "overlay")
check("new setting defaults to the overlay", app.DEFAULT_CFG["na_mode"] == "overlay")

# ---------------------------------------------------------------- real errors become the reason
lp = os.path.join(HOME, "logs", "na.log")
logsetup._configured = False
lg = logsetup.setup(lp)
t = sensors.Telemetry(1000)
real_freq = psutil.cpu_freq
psutil.cpu_freq = lambda: (_ for _ in ()).throw(OSError("sysfs exploded"))
d = t.poll(); d = t.poll(); d = t.poll()
check("a failing sensor read shows what failed, not a generic 'not exposed'", d["cpu_freq"] is None and t.reasons["cpu_freq"] == "Read failed (OSError: sysfs exploded)", t.reasons.get("cpu_freq"))
for h in lg.handlers:
    h.flush()
logged = [json.loads(l) for l in open(lp)]
check("the failure is in the log with a traceback, once (not 3 times)", len([l for l in logged if l.get("where") == "cpu_freq"]) == 1 and "sysfs exploded" in logged[0]["traceback"])
psutil.cpu_freq = real_freq
d = t.poll()
check("when the sensor recovers the error text disappears", d["cpu_freq"] is not None and "cpu_freq" not in t.reasons)
real_poll = t._poll
def broken(d, now, dt):
    raise RuntimeError("driver went away")
t._poll = broken
d = t.poll()
check("a crash inside the poll becomes every gauge's reason (no silent N/A)", d["cpu_util"] is None and "RuntimeError: driver went away" in t.reasons["cpu_util"], t.reasons.get("cpu_util"))
t._poll = real_poll
d = t.poll()
check("after the crash clears, values and reasons recover", d["cpu_util"] is not None and "cpu_util" not in t.reasons)
real_fans = getattr(psutil, "sensors_fans", None)
if real_fans:
    delattr(psutil, "sensors_fans")
    t._slow_t = 0
    d = t.poll()
    check("an API the OS simply does not have is 'unsupported', not a bug (no 'Read failed')", "Read failed" not in t.reasons.get("fan_rpm", ""))
    psutil.sensors_fans = real_fans
t.close()

print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
