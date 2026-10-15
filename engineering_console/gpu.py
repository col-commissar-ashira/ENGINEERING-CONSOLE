"""Vendor-independent GPU telemetry.

    GPUBackend
      +-- NvidiaBackend   (nvidia-smi, one long-lived process)
      +-- AmdBackend      (amdgpu sysfs / hwmon, no ROCm needed)
      +-- IntelBackend    (i915 / xe sysfs + hwmon)
      +-- NoToolsBackend  (card present but vendor tools missing -> N/A + reason)

Every backend returns the SAME normalized fields (FIELDS) for each GpuDevice. A
field is None when no reliable sensor exists - values are never guessed. The UI
only ever sees normalized data and never imports vendor code.
"""
import glob
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .platform_info import IS_WIN, hidden_subprocess_kwargs
from .sysfs import _read, _num, first

# util %, temp degC, vram GiB, clocks MHz, power W, fan %
FIELDS = ("util", "temp", "vram_used", "vram_total", "clock", "clock_max", "mem_clock", "power", "fan")
GIB = 2 ** 30

_PCI = None


def find_nvidia_smi():
    """nvidia-smi on PATH, or - on Windows, where the driver does not always add it - in its usual folders."""
    exe = shutil.which("nvidia-smi")
    if exe or not IS_WIN:
        return exe
    for base in (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
                 os.environ.get("ProgramFiles", r"C:\Program Files") + r"\NVIDIA Corporation\NVSMI"):
        cand = os.path.join(base, "nvidia-smi.exe")
        if os.path.exists(cand):
            return cand
    return None


def pci_names(root_cmd=("lspci", "-Dmm")):
    """slot -> (vendor, device) strings from lspci (cached; Linux only)."""
    global _PCI
    if _PCI is None:
        _PCI = {}
        if IS_WIN or not shutil.which(root_cmd[0]):
            return _PCI
        try:
            out = subprocess.run(list(root_cmd), capture_output=True, text=True, timeout=4).stdout
            for line in out.splitlines():
                p = shlex.split(line)
                if len(p) >= 4:
                    _PCI[p[0]] = (p[2], p[3])
        except Exception:
            pass
    return _PCI


def pci_model(slot, fallback):
    v = pci_names().get(slot)
    if not v:
        return fallback
    m = re.search(r"\[([^\]]+)\]\s*$", v[0])
    short = m.group(1) if m else v[0].split(",")[0]
    return f"{short} {v[1]}"


@dataclass
class GpuDevice:
    index: int = -1
    vendor: str = ""
    model: str = ""
    driver: str = ""
    pci: str = ""
    kind: str = "unknown"          # "discrete" | "integrated"
    backend: object = None
    ctx: dict = field(default_factory=dict)

    def read(self):
        """-> (fields dict, reasons dict). Never raises."""
        try:
            f, r = self.backend.read(self)
        except Exception as e:
            f, r = {}, {"_all": f"read error: {e}"}
        return f, r


class GPUBackend:
    vendor = "?"

    def detect(self):
        return []

    def read(self, dev):
        raise NotImplementedError

    def close(self):
        pass


# ---------------------------------------------------------------- NVIDIA
class NvidiaReader:
    """One long-lived `nvidia-smi -lms` process; a thread keeps the latest rows
    (a new process per sample would be wasteful, especially with many GPUs)."""
    Q = ["index", "name", "driver_version", "pci.bus_id", "utilization.gpu", "temperature.gpu",
         "memory.used", "memory.total", "clocks.gr", "clocks.max.gr", "clocks.mem", "power.draw", "fan.speed"]
    NAMES = ["idx", "name", "driver", "pci", "util", "temp", "mem_used", "mem_total",
             "clk", "clkmax", "mclk", "power", "fan"]

    def __init__(self, interval_ms=1000, exe="nvidia-smi"):
        self.exe = exe
        self.interval_ms = int(interval_ms)
        self.data = {}
        self.error = None
        self.proc = None
        self._stop = False
        self._restart = False
        threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None  # "[N/A]", "[Not Supported]", ...

    def _parse(self, line):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(self.Q):
            return
        row = dict(zip(self.NAMES, parts))
        for k in ("util", "temp", "mem_used", "mem_total", "clk", "clkmax", "mclk", "power", "fan"):
            row[k] = self._f(row[k])
        try:
            self.data[int(row["idx"])] = row
        except ValueError:
            pass

    def _run(self):
        while not self._stop:
            try:
                self.proc = subprocess.Popen(
                    [self.exe, "--query-gpu=" + ",".join(self.Q),
                     "--format=csv,noheader,nounits", "-lms", str(self.interval_ms)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                    **hidden_subprocess_kwargs())
                self.error = None
                for line in self.proc.stdout:
                    self._parse(line)
                self.proc.wait()
                if not self._restart:
                    self.error = "nvidia-smi exited (driver unloaded?)"
            except Exception as e:
                self.error = str(e)
            if self._stop:
                break
            time.sleep(0.2 if self._restart else 5)
            self._restart = False

    def set_interval(self, ms):
        ms = int(ms)
        if ms != self.interval_ms:
            self.interval_ms = ms
            self._restart = True
            self.stop_proc()

    def stop_proc(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def close(self):
        self._stop = True
        self.stop_proc()


class NvidiaBackend(GPUBackend):
    vendor = "NVIDIA"

    def __init__(self, interval_ms=1000):
        self.interval_ms = interval_ms
        self.reader = None

    def detect(self):
        exe = find_nvidia_smi()
        if not exe:
            return []
        self.reader = NvidiaReader(self.interval_ms, exe)
        t0 = time.time()
        while time.time() - t0 < 6 and not self.reader.data:   # many-GPU servers init slowly
            time.sleep(0.05)
        devs = [GpuDevice(vendor="NVIDIA", model=row["name"], driver=row["driver"], pci=row["pci"],
                          kind="discrete", backend=self, ctx={"idx": idx})
                for idx, row in sorted(self.reader.data.items())]
        if not devs:
            self.reader.close()
            self.reader = None
        return devs

    def set_interval(self, ms):
        if self.reader:
            self.reader.set_interval(ms)

    def read(self, dev):
        row = self.reader.data.get(dev.ctx["idx"]) if self.reader else None
        if not row:
            why = (self.reader.error if self.reader else None) or "nvidia-smi returned no data"
            return {}, {"_all": why}
        used, total = row["mem_used"], row["mem_total"]
        f = {"util": row["util"], "temp": row["temp"],
             "vram_used": used / 1024 if used is not None else None,
             "vram_total": total / 1024 if total else None,
             "clock": row["clk"], "clock_max": row["clkmax"], "mem_clock": row["mclk"],
             "power": row["power"], "fan": row["fan"]}
        r = {"power": "Power telemetry unavailable (GPU/driver does not report power draw)",
             "fan": "No fan sensor reported (laptop or chassis-managed cooling)"}
        return f, r

    def close(self):
        if self.reader:
            self.reader.close()


# ---------------------------------------------------------------- DRM helpers
def drm_cards(root, vendor_id):
    for card in sorted(glob.glob(f"{root}/class/drm/card[0-9]*")):
        if "-" in os.path.basename(card):          # skip connectors like card0-eDP-1
            continue
        if _read(card + "/device/vendor") == vendor_id:
            yield card


def card_slot(dev_dir):
    for line in (_read(dev_dir + "/uevent") or "").splitlines():
        if line.startswith("PCI_SLOT_NAME="):
            return line.split("=", 1)[1]
    return ""


def card_driver(dev_dir):
    p = os.path.realpath(dev_dir + "/driver")
    return os.path.basename(p) if os.path.exists(p) else ""


def hwmon_of(dev_dir):
    g = sorted(glob.glob(dev_dir + "/hwmon/hwmon*"))
    return g[0] if g else None


def _dpm_current(path):
    """Current clock from an amdgpu pp_dpm_* table (the line ending in '*')."""
    txt = _read(path)
    for line in (txt or "").splitlines():
        if line.rstrip().endswith("*"):
            m = re.search(r"(\d+)\s*[Mm][Hh]z", line)
            return float(m.group(1)) if m else None
    return None


# ---------------------------------------------------------------- AMD
class AmdBackend(GPUBackend):
    vendor = "AMD"

    def __init__(self, root="/sys"):
        self.root = root

    def detect(self):
        devs = []
        for card in drm_cards(self.root, "0x1002"):
            d = card + "/device"
            slot = card_slot(d)
            has_vram = _num(d + "/mem_info_vram_total") is not None
            devs.append(GpuDevice(vendor="AMD", model=pci_model(slot, "AMD GPU"), driver=card_driver(d) or "amdgpu",
                                  pci=slot, kind="discrete" if has_vram else "integrated", backend=self,
                                  ctx={"dev": d, "hwmon": hwmon_of(d)}))
        return devs

    def read(self, dev):
        d, hw = dev.ctx["dev"], dev.ctx["hwmon"]
        used, total = _num(d + "/mem_info_vram_used"), _num(d + "/mem_info_vram_total")
        f = {"util": _num(d + "/gpu_busy_percent"),
             "vram_used": used / GIB if used is not None else None,
             "vram_total": total / GIB if total else None}
        f["clock"] = first(_num(hw + "/freq1_input", 1e-6) if hw else None, _dpm_current(d + "/pp_dpm_sclk"))
        f["mem_clock"] = first(_num(hw + "/freq2_input", 1e-6) if hw else None, _dpm_current(d + "/pp_dpm_mclk"))
        f["clock_max"] = None
        f["temp"] = _num(hw + "/temp1_input", 1e-3) if hw else None
        f["power"] = first(_num(hw + "/power1_average", 1e-6), _num(hw + "/power1_input", 1e-6)) if hw else None
        fan = None
        if hw:
            pwm, pmax = _num(hw + "/pwm1"), _num(hw + "/pwm1_max") or 255
            rpm, rmax = _num(hw + "/fan1_input"), _num(hw + "/fan1_max")
            if pwm is not None:
                fan = pwm / pmax * 100
            elif rpm is not None and rmax:
                fan = rpm / rmax * 100
        f["fan"] = fan
        r = {"util": "amdgpu gpu_busy_percent not exposed (old kernel/driver?)",
             "vram_used": "mem_info_vram_* not exposed by this driver",
             "temp": "No hwmon temperature for this GPU",
             "power": "Power telemetry unavailable (no hwmon power1_* on this GPU)",
             "fan": "No fan percentage available (fan absent or RPM-only without a maximum)",
             "clock": "No GPU clock in hwmon or pp_dpm_sclk", "mem_clock": "No memory clock in hwmon or pp_dpm_mclk"}
        return f, r


# ---------------------------------------------------------------- Intel
class IntelBackend(GPUBackend):
    vendor = "Intel"

    def __init__(self, root="/sys"):
        self.root = root

    def detect(self):
        devs = []
        for card in drm_cards(self.root, "0x8086"):
            d = card + "/device"
            slot = card_slot(d)
            integrated = slot.endswith(":00:02.0") or slot.split(":")[-2:-1] == ["00"]
            devs.append(GpuDevice(vendor="Intel", model=pci_model(slot, "Intel graphics"), driver=card_driver(d) or "i915",
                                  pci=slot, kind="integrated" if integrated else "discrete", backend=self,
                                  ctx={"card": card, "dev": d, "hwmon": hwmon_of(d), "e_prev": None}))
        return devs

    def read(self, dev):
        card, d, hw = dev.ctx["card"], dev.ctx["dev"], dev.ctx["hwmon"]
        f = {k: None for k in FIELDS}
        for p in (card + "/gt_cur_freq_mhz", card + "/gt/gt0/rps_cur_freq_mhz", d + "/tile0/gt0/freq0/cur_freq"):
            v = _num(p)
            if v is not None:
                f["clock"] = v
                mx = _num(p.replace("cur_freq", "max_freq").replace("gt_cur_freq_mhz", "gt_max_freq_mhz")
                          .replace("rps_cur_freq_mhz", "rps_max_freq_mhz"))
                f["clock_max"] = mx
                break
        if hw:
            temps = sorted(glob.glob(hw + "/temp*_input"))
            f["temp"] = _num(temps[0], 1e-3) if temps else None
            f["power"] = _num(hw + "/power1_input", 1e-6)
            e = _num(hw + "/energy1_input")
            if f["power"] is None and e is not None:
                now, prev = time.monotonic(), dev.ctx["e_prev"]
                if prev and now > prev[0] and e >= prev[1]:
                    f["power"] = (e - prev[1]) / 1e6 / (now - prev[0])
                dev.ctx["e_prev"] = (now, e)
        integrated = dev.kind == "integrated"
        r = {"util": "Intel GPU load needs intel_gpu_top (sudo apt install intel-gpu-tools) with root/CAP_PERFMON",
             "temp": "Integrated Intel GPU has no separate temperature sensor (see CPU package temperature)"
                     if integrated else "No hwmon temperature exposed by this Intel GPU",
             "vram_used": "Integrated GPU shares system RAM (no dedicated VRAM)" if integrated
                          else "Intel driver does not expose VRAM usage via sysfs",
             "mem_clock": "Not exposed by the Intel driver",
             "power": "Power telemetry unavailable (no hwmon power/energy counter on this GPU)",
             "fan": "No fan sensor on this GPU",
             "clock": "No GT frequency file in sysfs"}
        return f, r


class NoToolsBackend(GPUBackend):
    """A GPU is present but its vendor tools/driver are missing: list it, show N/A with the reason."""
    def __init__(self, vendor, vendor_id, why, root="/sys"):
        self.vendor, self.vendor_id, self.why, self.root = vendor, vendor_id, why, root

    def detect(self):
        devs = []
        for card in drm_cards(self.root, self.vendor_id):
            slot = card_slot(card + "/device")
            devs.append(GpuDevice(vendor=self.vendor, model=pci_model(slot, f"{self.vendor} GPU"),
                                  driver=card_driver(card + "/device") or "unknown", pci=slot, kind="unknown", backend=self))
        return devs

    def read(self, dev):
        return {}, {"_all": self.why}


def detect_gpus(interval_ms=1000, root="/sys", mac=None):
    """-> (devices, backends). Index order: NVIDIA, AMD, Intel. On macOS: the Mac's GPUs via ioreg."""
    if mac is not None:
        from .macos import MacGpuBackend
        be = MacGpuBackend(mac)
        devs = be.detect()
        for i, dev in enumerate(devs):
            dev.index = i
        return devs, [be]
    backends, devices = [], []
    nv = NvidiaBackend(interval_ms)
    devs = nv.detect()
    if IS_WIN:                                  # NVIDIA via nvidia-smi; every other adapter via DXGI + GPU counters
        if devs:
            backends.append(nv)
            devices += devs
        else:
            nv.close()
        from .wingpu import WindowsGpuBackend
        wb = WindowsGpuBackend(skip_nvidia=bool(devs))
        wd = wb.detect()
        if wd:
            backends.append(wb)
            devices += wd
        for i, dev in enumerate(devices):
            dev.index = i
        return devices, backends
    if devs:
        backends.append(nv)
    else:
        nv.close()
        nt = NoToolsBackend("NVIDIA", "0x10de", "NVIDIA driver tools missing: install the proprietary driver "
                            "(Driver Manager) so nvidia-smi is available", root)
        devs = nt.detect()
        if devs:
            backends.append(nt)
    devices += devs
    for be in (AmdBackend(root), IntelBackend(root)):
        d = be.detect()
        if d:
            backends.append(be)
            devices += d
    for i, dev in enumerate(devices):
        dev.index = i
    return devices, backends
