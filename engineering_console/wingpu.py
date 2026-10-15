"""GPU telemetry on Windows for every vendor, from Windows' own GPU performance counters + DXGI.

* DXGI lists the adapters (name, vendor, dedicated VRAM, LUID) - the LUID is what ties a counter to an adapter.
* The performance counters '\\GPU Engine(*)\\Utilization Percentage' and '\\GPU Adapter Memory(*)\\Dedicated Usage'
  (Windows 10 1709+, WDDM 2.0 drivers) give load and dedicated VRAM use - the same source as Task Manager.
* Temperature, clocks, power and fan need vendor APIs (NVML for NVIDIA is covered by nvidia-smi; ADLX / IGCL for
  AMD / Intel are not integrated) so they show N/A with that reason. Nothing is estimated.

The parsing/aggregation functions are pure Python and unit-tested on any OS; the ctypes layer (DXGI, PDH) can only
run on Windows and must be verified on real hardware."""
import re
import sys
import threading
import time
from dataclasses import dataclass

from .gpu import GPUBackend, GpuDevice, GIB

VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x1022: "AMD", 0x8086: "Intel", 0x1414: "Microsoft"}
ENGINE_PATH = r"\GPU Engine(*)\Utilization Percentage"
MEMORY_PATH = r"\GPU Adapter Memory(*)\Dedicated Usage"
_ENG = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_(\d+)_engtype_([a-z0-9_]+)", re.I)
_MEM = re.compile(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+", re.I)
WHY_UNSUPPORTED = ("Needs a vendor API that is not integrated (AMD ADLX / Intel IGCL); "
                   "Windows exposes only load and VRAM use for this GPU")


def luid_key(high, low):
    return f"0x{high & 0xFFFFFFFF:08x}_0x{low & 0xFFFFFFFF:08x}"


def aggregate_engine_load(items):
    """items: {counter instance name: percent}. -> {luid: load %}.
    Per engine, the load of all processes is summed; the GPU's load is its busiest engine (Task Manager's rule)."""
    engines = {}
    for name, val in items.items():
        m = _ENG.search(name)
        if not m or val is None:
            continue
        key = (m.group(1).lower(), m.group(2), m.group(3).lower())
        engines[key] = engines.get(key, 0.0) + float(val)
    out = {}
    for (luid, _eng, _type), v in engines.items():
        out[luid] = max(out.get(luid, 0.0), min(100.0, v))
    return out


def aggregate_memory(items):
    """items: {instance: bytes} -> {luid: dedicated bytes in use}"""
    out = {}
    for name, val in items.items():
        m = _MEM.search(name)
        if m and val is not None:
            k = m.group(1).lower()
            out[k] = out.get(k, 0.0) + float(val)
    return out


@dataclass
class Adapter:
    name: str
    vendor_id: int
    dedicated: int
    luid: str
    software: bool


# ------------------------------------------------------------------ Windows-only layer (ctypes)
def list_adapters():
    """Adapters via DXGI (IDXGIFactory1::EnumAdapters1). Returns [] if DXGI is unavailable."""
    if not sys.platform.startswith("win"):
        return []
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    class LUID(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                    ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]

    def call(obj, index, restype, *argtypes):
        vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])

    try:
        dxgi = ctypes.WinDLL("dxgi")
        iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
        factory = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return []
        out, i = [], 0
        enum = call(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))      # EnumAdapters1
        while True:
            ad = ctypes.c_void_p()
            if enum(factory, i, ctypes.byref(ad)) != 0:
                break
            desc = DESC1()
            call(ad, 10, ctypes.c_long, ctypes.POINTER(DESC1))(ad, ctypes.byref(desc))                # GetDesc1
            out.append(Adapter(desc.Description.strip(), desc.VendorId, int(desc.DedicatedVideoMemory),
                               luid_key(desc.AdapterLuid.high, desc.AdapterLuid.low), bool(desc.Flags & 2)))
            call(ad, 2, ctypes.c_ulong)(ad)                                                           # Release
            i += 1
        call(factory, 2, ctypes.c_ulong)(factory)
        return out
    except Exception:
        return []


class PdhCounters:
    """Wildcard performance counters read through PDH (English names, so it also works on localized Windows)."""

    def __init__(self, paths):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.pdh = ctypes.WinDLL("pdh")
        self.query = wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery failed")
        self.counters = {}
        for p in paths:
            h = wintypes.HANDLE()
            if self.pdh.PdhAddEnglishCounterW(self.query, p, 0, ctypes.byref(h)) != 0:
                raise OSError(f"counter not available: {p}")
            self.counters[p] = h
        self.pdh.PdhCollectQueryData(self.query)

    def collect(self):
        """-> {path: {instance: value}}; raises OSError if collection fails."""
        ct, wt = self.ct, self.wt

        class Value(ct.Structure):
            _fields_ = [("CStatus", wt.DWORD), ("doubleValue", ct.c_double)]

        class Item(ct.Structure):
            _fields_ = [("szName", wt.LPWSTR), ("FmtValue", Value)]

        if self.pdh.PdhCollectQueryData(self.query) != 0:
            raise OSError("PdhCollectQueryData failed")
        res = {}
        for path, h in self.counters.items():
            size, count = wt.DWORD(0), wt.DWORD(0)
            self.pdh.PdhGetFormattedCounterArrayW(h, 0x200, ct.byref(size), ct.byref(count), None)   # PDH_FMT_DOUBLE
            if not size.value:
                res[path] = {}
                continue
            buf = (ct.c_char * size.value)()
            if self.pdh.PdhGetFormattedCounterArrayW(h, 0x200, ct.byref(size), ct.byref(count), buf) != 0:
                res[path] = {}
                continue
            arr = ct.cast(buf, ct.POINTER(Item))
            res[path] = {arr[i].szName: arr[i].FmtValue.doubleValue for i in range(count.value)
                         if arr[i].FmtValue.CStatus in (0, 1)}
        return res

    def close(self):
        try:
            self.pdh.PdhCloseQuery(self.query)
        except Exception:
            pass


class WindowsGpuBackend(GPUBackend):
    vendor = "Windows"

    def __init__(self, skip_nvidia=False):
        self.skip_nvidia, self.pdh, self.error = skip_nvidia, None, None
        self._t, self._load, self._mem = 0.0, {}, {}
        self._lock = threading.Lock()

    def detect(self):
        devs = []
        for a in list_adapters():
            if a.software or a.vendor_id == 0x1414 or (self.skip_nvidia and a.vendor_id == 0x10DE):
                continue
            kind = "integrated" if a.dedicated < GIB else "discrete"
            devs.append(GpuDevice(vendor=VENDORS.get(a.vendor_id, f"0x{a.vendor_id:04x}"), model=a.name, driver="WDDM",
                                  pci="", kind=kind, backend=self, ctx={"luid": a.luid, "dedicated": a.dedicated}))
        if devs:
            try:
                self.pdh = PdhCounters([ENGINE_PATH, MEMORY_PATH])
            except Exception as e:
                self.error = f"Windows GPU performance counters unavailable ({e})"
        return devs

    def _sample(self):
        now = time.monotonic()
        with self._lock:
            if now - self._t < 0.9 or not self.pdh:
                return
            self._t = now
            try:
                res = self.pdh.collect()
                self._load = aggregate_engine_load(res.get(ENGINE_PATH, {}))
                self._mem = aggregate_memory(res.get(MEMORY_PATH, {}))
                self.error = None
            except Exception as e:
                self.error = f"GPU counter read failed: {e}"

    def read(self, dev):
        if not self.pdh:
            return {}, {"_all": self.error or "Windows GPU performance counters unavailable"}
        self._sample()
        if self.error:
            return {}, {"_all": self.error}
        luid, ded = dev.ctx["luid"], dev.ctx["dedicated"]
        used = self._mem.get(luid)
        f = {"util": self._load.get(luid, 0.0 if self._load else None),
             "vram_used": used / GIB if used is not None else None,
             "vram_total": ded / GIB if ded else None}
        r = {k: WHY_UNSUPPORTED for k in ("temp", "clock", "mem_clock", "power", "fan")}
        if dev.kind == "integrated":
            r["vram_used"] = "Integrated GPU: dedicated VRAM is tiny; it shares system memory"
        return f, r

    def close(self):
        if self.pdh:
            self.pdh.close()
