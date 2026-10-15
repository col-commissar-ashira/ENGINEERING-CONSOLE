"""Telemetry collection for Engineering Console (Linux, Windows, macOS; Qt-free).

Every metric is returned as a float or None. None means "N/A"; the reason is
stored in Telemetry.reasons[key]. Nothing in here may raise to the caller.
"""
import glob
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

import psutil


from .sysfs import _read, _num
from .gpu import detect_gpus, FIELDS
from .audio import AudioMeter, FLOOR_DB, make_meters
from . import logsetup
from .platform_info import IS_MAC, IS_WIN, system_root
from .speedtest import SpeedController, DEFAULT_ENDPOINTS


@dataclass
class MetricDef:
    key: str
    label: str
    unit: str
    group: str
    lo: float = 0.0
    hi: float = 100.0
    auto: bool = False        # auto-scale the maximum from observed history
    floor: float = 0.0        # smallest maximum when auto-scaling
    warn: Optional[float] = None
    crit: Optional[float] = None
    dec: int = 0


M = MetricDef
METRICS = [
    M("cpu_util", "CPU LOAD", "%", "CPU", warn=85, crit=95),
    M("cpu_temp", "CPU TEMP", "°C", "Temperature", warn=80, crit=92),
    M("cpu_cores", "CORE ARRAY", "%", "CPU", warn=85, crit=95),
    M("cpu_freq", "CPU CLOCK", "MHz", "CPU", auto=True, floor=1000),
    M("cpu_power", "CPU POWER", "W", "Power", auto=True, floor=15, dec=1),
    M("load1", "LOAD AVG 1m", "", "CPU", auto=True, floor=4, dec=2),
    M("procs", "PROCESSES", "", "CPU", auto=True, floor=400),
    M("ram_pct", "RAM", "%", "RAM", warn=80, crit=92),
    M("ram_used", "RAM USED", "GB", "RAM", auto=True, floor=4, dec=2),
    M("ram_avail", "RAM FREE", "GB", "RAM", auto=True, floor=4, dec=2),
    M("app_ram", "THIS APP RAM", "MB", "RAM", auto=True, floor=200, dec=0),
    M("ram_used_noapp", "RAM USED (NO APP)", "GB", "RAM", auto=True, floor=4, dec=2),
    M("swap_pct", "SWAP", "%", "RAM", warn=50, crit=80),
    M("swap_used", "SWAP USED", "GB", "RAM", auto=True, floor=2, dec=2),
    M("disk_read", "DISK READ", "MB/s", "Disk", auto=True, floor=50, dec=1),
    M("disk_write", "DISK WRITE", "MB/s", "Disk", auto=True, floor=50, dec=1),
    M("disk_util", "DISK BUSY", "%", "Disk", warn=85, crit=98),
    M("disk_pct", "DISK USED", "%", "Disk", warn=85, crit=95),
    M("disk_iops", "DISK IOPS", "IO/s", "Disk", auto=True, floor=500),
    M("ssd_temp", "SSD TEMP", "°C", "Temperature", warn=65, crit=75),
    M("net_down", "NET DOWN", "MB/s", "Network", auto=True, floor=1, dec=2),
    M("net_up", "NET UP", "MB/s", "Network", auto=True, floor=1, dec=2),
    M("net_rx_total", "TOTAL DOWN", "GB", "Network", auto=True, floor=1, dec=2),
    M("net_tx_total", "TOTAL UP", "GB", "Network", auto=True, floor=1, dec=2),
    M("net_link", "LINK SPEED", "Mbps", "Network", auto=True, floor=100),
    M("fan_rpm", "FAN SPEED", "RPM", "Fans", hi=6000, auto=True, floor=3000),
    M("sys_power", "SYSTEM POWER", "W", "Power", auto=True, floor=30, dec=1),
    M("audio_out", "AUDIO OUT", "dB", "Audio", lo=-60, hi=0, warn=-6, crit=-1),
    M("audio_in", "MIC INPUT", "dB", "Audio", lo=-60, hi=0, warn=-6, crit=-1),
    M("speed_test", "INTERNET TEST", "MB/s", "Network", auto=True, floor=12.5, dec=1),
    M("fps", "FPS", "fps", "Game", hi=144, auto=True, floor=60),
]
METRIC_BY_KEY = {m.key: m for m in METRICS}


def build_metrics(gpus):
    """Static metrics + one independent set per detected GPU (any number, 0..N)."""
    ms = list(METRICS)
    for g in gpus:
        i, grp = g.index, f"GPU {g.index}"
        ms += [
            MetricDef(f"gpu{i}_panel", f"GPU {i}", "%", grp, warn=90, crit=98),
            MetricDef(f"gpu{i}_util", f"GPU{i} LOAD", "%", grp, warn=90, crit=98),
            MetricDef(f"gpu{i}_temp", f"GPU{i} TEMP", "°C", grp, warn=75, crit=85),
            MetricDef(f"gpu{i}_vram_pct", f"GPU{i} VRAM", "%", grp, warn=85, crit=95),
            MetricDef(f"gpu{i}_vram_used", f"GPU{i} VRAM USED", "GB", grp, auto=True, floor=2, dec=2),
            MetricDef(f"gpu{i}_clock", f"GPU{i} CLOCK", "MHz", grp, auto=True, floor=1000),
            MetricDef(f"gpu{i}_mem_clock", f"GPU{i} MEM CLOCK", "MHz", grp, auto=True, floor=3000),
            MetricDef(f"gpu{i}_power", f"GPU{i} POWER", "W", grp, auto=True, floor=25, dec=1),
            MetricDef(f"gpu{i}_fan", f"GPU{i} FAN", "%", grp),
        ]
    if IS_MAC:     # Apple's thermal pressure level (0 Nominal ... 4 Trapping): the Mac's "about to throttle" signal
        ms.append(MetricDef("thermal_state", "THERMAL STATE", "", "Power", lo=0, hi=4, warn=2, crit=3))
    return ms

_WIN_HW = ("Windows does not give normal programs this reading. Run LibreHardwareMonitor (as administrator) "
           "and Engineering Console will read it from there")
NA_DEFAULT = {
    "cpu_temp": _WIN_HW if IS_WIN else "No CPU temperature sensor in /sys/class/hwmon (try: sudo apt install lm-sensors && sudo sensors-detect)",
    "cpu_power": _WIN_HW if IS_WIN else "Intel RAPL energy counter not readable (kernel restricts it to root). Run install.sh --enable-rapl",
    "fan_rpm": _WIN_HW if IS_WIN else "Kernel exposes no fan tachometer on this laptop (check 'sensors'; HP often hides it in firmware)",
    "ssd_temp": _WIN_HW if IS_WIN else "No NVMe/drivetemp hwmon sensor found (try: sudo modprobe drivetemp)",
    "load1": "Windows has no load average" if IS_WIN else "Load average not available on this system",
    "sys_power": "Only available from the battery sensor while discharging; no wall-power sensor exists",
    "fps": "FPS needs a game overlay hook (e.g. MangoHud) - not integrated yet",
    "net_link": "Link speed unknown (normal for Wi-Fi; Ethernet usually reports it)",
    "disk_util": "Kernel did not report disk busy-time",
}


def app_memory(proc):
    """Memory of this app plus its helper processes (nvidia-smi, powermetrics ...), in bytes.
    Prefers USS (memory that would be freed if the process exited); falls back to RSS per process and says so."""
    total, kinds, n = 0, set(), 0
    try:
        procs = [proc] + proc.children(recursive=True)
    except psutil.Error:
        procs = [proc]
    for p in procs:
        try:
            uss = getattr(p.memory_full_info(), "uss", None)
            if uss is not None:
                total, n = total + uss, n + 1
                kinds.add("USS")
                continue
        except (psutil.Error, NotImplementedError, AttributeError, OSError):
            pass
        try:
            total, n = total + p.memory_info().rss, n + 1
            kinds.add("RSS")
        except psutil.Error:
            continue
    return total, ("USS" if kinds == {"USS"} else "USS+RSS" if "USS" in kinds else "RSS"), n


def ram_numbers(vm):
    """The ONE definition of RAM use on every OS:  USED = TOTAL physical RAM - AVAILABLE RAM."""
    return vm.total, vm.available, vm.total - vm.available


def ram_detail_text(vm, app_bytes, app_kind, app_n):
    """Per-OS breakdown shown in tooltips. These OS-specific numbers are information only; the gauges use
    ram_numbers(). psutil's own `used` differs between Linux, Windows and macOS, which is why it is not used."""
    g = 2 ** 30
    total, avail, used = ram_numbers(vm)
    lines = [f"Total physical RAM: {total / g:.2f} GB   Available: {avail / g:.2f} GB",
             f"USED = total - available = {used / g:.2f} GB  (same definition on every OS)",
             f"This app{'' if app_n <= 1 else ' + ' + str(app_n - 1) + ' helper process(es)'}: {app_bytes / 2**20:.0f} MB ({app_kind})"]
    if app_kind != "USS":
        lines[-1] += " - USS not readable here, RSS may overstate shared memory"
    lines.append(f"USED without this app: {(used - app_bytes) / g:.2f} GB")
    extra = [(f, getattr(vm, f)) for f in vm._fields if f not in ("total", "available", "percent")]
    if extra:
        lines.append(f"{platform.system() or 'OS'} details (informational): " + ", ".join(
            f"{f} {v / g:.2f} GB" for f, v in extra))
    return "\n".join(lines)


def busiest_interface(counters, stats):
    """Network interface carrying the most traffic that is up and not a loopback (used when no default route is known)."""
    best, score = None, -1
    for name, c in counters.items():
        st = stats.get(name)
        if st is not None and not st.isup:
            continue
        if name == "lo" or "loopback" in name.lower():
            continue
        if c.bytes_recv + c.bytes_sent > score:
            best, score = name, c.bytes_recv + c.bytes_sent
    return best


class Telemetry:
    def __init__(self, nv_interval_ms=1000, speed_endpoints=None, speed_size="quick"):
        self.reasons = {}
        self.floors = {}
        self.cores = []
        self.cores_n = psutil.cpu_count(logical=True) or 1
        psutil.cpu_percent(percpu=True)  # prime
        self._t = time.monotonic()
        self._net_prev = None
        self._disk_prev = None
        self._rapl_prev = None
        self._slow_t = 0.0
        self._slow = {}
        self.iface = None
        self.rapl = self._find_rapl()
        self.bats = glob.glob("/sys/class/power_supply/BAT*")
        self._me = psutil.Process()
        self._err_seen = set()
        self.app_mem = (0, "RSS", 1)                     # (bytes, kind, processes) of this app + its helpers
        self.ram_detail = ""
        self.winhw = None
        if IS_WIN:
            from .winhw import WinHwMonitor
            self.winhw = WinHwMonitor()
        self.mac = None
        if IS_MAC:
            from .macos import MacSensors
            self.mac = MacSensors()
            self.gpus, self.backends = detect_gpus(nv_interval_ms, mac=self.mac)
        else:
            self.gpus, self.backends = detect_gpus(nv_interval_ms)
        self.metrics = build_metrics(self.gpus)
        self.mby = {m.key: m for m in self.metrics}
        self.gpu_last = {}
        self.audio_out, self.audio_in = make_meters()
        self.speed = SpeedController(tuple(speed_endpoints or DEFAULT_ENDPOINTS), speed_size)
        self.info = self._detect_info()

    # ---------- detection ----------
    @staticmethod
    def _find_rapl():
        for d in sorted(glob.glob("/sys/class/powercap/intel-rapl:*")):
            name = _read(d + "/name")
            if name and name.startswith("package"):
                return d
        return None

    def _detect_info(self):
        cpu = platform.processor() or "CPU"
        try:
            if IS_MAC:
                cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                     timeout=4).stdout.strip() or "Mac"
            elif IS_WIN:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                    cpu = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
            else:
                for line in open("/proc/cpuinfo"):
                    if line.startswith("model name"):
                        cpu = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
        return {"cpu": cpu, "ram_gb": psutil.virtual_memory().total / 2**30}

    def set_nv_interval(self, ms):
        for b in self.backends:
            if hasattr(b, "set_interval"):
                b.set_interval(ms)

    def set_audio(self, out_on, in_on):
        for m, on in ((self.audio_out, out_on), (self.audio_in, in_on)):
            m.start() if on else m.stop()

    def gpu_fields(self, i):
        return self.gpu_last.get(i, ({}, {}))

    def close(self):
        self.speed.close()
        if self.winhw:
            self.winhw.close()
        for b in self.backends:
            b.close()
        self.audio_out.stop()
        self.audio_in.stop()
        if self.mac:
            self.mac.close()

    # ---------- helpers ----------
    @staticmethod
    def _pick_temp(temps, names, prefer=()):
        for n in names:
            ents = [e for e in temps.get(n, []) if e.current is not None]
            if not ents:
                continue
            for e in ents:
                if any(p.lower() in (e.label or "").lower() for p in prefer):
                    return e.current
            return max(e.current for e in ents)
        return None

    def _slow_update(self, now):
        s = self._slow
        s["procs"] = len(psutil.pids())
        self.app_mem = app_memory(self._me)
        try:
            u = psutil.disk_usage(system_root())
            s["disk_pct"] = u.percent
        except Exception as e:
            s["disk_pct"] = None
            self._note("disk_pct", e)
        # default-route interface
        iface = None
        try:
            best = None
            for line in open("/proc/net/route").read().splitlines()[1:]:
                f = line.split()
                if f[1] == "00000000" and (best is None or int(f[6]) < best[0]):
                    best = (int(f[6]), f[0])
            iface = best[1] if best else None
        except Exception:
            pass
        if iface is None:
            try:
                iface = busiest_interface(psutil.net_io_counters(pernic=True), psutil.net_if_stats())
            except Exception:
                pass
        self.iface = iface
        try:
            st = psutil.net_if_stats().get(iface)
            s["net_up_flag"] = bool(st and st.isup)
            s["net_link"] = float(st.speed) if st and st.speed > 0 else None
        except Exception:
            s["net_up_flag"], s["net_link"] = False, None
        try:
            fans = psutil.sensors_fans()
            vals = [f.current for lst in fans.values() for f in lst if f.current is not None]
            s["fan_rpm"] = float(max(vals)) if vals else None
        except Exception as e:
            s["fan_rpm"] = None
            self._note("fan_rpm", e)
        s["sys_power"] = None
        for b in self.bats:
            status = _read(b + "/status")
            p = _num(b + "/power_now", 1e-6)
            if p is None:
                i, v = _num(b + "/current_now"), _num(b + "/voltage_now")
                p = i * v * 1e-12 if i and v else None
            if status == "Discharging" and p:
                s["sys_power"] = p
            else:
                self.reasons["sys_power"] = f"Battery sensor is only meaningful while discharging (status: {status})"

    # ---------- errors become reasons (never silent) ----------
    def _log_error(self, where, exc):
        """Log a failure with its traceback once per (place, error type), so a 1 Hz failure cannot flood the log."""
        key = (where, type(exc).__name__)
        if key not in self._err_seen:
            self._err_seen.add(key)
            logsetup.event(logging.getLogger("engineering_console.sensors"), "sensor.read_failed", logging.WARNING,
                           exc_info=(type(exc), exc, exc.__traceback__), where=where, error=repr(exc))

    def _note(self, key, exc):
        """A sensor read raised: remember WHY for the gauge (unless the OS simply does not offer that API)."""
        if isinstance(exc, (AttributeError, NotImplementedError)):
            return
        self.reasons[key] = f"Read failed ({type(exc).__name__}: {str(exc)[:100]})"
        self._log_error(key, exc)

    # ---------- main poll ----------
    def poll(self):
        now = time.monotonic()
        dt = max(now - self._t, 1e-3)
        self._t = now
        d = {}
        self.floors = {}
        for k in [k for k, r in self.reasons.items() if isinstance(r, str) and r.startswith(("Read failed", "Sensor read failed"))]:
            del self.reasons[k]                       # stale error text must not outlive the error
        err = None
        try:
            self._poll(d, now, dt)
        except Exception as e:  # never crash the UI - but say what broke
            err = f"Sensor read failed ({type(e).__name__}: {str(e)[:100]})"
            self.reasons["_error"] = repr(e)
            self._log_error("poll", e)
        for m in self.metrics:
            if d.get(m.key) is None:
                d[m.key] = None
                if m.key not in self.reasons:
                    self.reasons[m.key] = err or NA_DEFAULT.get(m.key, "Not exposed by this system")
            else:
                self.reasons.pop(m.key, None)
        return d

    def _poll(self, d, now, dt):
        if now - self._slow_t > 2.0:
            self._slow_t = now
            try:
                self._slow_update(now)
            except Exception as e:
                self._log_error("slow_update", e)
        s = self._slow
        # CPU
        self.cores = psutil.cpu_percent(percpu=True) or []
        if self.cores:
            d["cpu_util"] = sum(self.cores) / len(self.cores)
            d["cpu_cores"] = d["cpu_util"]
        try:
            f = psutil.cpu_freq()
            if f:
                d["cpu_freq"] = f.current
                if f.max:
                    self.floors["cpu_freq"] = f.max
        except Exception as e:
            self._note("cpu_freq", e)
        if not IS_WIN:                                       # Windows has no load average (psutil only emulates one)
            try:
                d["load1"] = os.getloadavg()[0]
                self.floors["load1"] = float(self.cores_n)
            except Exception as e:
                self._note("load1", e)
        d["procs"] = s.get("procs")
        # temperatures
        try:
            temps = psutil.sensors_temperatures()          # absent on Windows: handled below
        except Exception:
            temps = {}
        d["cpu_temp"] = self._pick_temp(
            temps, ("coretemp", "k10temp", "zenpower", "cpu_thermal", "x86_pkg_temp", "acpitz"),
            prefer=("package", "tctl", "tdie"))
        d["ssd_temp"] = self._pick_temp(temps, ("nvme", "drivetemp"), prefer=("composite",))
        if self.winhw:                                       # LibreHardwareMonitor / OpenHardwareMonitor via WMI, if running
            hw, why = self.winhw.read()
            for k in ("cpu_temp", "ssd_temp", "cpu_power", "fan_rpm"):
                if hw.get(k) is not None:
                    d[k] = hw[k]
                elif why:
                    self.reasons[k] = why
        # CPU package power (Intel RAPL)
        if self.rapl:
            e = _num(self.rapl + "/energy_uj")
            if e is None:
                self.reasons["cpu_power"] = NA_DEFAULT["cpu_power"]
            else:
                if self._rapl_prev:
                    pe, pt = self._rapl_prev
                    de = e - pe
                    if de < 0:
                        de += _num(self.rapl + "/max_energy_range_uj") or 0
                    if 0 <= de:
                        d["cpu_power"] = de / 1e6 / max(now - pt, 1e-3)
                self._rapl_prev = (e, now)
        else:
            self.reasons["cpu_power"] = "No Intel RAPL powercap interface on this CPU/kernel"
        # RAM / swap
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        g = 2**30
        total, avail, used = ram_numbers(vm)
        d["ram_pct"] = used / total * 100 if total else None
        d["ram_used"] = used / g
        d["ram_avail"] = avail / g
        ab, kind, n = self.app_mem
        d["app_ram"] = ab / 2 ** 20
        d["ram_used_noapp"] = max(0.0, used - ab) / g
        self.ram_detail = ram_detail_text(vm, ab, kind, n)
        if kind != "USS":
            self.reasons["_app_ram_kind"] = f"app memory measured as {kind}"
        self.floors["ram_used"] = self.floors["ram_avail"] = self.floors["ram_used_noapp"] = total / g
        d["swap_pct"] = sw.percent if sw.total else None
        d["swap_used"] = sw.used / g if sw.total else None
        if not sw.total:
            self.reasons["swap_pct"] = self.reasons["swap_used"] = "No swap configured"
        else:
            self.floors["swap_used"] = sw.total / g
        self._poll_gpu(d)
        self._poll_extra(d)
        # disk
        io = psutil.disk_io_counters()
        if io:
            if self._disk_prev:
                pt, p = self._disk_prev
                dd = max(now - pt, 1e-3)
                d["disk_read"] = (io.read_bytes - p.read_bytes) / dd
                d["disk_write"] = (io.write_bytes - p.write_bytes) / dd
                d["disk_iops"] = (io.read_count + io.write_count - p.read_count - p.write_count) / dd
                bt = getattr(io, "busy_time", None)
                if bt is not None:
                    d["disk_util"] = max(0.0, min(100.0, (bt - p.busy_time) / (dd * 1000) * 100))
            self._disk_prev = (now, io)
        d["disk_pct"] = s.get("disk_pct")
        # network (raw bytes/s here; the UI converts to Mbps or MB/s)
        try:
            c = psutil.net_io_counters(pernic=True).get(self.iface)
        except Exception as e:
            c = None
            self._note("net_down", e)
            self._note("net_up", e)
        if c:
            if self._net_prev and self._net_prev[0] == self.iface:
                _, pt, rx, tx = self._net_prev
                dd = max(now - pt, 1e-3)
                d["net_down"] = max(0.0, (c.bytes_recv - rx) / dd)
                d["net_up"] = max(0.0, (c.bytes_sent - tx) / dd)
            self._net_prev = (self.iface, now, c.bytes_recv, c.bytes_sent)
            d["net_rx_total"] = c.bytes_recv / 1e9
            d["net_tx_total"] = c.bytes_sent / 1e9
        else:
            for k in ("net_down", "net_up", "net_rx_total", "net_tx_total"):
                self.reasons[k] = "No active network interface"
        d["net_link"] = s.get("net_link")
        d["fan_rpm"] = s.get("fan_rpm")
        d["sys_power"] = s.get("sys_power")
        if self.mac:                         # macOS: fill/override with AppleSMC, IOHID and powermetrics readings
            self.mac.augment(d, self.reasons, self.floors)

    def _poll_gpu(self, d):
        """Normalize every GPU into gpuN_* keys. Unavailable -> None + reason (never estimated)."""
        for dev in self.gpus:
            i = dev.index
            f, rs = dev.read()
            self.gpu_last[i] = (f, rs)
            allwhy = rs.get("_all")
            for name in FIELDS:
                if name in ("vram_total", "clock_max"):
                    continue
                v = f.get(name)
                key = {"vram_used": f"gpu{i}_vram_used"}.get(name, f"gpu{i}_{name}")
                d[key] = v
                if v is None:
                    self.reasons[key] = allwhy or rs.get(name) or "Not exposed by this GPU/driver"
            used, total = f.get("vram_used"), f.get("vram_total")
            d[f"gpu{i}_vram_pct"] = used / total * 100 if used is not None and total else None
            if d[f"gpu{i}_vram_pct"] is None:
                self.reasons[f"gpu{i}_vram_pct"] = allwhy or rs.get("vram_used") or "VRAM telemetry unavailable"
            if total:
                self.floors[f"gpu{i}_vram_used"] = total
            if f.get("clock_max"):
                self.floors[f"gpu{i}_clock"] = f["clock_max"]
            d[f"gpu{i}_panel"] = d.get(f"gpu{i}_util")
            if d[f"gpu{i}_panel"] is None:
                self.reasons[f"gpu{i}_panel"] = allwhy or rs.get("util") or "GPU load unavailable"

    def _poll_extra(self, d):
        """Audio meters and the last speed-test result (bytes/s, converted by the UI)."""
        d["audio_out"], why = self.audio_out.read()
        if why:
            self.reasons["audio_out"] = why
        d["audio_in"], why = self.audio_in.read()
        if why:
            self.reasons["audio_in"] = why
        r = self.speed.snapshot().last                  # only ever a VALID measurement; failures never become 0
        if r.down_mbps is not None:
            d["speed_test"] = r.down_mbps * 1e6 / 8
        else:
            self.reasons["speed_test"] = self.speed.na_reason()


def hardware_report(t):
    L = ["=== SYSTEM ===", f"OS  : {platform.platform()}", f"CPU : {t.info['cpu']}  ({t.cores_n} logical cores)",
         f"RAM : {t.info['ram_gb']:.1f} GB", "", "=== MEMORY (comparable across Windows / macOS / Linux) ===",
         t.ram_detail or "(not sampled yet)", "", f"=== GPUs ({len(t.gpus)} detected) ==="]
    for g in t.gpus:
        L.append(f"GPU {g.index}: {g.vendor} {g.model}  [{g.kind}]  driver={g.driver}  PCI={g.pci or '?'}")
    if not t.gpus:
        L.append("none detected")
    L += ["", "=== DISKS ==="]
    for p in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(p.mountpoint)
            L.append(f"{p.device} on {p.mountpoint} ({p.fstype}) {u.used/1e9:.1f}/{u.total/1e9:.1f} GB")
        except Exception:
            pass
    L += ["", "=== NETWORK ==="]
    try:
        for n, st in psutil.net_if_stats().items():
            L.append(f"{n}: {'UP' if st.isup else 'down'}  speed={st.speed or '?'} Mbps" + ("   <- active" if n == t.iface else ""))
    except Exception:
        pass
    L += ["", "=== TEMPERATURE SENSORS (hwmon) ==="]
    try:
        for name, ents in psutil.sensors_temperatures().items():
            for e in ents:
                L.append(f"{name}/{e.label or 'temp'}: {e.current:.1f} °C")
    except Exception:
        L.append("unavailable")
    L += ["", "=== FANS ==="]
    try:
        fans = psutil.sensors_fans()
        for name, ents in fans.items():
            for e in ents:
                L.append(f"{name}/{e.label or 'fan'}: {e.current} RPM")
        if not fans:
            L.append("no fan tachometers exposed")
    except Exception:
        L.append("unavailable")
    if t.mac:
        L += t.mac.report()
    else:
        L += ["", "=== POWER ===", f"Intel RAPL: {t.rapl or 'not present'}", f"Battery: {', '.join(t.bats) or 'none'}"]
    L += ["", "=== N/A REASONS ==="]
    for k, v in sorted(t.reasons.items()):
        L.append(f"{k}: {v}")
    return "\n".join(L)


def cli_report():
    """`engineering-console --sensors`: print what this machine exposes (no GUI)."""
    t = Telemetry()
    t.poll()
    time.sleep(1.2)
    d = t.poll()
    print(hardware_report(t))
    print("\n=== LIVE VALUES ===")
    for m in t.metrics:
        v = d.get(m.key)
        print(f"{m.label:<14} {'N/A' if v is None else f'{v:.2f} {m.unit}'}")
    t.close()
    if IS_MAC:
        from .macos import mac_diagnostics
        print("\n=== macOS RAW DIAGNOSTICS ===")
        mac_diagnostics()
