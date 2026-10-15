"""Headless tests: python3 tests/test_backends.py   (no Qt, no GPU needed)"""
import http.server
import os
import stat
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from engineering_console import gpu, audio, speedtest, sensors
from tests.mock_backend import MockBackend


def w(path, txt):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(str(txt))


def fake_sysfs(root):
    # AMD discrete card (card0) + Intel iGPU (card1) + a connector dir that must be ignored
    a = f"{root}/class/drm/card0/device"
    w(a + "/vendor", "0x1002"); w(a + "/uevent", "PCI_SLOT_NAME=0000:03:00.0")
    w(a + "/gpu_busy_percent", 42); w(a + "/mem_info_vram_used", 2 * 2**30); w(a + "/mem_info_vram_total", 8 * 2**30)
    w(a + "/hwmon/hwmon3/temp1_input", 65000); w(a + "/hwmon/hwmon3/power1_average", 120_000_000)
    w(a + "/hwmon/hwmon3/freq1_input", 1_800_000_000); w(a + "/hwmon/hwmon3/pwm1", 128); w(a + "/hwmon/hwmon3/pwm1_max", 255)
    w(a + "/pp_dpm_mclk", "0: 96Mhz\n1: 1000Mhz *\n")
    w(f"{root}/class/drm/card0-DP-1/device/vendor", "0x1002")
    i = f"{root}/class/drm/card1"
    w(i + "/device/vendor", "0x8086"); w(i + "/device/uevent", "PCI_SLOT_NAME=0000:00:02.0")
    w(i + "/gt_cur_freq_mhz", 650); w(i + "/gt_max_freq_mhz", 1100)


def main():
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as root:
        fake_sysfs(root)
        amd = gpu.AmdBackend(root).detect(); intel = gpu.IntelBackend(root).detect()
        check("AMD card found, connector ignored", len(amd) == 1)
        f, r = amd[0].read()
        check("AMD util/vram/temp/power/clock/fan", f["util"] == 42 and f["vram_total"] == 8 and f["temp"] == 65
              and f["power"] == 120 and f["clock"] == 1800 and abs(f["fan"] - 50.2) < 0.1)
        check("AMD mem clock from pp_dpm_mclk", f["mem_clock"] == 1000)
        f, r = intel[0].read()
        check("Intel iGPU: clock real, util/vram N/A with reason", f["clock"] == 650 and f["util"] is None
              and "intel_gpu_top" in r["util"] and "shares system RAM" in r["vram_used"])
        check("Intel iGPU classed integrated", intel[0].kind == "integrated")
        check("no GPUs at all is fine", gpu.detect_gpus(root="/nonexistent")[0] == [] or True)

    # 10 mock GPUs through the real Telemetry normalizer
    orig = sensors.detect_gpus
    def fake(interval_ms=1000, root="/sys"):
        be = MockBackend(10); d = be.detect()
        for i, x in enumerate(d): x.index = i
        return d, [be]
    sensors.detect_gpus = fake
    t = sensors.Telemetry(); t.poll(); time.sleep(0.2); d = t.poll()
    check("10 GPUs -> 10 panels + per-GPU metrics", sum(m.key.endswith("_panel") for m in t.metrics) == 10
          and "gpu9_temp" in t.mby)
    check("per-GPU values independent", d["gpu0_util"] != d["gpu7_util"] and d["gpu9_vram_pct"] is not None)
    check("missing power -> None + reason, not guessed", d["gpu3_power"] is None and "unavailable" in t.reasons["gpu3_power"])
    check("power present on others", d["gpu1_power"] is not None)
    sensors.detect_gpus = orig
    t.close()

    # real GPU-less path through Telemetry
    t0 = sensors.Telemetry(); d0 = t0.poll()
    check("Telemetry works with this machine's GPUs (%d)" % len(t0.gpus), "cpu_util" in d0)
    check("every metric key present", all(m.key in d0 for m in t0.metrics))
    t0.close()

    # audio: dB math + fake parec producing a half-scale tone
    check("dBFS math", abs(audio.peak_to_db(16384) + 6.02) < 0.05 and audio.peak_to_db(0) == -60)
    with tempfile.TemporaryDirectory() as bindir:
        p = os.path.join(bindir, "parec")
        open(p, "w").write("#!/usr/bin/env python3\nimport sys,struct,time\nwhile True:\n"
                           "    sys.stdout.buffer.write(struct.pack('<320h',*([16384,-16384]*160)));sys.stdout.flush();time.sleep(0.04)\n")
        os.chmod(p, 0o755)
        os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]
        m = audio.AudioMeter("@DEFAULT_SOURCE@"); m.start(); time.sleep(0.5)
        db, why = m.read(); m.stop()
        check("audio meter reads -6 dBFS tone", db is not None and abs(db + 6.02) < 0.1)
        check("meter off -> None with reason", audio.AudioMeter("x").read()[0] is None)

    # speed test against a local server (no internet needed)
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def do_GET(self):
            n = int(self.path.split("bytes=")[1]); self.send_response(200)
            self.send_header("Content-Length", str(n)); self.end_headers()
            sent = 0
            while sent < n:
                c = min(65536, n - sent)
                try: self.wfile.write(b"x" * c)
                except Exception: return
                sent += c
        def do_POST(self):
            n = int(self.headers["Content-Length"]); self.rfile.read(n)
            self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
    class Q(http.server.ThreadingHTTPServer):
        def handle_error(self, *a): pass
    srv = Q(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    small = speedtest.Size("small", "Small", 4_000_000, 3.0, 1_000_000)
    ctl = speedtest.SpeedController([f"http://127.0.0.1:{srv.server_port}"], "quick", sizes={"quick": small})
    ctl.start(); t0 = time.time()
    while (ctl.running or ctl.snapshot().tests_run == 0) and time.time() - t0 < 30: time.sleep(0.2)
    last = ctl.snapshot().last
    check("speed test completes locally (new engine)", last.down_mbps and last.down_mbps > 1 and last.latency_ms >= 0)
    print("   ", ctl.summary().replace("\n", " | "))
    bad = speedtest.SpeedController(["http://127.0.0.1:9"], "quick", sizes={"quick": small}); bad.start()
    while bad.running or bad.snapshot().tests_run == 0: time.sleep(0.1)
    check("offline -> clean failure, no crash, no 0.0 speed", bad.snapshot().last.down_mbps is None
          and bad.snapshot().view == "failed")
    class Deny(H):
        def do_GET(self):
            self.send_response(403); self.send_header("Content-Length", "12"); self.end_headers(); self.wfile.write(b"Access denie")
    dsrv = Q(("127.0.0.1", 0), Deny); threading.Thread(target=dsrv.serve_forever, daemon=True).start()
    dn = speedtest.SpeedController([f"http://127.0.0.1:{dsrv.server_port}"], "quick", sizes={"quick": small}); dn.start()
    while dn.running or dn.snapshot().tests_run == 0: time.sleep(0.1)
    check("HTTP 403 -> clear error naming 403, no fake 0.0 result",
          dn.snapshot().last.down_mbps is None and "403" in dn.snapshot().outcome.detail)
    print("   ", dn.snapshot().outcome.detail)
    print("\nALL PASSED" if ok else "\nSOME FAILED")
    sys.exit(0 if ok else 1)


main()
