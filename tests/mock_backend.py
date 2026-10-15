"""DEVELOPMENT/TEST ONLY - never imported by the application and not shipped in the .deb.
Provides fake GPUs so multi-GPU layouts can be tested without the hardware."""
import math
import time

from engineering_console.gpu import GPUBackend, GpuDevice


class MockBackend(GPUBackend):
    vendor = "MOCK"

    def __init__(self, n):
        self.n = n

    def detect(self):
        return [GpuDevice(vendor="MOCK", model=f"Mock Accelerator {i}", driver="mock", pci=f"0000:{i:02x}:00.0",
                          kind="discrete", backend=self, ctx={"i": i}) for i in range(self.n)]

    def read(self, dev):
        i, t = dev.ctx["i"], time.time()
        w = 0.5 + 0.5 * math.sin(t / 3 + i)
        f = {"util": 100 * w, "temp": 40 + 50 * w, "vram_used": 24 * w, "vram_total": 24.0, "clock": 600 + 1400 * w,
             "clock_max": 2000.0, "mem_clock": 7000.0, "power": 60 + 240 * w if i % 3 else None, "fan": 30 + 60 * w}
        return f, {"power": "Power telemetry unavailable (mock: every 3rd GPU)"}
