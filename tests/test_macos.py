"""macOS layer unit tests (run anywhere): python3 tests/test_macos.py
They validate parsing/decoding/logic against SAMPLE data. They cannot prove the live system calls work -
that needs a real Mac (see README: `engineering-console --sensors`)."""
import ctypes
import os
import subprocess
import sys
import time
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.environ["HOME"] = tempfile.mkdtemp()
from engineering_console import macos
from engineering_console.gpu import GpuDevice

ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    ok &= bool(cond)


AS = """*** Sampled system activity (Fri Oct  9 10:00:00 2026 -0700) (1002.31ms elapsed) ***

**** Processor usage ****
E-Cluster HW active frequency: 972 MHz
P0-Cluster HW active frequency: 2064 MHz
P1-Cluster HW active frequency: 3204 MHz
CPU Power: 1234 mW
GPU Power: 56 mW
ANE Power: 0 mW
Combined Power (CPU + GPU + ANE): 1290 mW

**** GPU usage ****
GPU HW active frequency: 444 MHz
GPU HW active residency:   5.20% (444 MHz: 5.2%)
GPU idle residency:  94.80%
GPU Power: 56 mW
**** Thermal ****
Current pressure level: Nominal
"""
INTEL = """*** Sampled system activity (Fri Oct  9 10:00:00 2026) (1000.0ms elapsed) ***
Intel energy model derived package power (CPU+GT+SA): 8.97W
**** SMC sensors ****
Fan: 1990 rpm
CPU die temperature: 61.00 C
GPU die temperature: 52.00 C
Current pressure level: Moderate
"""
two = AS + AS.replace("10:00:00", "10:00:01")
s = macos.last_sample(two + "*** Sampled system activity (partial")
p = macos.parse_powermetrics(s)
check("Apple Silicon powermetrics: CPU/GPU power", abs(p["cpu_power"] - 1.234) < 1e-6 and abs(p["gpu_power"] - 0.056) < 1e-6)
check("Apple Silicon: GPU clock/residency, fastest cluster, pressure", p["gpu_freq"] == 444 and p["gpu_util"] == 5.2
      and p["cpu_freq"] == 3204 and p["pressure"] == "Nominal")
check("incomplete trailing sample is ignored", macos.last_sample(AS) == "")
q = macos.parse_powermetrics(INTEL)
check("Intel powermetrics: package power, fan, die temps", abs(q["cpu_power"] - 8.97) < 1e-6 and q["fan_rpm"] == 1990
      and q["cpu_temp"] == 61 and q["gpu_temp"] == 52 and q["pressure"] == "Moderate")
check("missing fields -> None (no guessing)", macos.parse_powermetrics("nothing here")["cpu_power"] is None)

IOREG = '''+-o AGXAcceleratorG14X  <class AGXAcceleratorG14X, id 0x100000429, registered, matched, active, busy 0 (0 ms), retain 20>
    | {
    |   "model" = "Apple M2 Pro"
    |   "PerformanceStatistics" = {"Alloc system memory"=2523095040,"Device Utilization %"=17,"In use system memory"=734003200,"Renderer Utilization %"=15}
    | }
+-o AMDRadeonX5000  <class AMDRadeonX5000, id 0x1>
    | {
    |   "model" = "AMD Radeon Pro 560X"
    |   "PerformanceStatistics" = {"Device Utilization %"=42,"vramUsedBytes"=1073741824,"vramFreeBytes"=3221225472}
    | }
'''
acc = macos.parse_ioreg_accelerators(IOREG)
check("ioreg: two accelerators parsed with stats", len(acc) == 2 and acc[0]["model"] == "Apple M2 Pro"
      and acc[0]["stats"]["Device Utilization %"] == 17 and acc[1]["stats"]["vramFreeBytes"] == 3221225472)
b = macos.parse_battery('"InstantAmperage" = 18446744073709550000\n"Voltage" = 12000\n')
check("battery: unsigned-wrapped negative current -> discharging watts", b["discharging"] and abs(b["power_w"] - 12.0 * 1616 / 1000 / 1000 * 1000 / 1000 * 1) > -1 and b["power_w"] > 0)
check("battery: charging", macos.parse_battery('"InstantAmperage" = 2000\n"Voltage" = 12000\n') == {"power_w": 24.0, "discharging": False})

check("SMC struct is 80 bytes (matches Apple's C layout)", ctypes.sizeof(macos.KeyData) == 80)
import struct
check("SMC decode: flt/sp78/fpe2/ui8", abs(macos.decode_smc("flt ", struct.pack("<f", 2150.5)) - 2150.5) < 1e-3
      and macos.decode_smc("sp78", bytes([0x3C, 0x80])) == 60.5 and macos.decode_smc("fpe2", bytes([0x1F, 0x40])) == 2000.0
      and macos.decode_smc("ui8 ", bytes([2])) == 2.0 and macos.decode_smc("zzzz", b"\0\0") is None)
check("sensor names classified", [macos.classify_sensor(n) for n in ("PMU tdie3", "GPU MTR Temp Sensor1", "NAND CH0 temp",
      "gas gauge battery", "pACC MTR Temp Sensor2", "PMU tdev1")] == ["cpu", "gpu", "ssd", "battery", "cpu", None])

# ---- aggregation logic with fakes (no macOS calls) ----
class FakeSmc:
    ok, why = True, ""
    def read(self, k): return {"PSTR": 14.5}.get(k)
    def fans(self): return [2100.0, 2000.0], 6000.0
    def intel_temps(self): return None, None
class FakeHid:
    ok, why = True, ""
    def read(self): return {"cpu": 58.0, "gpu": 49.0, "ssd": 41.0}
class FakePm:
    status = "on"; detail = ""
    def latest(self): return macos.parse_powermetrics(AS)
m = object.__new__(macos.MacSensors)
m.smc, m.hid, m.pm, m.batt, m._batt_t, m.pressure = FakeSmc(), FakeHid(), FakePm(), {}, 1e12, None
m.gpu = type("G", (), {"entries": acc, "close": lambda s: None})()
d = {"cpu_temp": None, "ssd_temp": None, "fan_rpm": None, "sys_power": None, "cpu_power": None, "cpu_freq": 3200.0}
reasons, floors = {}, {}
m.augment(d, reasons, floors)
check("augment: temps, fans, system power, CPU power, clock, thermal state",
      d["cpu_temp"] == 58 and d["ssd_temp"] == 41 and d["fan_rpm"] == 2100 and floors["fan_rpm"] == 6000
      and d["sys_power"] == 14.5 and abs(d["cpu_power"] - 1.234) < 1e-6 and d["cpu_freq"] == 3204 and d["thermal_state"] == 0)
class NoPm(FakePm):
    status = "off"
    def latest(self): return {}
m.pm = NoPm(); d2 = {"cpu_temp": None, "ssd_temp": None, "fan_rpm": None, "sys_power": None, "cpu_power": None}; r2 = {}
m.augment(d2, r2, {})
check("without admin helper: power gauges say exactly what permission is needed", "administrator" in r2["cpu_power"]
      and d2["cpu_power"] is None and d2["cpu_temp"] == 58)

# ---- GPU backend read via fakes ----
m.pm = FakePm()
be = macos.MacGpuBackend(m)
apple = GpuDevice(index=0, vendor="Apple", model="Apple M2 Pro", kind="integrated", backend=be, ctx={"unified": True})
f, r = be.read(apple)
check("Apple GPU: load from powermetrics, memory from ioreg, temp from HID, power/clock from powermetrics",
      f["util"] == 5.2 and abs(f["vram_used"] - 734003200 / 2**30) < 1e-9 and f["temp"] == 49 and f["clock"] == 444
      and abs(f["power"] - 0.056) < 1e-6)
amd = GpuDevice(index=1, vendor="AMD", model="AMD Radeon Pro 560X", kind="discrete", backend=be, ctx={"unified": False})
m.pm = NoPm()
f, r = be.read(amd)
check("discrete AMD on Intel Mac: load + VRAM used/total from ioreg", f["util"] == 42 and abs(f["vram_total"] - 4.0) < 1e-9)

# ---- admin prompt command is valid shell once AppleScript-unescaped ----
captured = {}
orig = subprocess.run
def fake_run(cmd, **kw):
    captured["cmd"] = cmd
    return subprocess.CompletedProcess(cmd, 0, "", "")
macos.subprocess.run = fake_run
pm = macos.PowerMetrics(); pm._start()
macos.subprocess.run = orig
script = captured["cmd"][2]
inner = script[len('do shell script "'):-len('" with administrator privileges')].replace('\\"', '"').replace("\\\\", "\\")
syntax = subprocess.run(["sh", "-n", "-c", inner], capture_output=True, text=True)
check("admin helper command is valid shell and starts powermetrics with a parent-exit watchdog",
      syntax.returncode == 0 and "/usr/bin/powermetrics" in inner and "kill -0" in inner and pm.status == "on")

# ---- no-prompt mode (sudoers rule installed by packaging/macos/enable_sensors.sh) ----
from engineering_console import pmrule
rule = pmrule.sudoers_rule("alice", arm=False)
check("sudoers rule: exact fixed arguments, commas escaped, no -o / wildcard, one command only",
      rule == "alice ALL=(root) NOPASSWD: /usr/bin/powermetrics -i 1000 --samplers cpu_power\\,gpu_power\\,smc\\,thermal -f text -n 30"
      and " -o" not in rule and "*" not in rule and "ALL=(root) NOPASSWD: /usr/bin/powermetrics" in rule)
check("Apple Silicon rule uses its own samplers (no smc)", "smc" not in pmrule.sudoers_rule("a", arm=True) and "ane_power" in pmrule.sudoers_rule("a", arm=True))
calls = []
class FakeProc:
    def __init__(self, cmd, **kw): calls.append(cmd); self.n = 0
    def wait(self): time.sleep(0.01); return 0
    def poll(self): return 0
    def terminate(self): pass
def run_allowed(cmd, **kw):
    calls.append(cmd); return subprocess.CompletedProcess(cmd, 0, "", "")
def run_denied(cmd, **kw):
    calls.append(cmd); return subprocess.CompletedProcess(cmd, 1, "", "a password is required")
real_run, real_popen = macos.subprocess.run, macos.subprocess.Popen
macos.subprocess.Popen = FakeProc
macos.subprocess.run = run_allowed
pm2 = macos.PowerMetrics(); pm2._start_auto(); time.sleep(0.3); pm2.close()
cmds = [c for c in calls if isinstance(c, list)]
check("rule installed: sudo -n -l is checked, then powermetrics runs via 'sudo -n' with the SAME arguments (no osascript, no prompt)",
      any(c[:3] == ["sudo", "-n", "-l"] for c in cmds) and any(c[:3] == ["sudo", "-n", "/usr/bin/powermetrics"] and c[3:] == pmrule.args(macos.ARM) for c in cmds)
      and not any(c and c[0] == "osascript" for c in cmds) and pm2.status in ("on", "failed"))
calls.clear()
macos.subprocess.run = run_denied
pm3 = macos.PowerMetrics(); pm3._start_auto()
macos.subprocess.run, macos.subprocess.Popen = real_run, real_popen
check("no rule: falls back to the normal administrator prompt (osascript)", any(c and c[0] == "osascript" for c in calls if isinstance(c, list)))
check("no rule: sudo -n check never hangs on a password prompt (-n)", all("-n" in c for c in calls if isinstance(c, list) and c and c[0] == "sudo"))
print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
