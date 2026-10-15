"""3.0 feature tests (run headless with the fake Qt in tests/qt_stub). python3 tests/test_console.py

Covers: theme removal + settings migration, drag-reorder logic + persistence, settings persistence and damaged
configs, legacy (pre-rename) settings migration, RAM definition, Windows parsers/paths, audio decoding, logging.
"""
import json
import logging
import os
import sys
import tempfile
from collections import namedtuple

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tests", "qt_stub"))
sys.path.insert(0, ROOT)
HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME
os.environ.pop("XDG_CONFIG_HOME", None)

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok &= bool(cond)


from engineering_console import app, widgets, sensors, platform_info, logsetup, audio_qt, winhw, wingpu, ordering   # noqa: E402
from engineering_console.skins import skin_for                                                                   # noqa: E402
from tests.mock_backend import MockBackend                                                                       # noqa: E402

CONF_DIR = platform_info.config_dir()
CONF = os.path.join(CONF_DIR, "config.json")


def write_cfg(d):
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(CONF, "w") as f:
        json.dump(d, f)


def fake_detect(interval_ms=1000, root="/sys"):
    be = MockBackend(2)
    d = be.detect()
    for i, x in enumerate(d):
        x.index = i
    return d, [be]


sensors.detect_gpus = fake_detect

# ---------------------------------------------------------------- themes
check("Amber and Monochrome are gone from THEMES", "Amber" not in widgets.THEMES and "Monochrome" not in widgets.THEMES)
check("themes: the 7 kept originals, Apple (light, default) and Apple Dark",
      list(widgets.THEMES) == ["Engineering", "Tactical", "Minimal", "High Contrast", "Gold", "Red Alert", "Ice", "Apple", "Apple Dark"])
check("every remaining theme defines every colour key", all({k for k, _ in widgets.COLOR_KEYS} <= set(t) for t in widgets.THEMES.values()))
check("every theme resolves to a skin; only Apple and Apple Dark use the apple skin",
      all(skin_for(t).name == ("apple" if n.startswith("Apple") else "classic") for n, t in widgets.THEMES.items()))
src = open(os.path.join(ROOT, "engineering_console", "app.py")).read() + open(os.path.join(ROOT, "engineering_console", "widgets.py")).read()
check("the theme menu is built from THEMES (no hard-coded Amber/Monochrome entries)",
      src.count('"Amber"') == 1 and src.count('"Monochrome"') == 1)       # only the REMOVED_THEMES migration tuple

# saved settings that still name a removed theme fall back instead of crashing
amber = widgets._t("#0a0700", "#130e03", "#3a2c0a", "#211804", "#ffb000", "#ff7a00", "#ff2a1a", "#ffe9b8", "#8f7430")
write_cfg({"version": 2, "theme": "Amber", "theme_colors": amber, "first_run_done": True,
           "profiles": {"Old": {"visible": ["cpu_util"], "theme": "Monochrome", "theme_colors": amber}}})
cfg, existed = app.load_cfg()
check("config naming Amber -> Engineering, stale colours dropped",
      cfg["theme"] == "Engineering" and cfg["theme_colors"] is None)
check("saved profile naming Monochrome -> Engineering", cfg["profiles"]["Old"]["theme"] == "Engineering"
      and "theme_colors" not in cfg["profiles"]["Old"])
write_cfg({"version": 3, "theme": "Amber", "custom_themes": {"Amber": dict(amber)}, "first_run_done": True})
cfg, _ = app.load_cfg()
check("a user's OWN custom theme that happens to be called Amber is kept", cfg["theme"] == "Amber" and "Amber" in cfg["custom_themes"])

# ---------------------------------------------------------------- damaged / odd configs never crash
for label, content in (("not JSON", "{{{ nope"), ("a list", "[1,2,3]"), ("wrong types", json.dumps(
        {"visible": "cpu_util", "order": 5, "refresh_ms": "fast", "speed_size": "gigantic",
         "speed_continuous": {"interval_s": "x", "data_limit_mb": -5}, "theme": 7}))):
    os.makedirs(CONF_DIR, exist_ok=True)
    open(CONF, "w").write(content)
    try:
        cfg, _ = app.load_cfg()
        good = (isinstance(cfg["visible"], list) and isinstance(cfg["order"], list) and cfg["speed_size"] in app.SIZES
                and cfg["speed_continuous"]["interval_s"] > 0 and cfg["speed_continuous"]["data_limit_mb"] >= 0
                and isinstance(cfg["theme"], str) and isinstance(cfg["refresh_ms"], int))
    except Exception as e:
        good = False
        print("   ", repr(e))
    check(f"damaged config ({label}) loads with safe defaults", good)

# ---------------------------------------------------------------- legacy (pre-rename) settings migrate, old folder untouched
if os.path.exists(CONF):
    os.remove(CONF)
legacy_dir = platform_info.legacy_config_dir()
os.makedirs(legacy_dir, exist_ok=True)
json.dump({"version": 2, "theme": "Ice", "refresh_ms": 1000, "first_run_done": True}, open(os.path.join(legacy_dir, "config.json"), "w"))
cfg, existed = app.load_cfg()
check("pre-rename settings are migrated to the new folder", cfg["theme"] == "Ice" and cfg["refresh_ms"] == 1000 and os.path.exists(CONF))
check("the old folder is left untouched (copy, not move)", os.path.exists(os.path.join(legacy_dir, "config.json")))
json.dump({"theme": "Gold"}, open(CONF, "w"))
cfg, _ = app.load_cfg()
check("migration never overwrites newer settings", cfg["theme"] == "Gold")
check("config folder uses the new name", "engineering-console" in CONF_DIR and "cockpit" not in CONF_DIR)

# ---------------------------------------------------------------- ordering (pure) + persistence through the window
order = ["a", "b", "c", "d", "e", "f"]
disp = ordering.display_order(order, {"a", "c", "d", "f"}, {"f"})
check("display order: visible gauges in saved order, pinned first", disp == ["f", "a", "c", "d"])
new, clamped = ordering.reorder(order, ["a", "c", "d"], set(), "d", 0)
check("drag d onto a's cell -> d moves before a; hidden gauges keep their slots", new == ["d", "b", "a", "c", "e", "f"] and not clamped, str(new))
new, clamped = ordering.reorder(order, ["a", "c", "d"], set(), "a", 2)
check("drag a onto d's cell -> a lands after d (exact order)", new == ["c", "b", "d", "a", "e", "f"] and not clamped, str(new))
check("dropping on its own cell changes nothing", ordering.reorder(order, ["a", "c", "d"], set(), "c", 1) == (None, False))
check("an unpinned gauge dropped onto the pinned cell: nothing changes, user is told (clamped)",
      ordering.reorder(order, disp, {"f"}, "a", 0) == (None, True))
n2, cl = ordering.reorder(order, disp, {"f"}, "d", 0)
check("unpinned d dropped at index 0 lands right behind the pinned gauge (clamped)",
      cl and ordering.display_order(n2, {"a", "c", "d", "f"}, {"f"}) == ["f", "d", "a", "c"], str(n2))
check("unknown key is ignored", ordering.reorder(order, disp, set(), "zzz", 0) == (None, False))
check("full_order drops unknown keys and appends new gauges", ordering.full_order(["x", "b", "a"], ["a", "b", "c"]) == ["b", "a", "c"])

if os.path.exists(CONF):
    os.remove(CONF)
w = app.MainWindow()
w.apply_profile("Default")
before = w.ordered_visible()
key, other = before[-1], before[0]
w.on_reorder(key, 0)                                       # what GaugeGrid.dropEvent emits
after = w.ordered_visible()
check("window: dropping the last gauge on the first cell moves it first", after[0] == key and set(after) == set(before), str(after[:3]))
w.save_cfg()
w2 = app.MainWindow()
check("gauge order persists between launches", w2.ordered_visible() == after, str(w2.ordered_visible()[:3]))
hidden_before = [k for k in w2.gauges if k not in w2.vis_keys]
w2.hide_gauge(after[1]); w2.on_reorder(after[2], 0); w2.save_cfg()
w3 = app.MainWindow()
check("order survives hide/show cycles", w3.ordered_visible()[0] == after[2] and after[1] not in w3.vis_keys)
w3.cfg["pinned"] = [w3.ordered_visible()[1]]
w3.apply_layout()
pin = w3.ordered_visible()[0]
w3.on_reorder(w3.ordered_visible()[2], 0)
check("pinned gauge stays first even when something is dropped onto it", w3.ordered_visible()[0] == pin)
w3.save_profile = lambda: None
w3.cfg["profiles"]["Mine"] = {"visible": w3.ordered_visible()}
w3.apply_profile("Mine")
check("profiles keep the visible order", w3.ordered_visible()[:3] == w3.cfg["profiles"]["Mine"]["visible"][:3])

# ---------------------------------------------------------------- other settings persist
w3.set_theme("Apple"); w3.set_speed_size("normal"); w3.set_density("compact"); w3.set_units(False)
w3.cfg["speed_continuous"].update(interval_s=5, duration_min=20, max_tests=240, data_limit_mb=300)
w3.cfg["speed_endpoints"] = ["http://127.0.0.1:9"]
w3.save_cfg()
w4 = app.MainWindow()
c4 = w4.cfg
check("theme / speed size / density / units persist", c4["theme"] == "Apple" and c4["speed_size"] == "normal"
      and c4["density"] == "compact" and c4["net_bits"] is False and w4.skin.name == "apple")
check("continuous settings persist (5 s, 20 min, 240 tests, 300 MB)", c4["speed_continuous"] == {
    "interval_s": 5, "duration_min": 20, "max_tests": 240, "data_limit_mb": 300})
cc = w4.continuous_config()
check("continuous config is built from settings; limit is TOTAL bytes", cc.interval_s == 5 and cc.duration_s == 1200
      and cc.max_tests == 240 and cc.data_limit_bytes == 300_000_000 and cc.size == "normal")
check("custom endpoints persist and reach the engine", w4.tele.speed.engine.endpoints == ["http://127.0.0.1:9"])
check("speed size reaches the engine", w4.tele.speed.snapshot().size == "normal")
# lifetime traffic accounting
w4.tele.speed.engine._add_traffic(down=3_000_000, up=1_000_000)
w4._account_speed_traffic()
check("lifetime speed-test traffic is accumulated in settings", w4.cfg["speed_traffic_total"] == {"down": 3_000_000, "up": 1_000_000})
w4.tele.speed.engine.reset_traffic()
w4._account_speed_traffic()
check("resetting the session counters does not erase the lifetime total", w4.cfg["speed_traffic_total"]["down"] == 3_000_000)
w4.run_speed(); w4.tele.speed.cancel(); w4.tele.speed.engine.wait(10)
check("run + cancel from the window leaves the engine idle", w4.tele.speed.state.name == "IDLE")
w4.closeEvent(None) if False else None

# ---------------------------------------------------------------- RAM: one definition on every OS, own process shown
GIB = 2 ** 30
Linux = namedtuple("svmem", "total available percent used free active inactive buffers cached shared slab")
Win = namedtuple("svmem", "total available percent used free")
Mac = namedtuple("svmem", "total available percent used free active inactive wired")
vms = {"linux": Linux(16 * GIB, 10 * GIB, 37.5, 4 * GIB, 2 * GIB, 3 * GIB, 2 * GIB, 1 * GIB, 5 * GIB, 0, 1 * GIB),
       "windows": Win(16 * GIB, 10 * GIB, 37.5, 6 * GIB, 10 * GIB),
       "macos": Mac(16 * GIB, 10 * GIB, 37.5, 9 * GIB, 1 * GIB, 5 * GIB, 4 * GIB, 3 * GIB)}
check("USED = TOTAL - AVAILABLE for Linux / Windows / macOS shaped data (psutil.used is NOT used)",
      all(sensors.ram_numbers(v)[2] == 6 * GIB for v in vms.values()))
import psutil                                                                                    # noqa: E402
t = sensors.Telemetry(1000)
real_vm = psutil.virtual_memory
for name, vm in vms.items():
    psutil.virtual_memory = lambda vm=vm: vm
    d = t.poll()
    check(f"poll on {name}-shaped memory: ram_used == 6 GiB, free == 10 GiB, pct == 37.5",
          abs(d["ram_used"] - 6) < 1e-9 and abs(d["ram_avail"] - 10) < 1e-9 and abs(d["ram_pct"] - 37.5) < 1e-6)
    check(f"{name}: used-without-app = used - own memory", abs(d["ram_used_noapp"] - (6 - t.app_mem[0] / GIB)) < 1e-6 and d["app_ram"] > 5)
    txt = sensors.ram_detail_text(vm, t.app_mem[0], t.app_mem[1], t.app_mem[2])
    check(f"{name}: tooltip states the definition and keeps OS details separate", "total - available" in txt and "informational" in txt)
psutil.virtual_memory = real_vm
check("own process memory is USS on this platform (psutil memory_full_info)", t.app_mem[1] in ("USS", "USS+RSS") and t.app_mem[0] > 1_000_000, str(t.app_mem))
bytes_, kind, n = sensors.app_memory(psutil.Process())
check("app_memory includes helper processes and reports the measurement kind", n >= 1 and kind in ("USS", "RSS", "USS+RSS"))
fake_ns = namedtuple("c", "bytes_recv bytes_sent")
fake_st = namedtuple("s", "isup")
check("busiest_interface skips loopback and interfaces that are down",
      sensors.busiest_interface({"lo": fake_ns(10 ** 9, 0), "Loopback Pseudo-Interface 1": fake_ns(10 ** 9, 0),
                                 "eth0": fake_ns(5, 5), "wlan0": fake_ns(10 ** 6, 10 ** 6), "dead": fake_ns(10 ** 10, 0)},
                                {"eth0": fake_st(True), "wlan0": fake_st(True), "dead": fake_st(False)}) == "wlan0")
t.close()

# ---------------------------------------------------------------- Windows layer (pure parts) and platform paths
sample = json.dumps([
    {"SensorType": "Temperature", "Name": "CPU Package", "Identifier": "/intelcpu/0/temperature/16", "Value": 61.0},
    {"SensorType": "Temperature", "Name": "Core #1", "Identifier": "/intelcpu/0/temperature/1", "Value": 70.0},
    {"SensorType": "Fan", "Name": "Fan #1", "Identifier": "/lpc/nct/fan/0", "Value": 0},
    {"SensorType": "Power", "Name": "CPU Package", "Identifier": "/intelcpu/0/power/0", "Value": 14.5},
    {"SensorType": "Temperature", "Name": "Composite", "Identifier": "/nvme/0/temperature/0", "Value": 38},
    {"SensorType": "Temperature", "Name": "GPU Core", "Identifier": "/gpu-nvidia/0/temperature/0", "Value": 55}])
r = winhw.parse_sensors(sample)
check("LibreHardwareMonitor sensors parsed (CPU temp prefers Package, fan 0 RPM kept, GPU ignored)",
      r == {"cpu_temp": 61.0, "ssd_temp": 38.0, "cpu_power": 14.5, "fan_rpm": 0.0}, str(r))
check("a single-sensor JSON object (PowerShell quirk) parses", winhw.parse_sensors(json.dumps(
    {"SensorType": "Fan", "Name": "f", "Identifier": "/x/fan/0", "Value": 1200}))["fan_rpm"] == 1200.0)
check("no sensors -> all None (never invented)", set(winhw.parse_sensors("[]").values()) == {None})
eng = {"pid_1_luid_0x00000000_0x0000F4A2_phys_0_eng_0_engtype_3D": 30, "pid_2_luid_0x00000000_0x0000F4A2_phys_0_eng_0_engtype_3D": 25,
       "pid_2_luid_0x00000000_0x0000F4A2_phys_0_eng_3_engtype_Copy": 10, "pid_9_luid_0x00000000_0x0000AAAA_phys_0_eng_0_engtype_3D": 150}
agg = wingpu.aggregate_engine_load(eng)
check("GPU load: processes summed per engine, busiest engine wins, capped at 100",
      agg == {"0x00000000_0x0000f4a2": 55.0, "0x00000000_0x0000aaaa": 100.0}, str(agg))
check("GPU memory counters aggregate per adapter", wingpu.aggregate_memory({"luid_0x00000000_0x0000F4A2_phys_0": 2e9})
      == {"0x00000000_0x0000f4a2": 2e9})
check("DXGI LUID formatting matches the counter instance names", wingpu.luid_key(0, 0xF4A2) == "0x00000000_0x0000f4a2")
check("DXGI/PDH layer is inert off Windows (no adapters, no crash)", wingpu.list_adapters() == [])

real = (platform_info.IS_WIN, platform_info.IS_MAC)
platform_info.IS_WIN, platform_info.IS_MAC = True, False
os.environ["APPDATA"], os.environ["LOCALAPPDATA"] = r"C:\Users\x\AppData\Roaming", r"C:\Users\x\AppData\Local"
cd, ca, dd = platform_info.config_dir(), platform_info.cache_dir(), platform_info.data_dir()
check("Windows folders: %APPDATA% config, %LOCALAPPDATA% cache/data, named Engineering Console",
      "Engineering Console" in cd and cd.startswith(r"C:\Users\x\AppData\Roaming") and ca.startswith(r"C:\Users\x\AppData\Local")
      and dd.startswith(r"C:\Users\x\AppData\Local"), f"{cd} {ca} {dd}")
check("Windows legacy settings folder still found for migration", "System Cockpit" in platform_info.legacy_config_dir())
check("Windows: no console window flashes for helper processes", "creationflags" in platform_info.hidden_subprocess_kwargs())
platform_info.IS_WIN, platform_info.IS_MAC = False, True
check("macOS folders: Application Support / Caches", "Application Support/Engineering Console" in platform_info.config_dir()
      and "Caches/Engineering Console" in platform_info.cache_dir())
platform_info.IS_WIN, platform_info.IS_MAC = real
check("Linux folders follow XDG-style names", (platform_info.IS_WIN or platform_info.IS_MAC) or platform_info.config_dir().endswith(".config/engineering-console"))

# ---------------------------------------------------------------- audio decoding (Qt Multimedia path used on Windows/macOS)
import array, math                                                                              # noqa: E402
tone16 = array.array("h", [int(16384 * math.sin(i / 3)) for i in range(2000)]).tobytes()
check("Int16 peak ~ -6 dBFS", abs(audio_qt.peak_to_db(audio_qt.peak_from_bytes(tone16, "Int16")) + 6.02) < 0.2)
check("Float peak", abs(audio_qt.peak_from_bytes(array.array("f", [0.0, 0.5, -0.25]).tobytes(), "Float") - 0.5) < 1e-6)
check("Int32 peak", abs(audio_qt.peak_from_bytes(array.array("i", [0, 2 ** 30]).tobytes(), "Int32") - 0.5) < 1e-6)
check("UInt8 peak", abs(audio_qt.peak_from_bytes(bytes([128, 192, 64]), "UInt8") - 0.5) < 1e-6)
check("unsupported format -> None (meter says so; nothing invented)", audio_qt.peak_from_bytes(b"\0\0", "Int24") is None)
check("silence is the -60 dB floor, not -inf", audio_qt.peak_to_db(0.0) == -60.0)

# ---------------------------------------------------------------- structured logging
lp = os.path.join(HOME, "logs", "t.log")
logsetup._configured = False
lg = logsetup.setup(lp)
logsetup.event(lg, "unit.test", logging.WARNING, reason="rate_limited", n=3)
try:
    raise ValueError("boom")
except ValueError:
    logsetup.event(lg, "unit.exc", logging.ERROR, exc_info=True)
for h in lg.handlers:
    h.flush()
lines = [json.loads(l) for l in open(lp)]
check("log lines are JSON with event + fields", lines[0]["event"] == "unit.test" and lines[0]["reason"] == "rate_limited" and lines[0]["level"] == "WARNING")
check("tracebacks go to the log file", "ValueError: boom" in lines[1]["traceback"])
check("setup is idempotent (no duplicate handlers)", len(logsetup.setup(lp).handlers) == len(lg.handlers))

print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
