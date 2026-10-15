"""macOS sensor layer. NOT TESTED ON REAL MACS YET - the parsers/decoders are unit-tested against
sample data, the live system calls are not. Every reader fails soft: on any problem the sensor
becomes N/A with a reason, it never raises into the UI and never guesses a value.

What needs which permission (shown to the user in the app):
  * no permission:   CPU/RAM/disk/network (psutil), GPU load + memory (ioreg), SoC temperatures on
                     Apple Silicon (IOHID sensors), fans + total system power (AppleSMC), battery.
  * administrator:   Apple's `powermetrics` (CPU/GPU power, GPU clock + residency, CPU clocks,
                     thermal pressure; on Intel Macs also CPU/GPU die temperature and fan RPM).
                     Started through the standard macOS password prompt, stops when the app quits.
  * microphone:      MIC INPUT meter (and AUDIO OUT via a loopback input such as BlackHole).
"""
import ctypes
import ctypes.util
import json
import os
import platform
import re
import struct
import subprocess
import sys
import threading
import time
from ctypes import (Structure, POINTER, byref, c_bool, c_double, c_int, c_int32, c_int64, c_long, c_size_t,
                    c_ubyte, c_uint16, c_uint32, c_void_p, c_char_p, sizeof)

import psutil

from .gpu import GpuDevice, GPUBackend
from .platform_info import cache_dir
from . import pmrule

GIB = 2 ** 30
ARM = platform.machine() == "arm64"
ADMIN_HINT = ("Needs administrator access: Tools > Enable advanced Mac sensors "
              "(run packaging/macos/enable_sensors.sh once to stop being asked at every launch)")
PRESSURE = {"nominal": 0, "light": 1, "moderate": 2, "heavy": 3, "trapping": 4, "sleeping": 4}


def _run(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


# ====================================================================== parsers (pure, unit-tested)
def last_sample(text):
    """The most recent COMPLETE powermetrics text sample (bounded by two sample headers)."""
    marks = [m.start() for m in re.finditer(r"\*\*\* Sampled system activity", text)]
    return text[marks[-2]:marks[-1]] if len(marks) >= 2 else ""


def parse_powermetrics(s):
    def num(pat, scale=1.0):
        m = re.search(pat, s, re.M | re.I)
        return float(m.group(1)) * scale if m else None
    out = {"cpu_power": num(r"^CPU Power:\s*([\d.]+)\s*mW", 1e-3), "gpu_power": num(r"^GPU Power:\s*([\d.]+)\s*mW", 1e-3),
           "gpu_freq": num(r"GPU HW active frequency:\s*([\d.]+)\s*MHz"),
           "gpu_util": num(r"GPU HW active residency:\s*([\d.]+)\s*%"),
           "fan_rpm": num(r"^Fan:\s*([\d.]+)\s*rpm"),
           "cpu_temp": num(r"CPU die temperature:\s*([\d.]+)\s*C"),
           "gpu_temp": num(r"GPU die temperature:\s*([\d.]+)\s*C")}
    if out["cpu_power"] is None:                         # Intel Macs report a package power estimate
        m = re.search(r"package power[^:\n]*:\s*([\d.]+)\s*(mW|W)\b", s, re.I)
        if m:
            out["cpu_power"] = float(m.group(1)) * (1e-3 if m.group(2).lower() == "mw" else 1.0)
    freqs = [float(x) for x in re.findall(r"[EP]\d*-Cluster HW active frequency:\s*([\d.]+)\s*MHz", s)]
    out["cpu_freq"] = max(freqs) if freqs else None
    m = re.search(r"Current pressure level:\s*(\w+)", s)
    out["pressure"] = m.group(1) if m else None
    return out


def parse_ioreg_accelerators(text):
    """-> list of {name, model, stats{}} from `ioreg -r -d 1 -w0 -c IOAccelerator`."""
    entries, cur = [], None
    for line in text.splitlines():
        if "+-o " in line:
            cur = {"name": line.split("+-o ", 1)[1].split("  <")[0].strip(), "lines": []}
            entries.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    out = []
    for e in entries:
        blob = "\n".join(e["lines"])
        stats = {}
        m = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}', blob)
        if m:
            stats = {k: int(v) for k, v in re.findall(r'"([^"]+)"=(-?\d+)', m.group(1))}
        mm = re.search(r'"model"\s*=\s*(?:<)?"([^"]+)"', blob)
        out.append({"name": e["name"], "model": mm.group(1) if mm else "", "stats": stats})
    return out


def parse_battery(text):
    """-> {'power_w', 'discharging'} from `ioreg -rn AppleSmartBattery -w0`, or {}."""
    def val(key):
        m = re.search(r'"%s"\s*=\s*(-?\d+)' % key, text)
        return int(m.group(1)) if m else None
    amp, volt = val("InstantAmperage"), val("Voltage")
    if amp is None:
        amp = val("Amperage")
    if amp is None or volt is None:
        return {}
    if amp > 2 ** 63:                                   # unsigned-wrapped negative current
        amp -= 2 ** 64
    return {"power_w": abs(amp) * volt / 1e6, "discharging": amp < 0}


# ====================================================================== AppleSMC (no root)
class _Vers(Structure):
    _fields_ = [("major", c_ubyte), ("minor", c_ubyte), ("build", c_ubyte), ("reserved", c_ubyte), ("release", c_uint16)]


class _PLimit(Structure):
    _fields_ = [("version", c_uint16), ("length", c_uint16), ("cpu", c_uint32), ("gpu", c_uint32), ("mem", c_uint32)]


class _KeyInfo(Structure):
    _fields_ = [("dataSize", c_uint32), ("dataType", c_uint32), ("dataAttributes", c_ubyte)]


class KeyData(Structure):
    _fields_ = [("key", c_uint32), ("vers", _Vers), ("pLimit", _PLimit), ("keyInfo", _KeyInfo), ("result", c_ubyte),
                ("status", c_ubyte), ("data8", c_ubyte), ("data32", c_uint32), ("bytes", c_ubyte * 32)]


def decode_smc(typ, data):
    """Decode an SMC value of the given 4-char type; None if the type is not understood."""
    try:
        if typ == "flt ":
            return struct.unpack("<f", data[:4])[0]
        if typ == "sp78":
            return int.from_bytes(data[:2], "big", signed=True) / 256.0
        if typ == "fpe2":
            return int.from_bytes(data[:2], "big") / 4.0
        if typ in ("ui8 ", "ui16", "ui32"):
            return float(int.from_bytes(data, "big"))
    except Exception:
        pass
    return None


class AppleSmc:
    """Reads SMC keys through IOKit (what smcFanControl/iStats do) - works without administrator rights."""
    def __init__(self):
        self.ok, self.why, self.conn = False, "", 0
        try:
            self.io = ctypes.CDLL(ctypes.util.find_library("IOKit"))
            self.libc = ctypes.CDLL(ctypes.util.find_library("System"))
            self.io.IOServiceMatching.restype = c_void_p
            self.io.IOServiceMatching.argtypes = [c_char_p]
            self.io.IOServiceGetMatchingService.restype = c_uint32
            self.io.IOServiceGetMatchingService.argtypes = [c_uint32, c_void_p]
            self.io.IOServiceOpen.argtypes = [c_uint32, c_uint32, c_uint32, POINTER(c_uint32)]
            self.io.IOConnectCallStructMethod.argtypes = [c_uint32, c_uint32, c_void_p, c_size_t, c_void_p, POINTER(c_size_t)]
            self.libc.mach_task_self.restype = c_uint32
            svc = self.io.IOServiceGetMatchingService(0, self.io.IOServiceMatching(b"AppleSMC"))
            if not svc:
                raise RuntimeError("AppleSMC service not found")
            conn = c_uint32(0)
            if self.io.IOServiceOpen(svc, self.libc.mach_task_self(), 0, byref(conn)) != 0:
                raise RuntimeError("cannot open AppleSMC")
            self.conn, self.ok = conn.value, True
        except Exception as e:
            self.why = f"AppleSMC unavailable: {e}"

    def _call(self, inp):
        out, osz = KeyData(), c_size_t(sizeof(KeyData))
        r = self.io.IOConnectCallStructMethod(self.conn, 2, byref(inp), sizeof(inp), byref(out), byref(osz))
        return out if r == 0 and out.result == 0 else None

    def read(self, key):
        if not self.ok:
            return None
        try:
            k = int.from_bytes(key.encode(), "big")
            a = KeyData()
            a.key, a.data8 = k, 9                                   # get key info
            info = self._call(a)
            if info is None:
                return None
            size, typ = info.keyInfo.dataSize, info.keyInfo.dataType.to_bytes(4, "big").decode("latin-1")
            b = KeyData()
            b.key, b.keyInfo.dataSize, b.data8 = k, size, 5          # read bytes
            out = self._call(b)
            return None if out is None else decode_smc(typ, bytes(out.bytes[:size]))
        except Exception:
            return None

    def fans(self):
        """-> (list of rpm, max rpm) for all fans, or ([], None)."""
        n = self.read("FNum")
        if not n:
            return [], None
        rpm, mx = [], []
        for i in range(int(n)):
            v, m = self.read(f"F{i}Ac"), self.read(f"F{i}Mx")
            if v is not None:
                rpm.append(v)
            if m:
                mx.append(m)
        return rpm, (max(mx) if mx else None)

    def intel_temps(self):
        cpu = [v for k in ("TC0P", "TC0D", "TC0H", "TC0E", "TCXC") if (v := self.read(k)) and 0 < v < 130]
        gpu = [v for k in ("TG0P", "TG0D", "TGDD") if (v := self.read(k)) and 0 < v < 130]
        return (max(cpu) if cpu else None), (max(gpu) if gpu else None)


# ====================================================================== IOHID temperature sensors (no root)
def classify_sensor(name):
    n = name.lower()
    if "nand" in n:
        return "ssd"
    if "gpu" in n:
        return "gpu"
    if "tdie" in n or "pacc" in n or "eacc" in n or "cpu" in n or n.startswith("soc"):
        return "cpu"
    if "gas gauge" in n or "battery" in n:
        return "battery"
    return None


class AppleHid:
    """Apple Silicon SoC/NAND temperatures through the IOHID event system (what `macmon`/Stats use)."""
    def __init__(self):
        self.ok, self.why, self.sensors = False, "", []
        try:
            cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
            io = ctypes.CDLL(ctypes.util.find_library("IOKit"))
            self.cf, self.io = cf, io
            cf.CFStringCreateWithCString.restype = c_void_p
            cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
            cf.CFNumberCreate.restype = c_void_p
            cf.CFNumberCreate.argtypes = [c_void_p, c_int, c_void_p]
            cf.CFDictionaryCreate.restype = c_void_p
            cf.CFDictionaryCreate.argtypes = [c_void_p, POINTER(c_void_p), POINTER(c_void_p), c_long, c_void_p, c_void_p]
            cf.CFArrayGetCount.restype = c_long
            cf.CFArrayGetCount.argtypes = [c_void_p]
            cf.CFArrayGetValueAtIndex.restype = c_void_p
            cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
            cf.CFStringGetCString.restype = c_bool
            cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
            cf.CFRelease.argtypes = [c_void_p]
            io.IOHIDEventSystemClientCreate.restype = c_void_p
            io.IOHIDEventSystemClientCreate.argtypes = [c_void_p]
            io.IOHIDEventSystemClientSetMatching.argtypes = [c_void_p, c_void_p]
            io.IOHIDEventSystemClientCopyServices.restype = c_void_p
            io.IOHIDEventSystemClientCopyServices.argtypes = [c_void_p]
            io.IOHIDServiceClientCopyProperty.restype = c_void_p
            io.IOHIDServiceClientCopyProperty.argtypes = [c_void_p, c_void_p]
            io.IOHIDServiceClientCopyEvent.restype = c_void_p
            io.IOHIDServiceClientCopyEvent.argtypes = [c_void_p, c_int64, c_int32, c_int64]
            io.IOHIDEventGetFloatValue.restype = c_double
            io.IOHIDEventGetFloatValue.argtypes = [c_void_p, c_int32]
            S = lambda s: cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)
            N = lambda v: cf.CFNumberCreate(None, 3, byref(c_int32(v)))
            keys = (c_void_p * 2)(S("PrimaryUsagePage"), S("PrimaryUsage"))
            vals = (c_void_p * 2)(N(0xFF00), N(5))
            kcb = ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
            vcb = ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
            match = cf.CFDictionaryCreate(None, keys, vals, 2, kcb, vcb)
            self.client = io.IOHIDEventSystemClientCreate(None)
            io.IOHIDEventSystemClientSetMatching(self.client, match)
            arr = io.IOHIDEventSystemClientCopyServices(self.client)
            if not arr:
                raise RuntimeError("no temperature sensors exposed on this Mac")
            prod = S("Product")
            for i in range(cf.CFArrayGetCount(arr)):
                svc = cf.CFArrayGetValueAtIndex(arr, i)
                p = io.IOHIDServiceClientCopyProperty(svc, prod)
                buf = ctypes.create_string_buffer(128)
                name = buf.value.decode(errors="ignore") if p and cf.CFStringGetCString(p, buf, 128, 0x08000100) else ""
                self.sensors.append((svc, name, classify_sensor(name)))
            self.ok = bool(self.sensors)
            if not self.ok:
                self.why = "no IOHID temperature sensors found"
        except Exception as e:
            self.why = f"IOHID sensors unavailable: {e}"

    def read(self):
        """-> {'cpu': max C, 'gpu': .., 'ssd': .., 'battery': ..}"""
        out = {}
        if not self.ok:
            return out
        try:
            for svc, name, cls in self.sensors:
                if not cls:
                    continue
                ev = self.io.IOHIDServiceClientCopyEvent(svc, 15, 0, 0)       # kIOHIDEventTypeTemperature
                if not ev:
                    continue
                v = self.io.IOHIDEventGetFloatValue(ev, 15 << 16)
                self.cf.CFRelease(ev)
                if 0 < v < 150:
                    out[cls] = max(out.get(cls, 0.0), v)
        except Exception:
            pass
        return out


# ====================================================================== powermetrics helper (needs admin)
class PowerMetrics:
    """Runs Apple's powermetrics as root (standard macOS password prompt) and reads its output file.
    A tiny shell watchdog stops it when Engineering Console exits. Nothing is installed anywhere."""
    def __init__(self):
        self.path = os.path.join(cache_dir(), "powermetrics.log")
        self.status = "off"          # off | asking | on | declined | failed
        self.detail = ""
        self._cache, self._cache_t, self._size = {}, 0.0, 0
        self._stop, self.proc = False, None

    def available(self):
        return os.path.exists("/usr/bin/powermetrics")

    def enable(self):
        if self.status in ("asking", "on"):
            return
        if not self.available():
            self.status, self.detail = "failed", "/usr/bin/powermetrics not found"
            return
        self.status = "asking"
        threading.Thread(target=self._start_auto, daemon=True).start()

    def sudo_allowed(self):
        """True if the one-time setup (packaging/macos/enable_sensors.sh) was done: powermetrics may run via sudo
        without a password. Then there is no prompt at all."""
        try:
            r = subprocess.run(["sudo", "-n", "-l", pmrule.POWERMETRICS] + pmrule.args(ARM), capture_output=True,
                               text=True, timeout=6)
            return r.returncode == 0
        except Exception:
            return False

    def _start_auto(self):
        if self.sudo_allowed():
            self._start_sudo()
        else:
            self._start()

    def _start_sudo(self):
        """No-prompt mode: run powermetrics in 30-second batches through `sudo -n`. If the app dies, the orphaned
        batch ends by itself within 30 seconds."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        open(self.path, "a").close()
        os.chmod(self.path, 0o600)
        self._stop, self.status, self.detail = False, "on", ""
        threading.Thread(target=self._sudo_loop, daemon=True, name="powermetrics").start()

    def _sudo_loop(self):
        quick = 0
        while not self._stop:
            t0 = time.monotonic()
            try:
                with open(self.path, "ab") as f:
                    self.proc = subprocess.Popen(["sudo", "-n", pmrule.POWERMETRICS] + pmrule.args(ARM), stdout=f,
                                                 stderr=subprocess.DEVNULL)
                    self.proc.wait()
            except Exception as e:
                self.status, self.detail = "failed", str(e)[:200]
                return
            quick = quick + 1 if time.monotonic() - t0 < 3 else 0
            if quick >= 3 and not self._stop:                 # keeps dying at once: rule removed or powermetrics refuses
                self.status, self.detail = "failed", "powermetrics keeps exiting (was the sudo rule removed?)"
                return

    def close(self):
        self._stop = True
        try:
            if getattr(self, "proc", None) and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def _start(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            open(self.path, "a").close()
            os.chmod(self.path, 0o600)
            samplers = "cpu_power,gpu_power,ane_power,thermal" if ARM else "cpu_power,gpu_power,smc,thermal"   # == pmrule.args
            inner = (f"/usr/bin/powermetrics -i 1000 --samplers {samplers} -f text >> '{self.path}' 2>/dev/null & "
                     f"PM=$!; while kill -0 {os.getpid()} 2>/dev/null; do sleep 2; done; kill $PM")
            cmd = f"/bin/sh -c \"{inner}\" > /dev/null 2>&1 &"
            script = 'do shell script "%s" with administrator privileges' % cmd.replace("\\", "\\\\").replace('"', '\\"')
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                self.status, self.detail = "on", ""
            elif "-128" in r.stderr or "canceled" in r.stderr.lower():
                self.status, self.detail = "declined", "administrator prompt was cancelled"
            else:
                self.status, self.detail = "failed", r.stderr.strip()[:200]
        except Exception as e:
            self.status, self.detail = "failed", str(e)

    def latest(self):
        """Parsed latest complete sample, {} if none yet. Re-parses at most once per second."""
        if self.status != "on":
            return {}
        now = time.monotonic()
        if now - self._cache_t < 1.0:
            return self._cache
        self._cache_t = now
        try:
            size = os.path.getsize(self.path)
            if size > 8_000_000:                                 # keep the log small (we own the file)
                open(self.path, "w").close()
            with open(self.path, "rb") as f:
                f.seek(max(0, size - 400_000))
                text = f.read().decode("utf-8", "ignore")
            sample = last_sample(text)
            if sample:
                self._cache = parse_powermetrics(sample)
        except Exception:
            pass
        return self._cache


# ====================================================================== GPU (ioreg, no root)
class IoregGpu:
    """`ioreg` accelerator statistics at 1 Hz in a background thread (no root needed)."""
    def __init__(self):
        self.entries, self._stop = [], False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop:
            txt = _run(["ioreg", "-r", "-d", "1", "-w0", "-c", "IOAccelerator"])
            if txt:
                self.entries = parse_ioreg_accelerators(txt)
            time.sleep(1.0)

    def close(self):
        self._stop = True


class MacGpuBackend(GPUBackend):
    vendor = "Apple"

    def __init__(self, mac):
        self.mac = mac

    def detect(self):
        devs = []
        try:
            data = json.loads(_run(["system_profiler", "SPDisplaysDataType", "-json"], 20))["SPDisplaysDataType"]
            for g in data:
                name = g.get("sppci_model") or g.get("_name") or "GPU"
                ven = g.get("spdisplays_vendor", "") + g.get("sppci_vendor", "")
                vendor = "Apple" if "apple" in ven.lower() or name.startswith("Apple") else \
                         "AMD" if "amd" in ven.lower() else "Intel" if "intel" in ven.lower() else \
                         "NVIDIA" if "nvidia" in ven.lower() else "GPU"
                unified = vendor == "Apple"
                devs.append(GpuDevice(vendor=vendor, model=name, driver="Metal", pci=g.get("sppci_bus", ""),
                                      kind="integrated" if (unified or vendor == "Intel") else "discrete",
                                      backend=self, ctx={"unified": unified, "cores": g.get("sppci_cores")}))
        except Exception:
            pass
        if not devs and ARM:
            devs.append(GpuDevice(vendor="Apple", model="Apple GPU", driver="Metal", kind="integrated", backend=self,
                                  ctx={"unified": True, "cores": None}))
        return devs

    def read(self, dev):
        m = self.mac
        idx = dev.index if dev.index >= 0 else 0
        acc = None
        for e in m.gpu.entries:                                   # match by model name, else by order
            if e["model"] and (e["model"] in dev.model or dev.model in e["model"]):
                acc = e
                break
        if acc is None and idx < len(m.gpu.entries):
            acc = m.gpu.entries[idx]
        st = acc["stats"] if acc else {}
        pm = m.pm.latest() if dev.ctx.get("unified") or True else {}
        util = pm.get("gpu_util")
        if util is None:
            util = next((st[k] for k in ("Device Utilization %", "GPU Activity(%)", "Renderer Utilization %") if k in st), None)
        used = total = None
        if dev.ctx["unified"]:
            if "In use system memory" in st:
                used, total = st["In use system memory"] / GIB, psutil.virtual_memory().total / GIB
        elif "vramUsedBytes" in st:
            used = st["vramUsedBytes"] / GIB
            total = (st["vramUsedBytes"] + st.get("vramFreeBytes", 0)) / GIB or None
        temps = m.hid.read()
        gt = temps.get("gpu")
        if gt is None and not ARM:
            gt = m.smc_gpu_temp()
        f = {"util": util, "temp": gt, "vram_used": used, "vram_total": total, "clock": pm.get("gpu_freq"),
             "clock_max": None, "mem_clock": None, "power": pm.get("gpu_power"), "fan": None}
        r = {"util": "GPU statistics not reported by ioreg on this Mac",
             "temp": "No GPU temperature sensor exposed on this Mac",
             "vram_used": "GPU memory statistics not reported by ioreg",
             "clock": ADMIN_HINT, "power": ADMIN_HINT,
             "mem_clock": "macOS does not report a memory clock", "fan": "Mac fans are system-wide (see FAN SPEED)"}
        return f, r


# ====================================================================== aggregator used by Telemetry
class MacSensors:
    def __init__(self):
        self.smc = AppleSmc()
        self.hid = AppleHid() if ARM else None
        if self.hid is None:
            class _NoHid:
                ok, why = False, "Intel Mac: temperatures come from AppleSMC / powermetrics"
                def read(self): return {}
            self.hid = _NoHid()
        self.pm = PowerMetrics()
        self.gpu = IoregGpu()
        self.batt, self._batt_t = {}, 0.0
        self.pressure = None

    def smc_gpu_temp(self):
        return self.smc.intel_temps()[1]

    def _battery(self):
        if time.monotonic() - self._batt_t > 2.0:
            self._batt_t = time.monotonic()
            self.batt = parse_battery(_run(["ioreg", "-rn", "AppleSmartBattery", "-w0"]))
        return self.batt

    def augment(self, d, reasons, floors):
        pm = self.pm.latest()
        temps = self.hid.read()
        # --- temperatures (no root on Apple Silicon; SMC on Intel; powermetrics as fallback)
        cpu_t = temps.get("cpu")
        if cpu_t is None and not ARM:
            cpu_t = self.smc.intel_temps()[0]
        d["cpu_temp"] = d.get("cpu_temp") if d.get("cpu_temp") is not None else (cpu_t if cpu_t is not None else pm.get("cpu_temp"))
        if d["cpu_temp"] is None:
            reasons["cpu_temp"] = ("SoC temperature sensors not found (" + (self.hid.why or "unknown") + ")") if ARM else ADMIN_HINT
        d["ssd_temp"] = temps.get("ssd") if d.get("ssd_temp") is None else d["ssd_temp"]
        if d["ssd_temp"] is None:
            reasons["ssd_temp"] = "No SSD temperature sensor exposed on this Mac"
        # --- fans + system power (AppleSMC, no root)
        rpm, mx = self.smc.fans()
        d["fan_rpm"] = max(rpm) if rpm else pm.get("fan_rpm")
        if mx:
            floors["fan_rpm"] = mx
        if d["fan_rpm"] is None:
            reasons["fan_rpm"] = ("This Mac has no fan (fanless MacBook Air)" if self.smc.ok and not rpm
                                  else (self.smc.why or ADMIN_HINT))
        sp = self.smc.read("PSTR")
        if sp is None or not (0 < sp < 400):
            b = self._battery()
            sp = b["power_w"] if b.get("discharging") else None
        d["sys_power"] = sp
        if sp is None:
            reasons["sys_power"] = "System power not exposed (AppleSMC PSTR missing and not on battery)"
        # --- powermetrics (admin)
        if pm.get("cpu_power") is not None:
            d["cpu_power"] = pm["cpu_power"]
        else:
            reasons["cpu_power"] = ADMIN_HINT if self.pm.status != "on" else "powermetrics did not report CPU power on this Mac"
        if pm.get("cpu_freq") is not None:
            d["cpu_freq"] = pm["cpu_freq"]
        self.pressure = pm.get("pressure")
        d["thermal_state"] = PRESSURE.get((self.pressure or "").lower())
        if d["thermal_state"] is None:
            reasons["thermal_state"] = ADMIN_HINT if self.pm.status != "on" else "thermal pressure not reported"

    def status_lines(self):
        yes = lambda ok: "available" if ok else "NOT available"
        pm = self.pm
        return [f"AppleSMC (fans, system power): {yes(self.smc.ok)} {self.smc.why}",
                f"IOHID temperature sensors: {yes(self.hid.ok)} {self.hid.why}",
                f"powermetrics helper (administrator): {pm.status} {pm.detail}",
                f"GPU statistics (ioreg): {len(self.gpu.entries)} accelerator(s) seen",
                "Microphone: allowed in System Settings > Privacy & Security > Microphone"]

    def report(self):
        return ["", "=== macOS SENSOR STATUS ==="] + self.status_lines()

    def close(self):
        self.gpu.close()
        self.pm.close()


def mac_diagnostics():
    """For `engineering-console --sensors` on a Mac: raw evidence to debug parsers."""
    m = MacSensors()
    time.sleep(1.5)
    print("\n".join(m.status_lines()))
    print("\n--- ioreg accelerators (parsed) ---")
    for e in m.gpu.entries:
        print(e["name"], "|", e["model"], "|", e["stats"])
    print("\n--- IOHID temperatures ---")
    print(m.hid.read())
    print("\n--- SMC ---")
    print("fans:", m.smc.fans(), " PSTR:", m.smc.read("PSTR"))
    print("\n--- battery ---")
    print(m._battery())
    print("\n(For admin sensors run the app, enable them in Tools, then re-run with: tail -c 3000", m.pm.path, ")")
    m.close()
