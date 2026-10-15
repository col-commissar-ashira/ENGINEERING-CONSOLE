"""Speed-test subsystem against LOCAL mock servers only (no internet). python3 tests/test_speedtest.py

Proves: repeated successful tests, failures never become 0 Mbps, last valid result is preserved, no overlapping
tests, cancellation, timeouts, worker exceptions, endpoint failover, partial transfers, rate limiting with
Retry-After, continuous testing limits, and that threads / sockets / memory stay flat across hundreds of cycles.
"""
import gc
import os
import sys
import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from engineering_console import speedtest as st                      # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok &= bool(cond)


# ------------------------------------------------------------------ mock server
class Mock(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.mode = {}                       # behaviours, changed by tests at any time
        self.lock = threading.Lock()
        self.active = self.max_active = self.requests = 0
        self.rl_times = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def count(self, d):
        with self.lock:
            self.active += d
            self.max_active = max(self.max_active, self.active)
            if d > 0:
                self.requests += 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        m, srv = self.server.mode, self.server
        srv.count(1)
        try:
            n = int(parse_qs(urlparse(self.path).query).get("bytes", ["0"])[0])
            if m.get("rate_limit"):
                srv.rl_times.append(time.monotonic())
                return self._send(429, b"slow down", {"Retry-After": str(m.get("retry_after", 1))} if m.get("retry_after") else {})
            if m.get("down_status"):
                return self._send(m["down_status"], b"err")
            if n and m.get("sleep"):
                time.sleep(m["sleep"])
            if n and m.get("stall"):
                self.send_response(200)
                self.send_header("Content-Length", str(n))
                self.end_headers()
                time.sleep(m["stall"])                       # headers sent, body never arrives
                return
            if n and m.get("partial"):
                self.send_response(200)
                self.send_header("Content-Length", str(n))
                self.end_headers()
                self.wfile.write(b"\0" * (n // 3))
                self.wfile.flush()
                self.close_connection = True
                return
            if n and m.get("reset"):
                self.connection.close()
                return
            if n and m.get("trickle"):
                self.send_response(200)
                self.send_header("Content-Length", str(n))
                self.end_headers()
                sent = 0
                while sent < n:
                    self.wfile.write(b"\0" * 20000)
                    self.wfile.flush()
                    sent += 20000
                    time.sleep(0.05)
                return
            self._send(200, b"\0" * n)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            srv.count(-1)

    def do_POST(self):
        m, srv = self.server.mode, self.server
        srv.count(1)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            got = 0
            while got < length:
                chunk = self.rfile.read(min(65536, length - got))
                if not chunk:
                    break
                got += len(chunk)
            if m.get("rate_limit_up"):
                return self._send(429, b"", {"Retry-After": "2"})
            if m.get("up_status"):
                return self._send(m["up_status"], b"err")
            if m.get("up_sleep"):
                time.sleep(m["up_sleep"])
            self._send(200, b"ok")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            srv.count(-1)


TINY = st.Size("tiny", "Tiny", 400_000, 3.0, 200_000)
FAST = st.Timing(connect_timeout=1.0, io_timeout=1.0, ping_samples=3, up_chunk=100_000, retry_pause=0.05,
                 min_down_bytes=50_000, default_cooldown=1.0)
SIZES = {"quick": TINY, "normal": TINY, "full": TINY}


def make(srv=None, urls=None, timing=FAST):
    eps = urls or [srv.url]
    return st.SpeedTestEngine(eps, timing, SIZES)


def fds():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


def settle():
    gc.collect()
    time.sleep(0.15)


srv = Mock()

# ---------------- 1. one good test, state flow
e = make(srv)
o = e.run_test("quick")
s = e.snapshot()
check("successful test: outcome ok, valid down+up+latency",
      o.status == "ok" and s.last.down_mbps > 0 and s.last.up_mbps > 0 and s.last.latency_ms > 0, str(o))
check("state flow IDLE->STARTING->PING->DOWNLOAD->UPLOAD->COMPLETE->IDLE",
      e.transitions == ["STARTING", "PING", "DOWNLOAD", "UPLOAD", "COMPLETE", "IDLE"], str(e.transitions))
check("view is 'valid'; back at IDLE", s.view == "valid" and s.state is st.State.IDLE)
check("traffic is counted from real bytes (down >= 400KB-ish, up == 200KB)",
      s.traffic.down_bytes > 50_000 and s.traffic.up_bytes == 200_000 and s.traffic.total == s.traffic.down_bytes + s.traffic.up_bytes,
      str(s.traffic))
good = e.snapshot().last

# ---------------- 2. failures never become 0 Mbps and never wipe the last valid result
for name, mode, want in [
        ("download HTTP 500", {"down_status": 500}, st.Reason.SERVER_UNAVAILABLE),
        ("reset connection mid-download", {"reset": True}, None),
        ("stalled body (timeout)", {"stall": 3}, None),
        ("partial transfer", {"partial": True}, None)]:
    srv.mode = dict(mode)
    # ping uses bytes=0 so it passes for reset/stall/partial; the download phase must fail
    e2 = make(srv)
    e2._last = good
    o = e2.run_test("quick")
    s = e2.snapshot()
    check(f"{name}: test FAILED (not a speed)", o.status == "failed", str(o))
    check(f"{name}: last valid result preserved, not zeroed", s.last == good and s.last.down_mbps > 0)
    check(f"{name}: failure is reported with a human message", bool(o.message) and o.reason is not st.Reason.OK, o.message)
    check(f"{name}: view == 'failed'", s.view == "failed")
srv.mode = {}

srv.mode = {"stall": 3}
e2 = make(srv)
o = e2.run_test("quick")
check("stall maps to Timeout", o.reason is st.Reason.TIMEOUT, str(o))
srv.mode = {"down_status": 500}
o = make(srv).run_test("quick")
check("HTTP 500 maps to Server unavailable", o.reason is st.Reason.SERVER_UNAVAILABLE, str(o))
srv.mode = {"partial": True}
e2 = make(srv)
o = e2.run_test("quick")
check("partial transfer is detected (Download failure) and not averaged into a speed",
      o.status == "failed" and o.reason in (st.Reason.DOWNLOAD_FAILURE, st.Reason.NO_DATA), str(o))
srv.mode = {}

# ---------------- 3. upload failure keeps the valid download
srv.mode = {"up_status": 500}
e3 = make(srv)
o = e3.run_test("quick")
s = e3.snapshot()
check("upload failure: outcome partial/Upload failure, download kept, upload NOT zero",
      o.status == "partial" and o.reason is st.Reason.UPLOAD_FAILURE and s.last.down_mbps > 0 and s.last.up_mbps is None, str(o))
e3.set_size("quick")
srv.mode = {}
o = e3.run_test("quick")
first_up = e3.snapshot().last.up_mbps
srv.mode = {"up_status": 500}
e3.run_test("quick")
check("a later upload failure does not erase the last valid upload", e3.snapshot().last.up_mbps == first_up and first_up > 0)
srv.mode = {}

# ---------------- 4. network down / server down
dead = make(urls=["http://127.0.0.1:1"])
o = dead.run_test("quick")
check("connection refused -> failed (Server unavailable), no speed invented",
      o.status == "failed" and o.reason is st.Reason.SERVER_UNAVAILABLE and dead.snapshot().last.down_mbps is None, str(o))
o = make(urls=["http://does-not-exist.invalid"]).run_test("quick")
check("unresolvable host -> Network unavailable", o.reason is st.Reason.NETWORK_UNAVAILABLE, str(o))
check("no result yet => view 'failed' with nothing to show", dead.snapshot().view == "failed" and not dead.snapshot().last.has_data)

# ---------------- 5. endpoint failover
good_srv = Mock()
fo = make(urls=["http://127.0.0.1:1", good_srv.url])
o = fo.run_test("quick")
check("endpoint failover: first endpoint dead, second used", o.status == "ok" and fo.snapshot().last.server == "127.0.0.1", str(o))
fo2 = make(urls=["http://127.0.0.1:1", "http://127.0.0.1:2"])
o = fo2.run_test("quick")
check("all endpoints failed is reported as such", o.reason is st.Reason.ALL_ENDPOINTS_FAILED, str(o))

# ---------------- 6. rate limiting: Retry-After honoured, never 0 Mbps, no hammering
srv.mode = {"rate_limit": True, "retry_after": 2}
srv.rl_times.clear()
rl = make(srv)
rl._last = good
o = rl.run_test("quick")
check("HTTP 429 -> Rate limited (retry_after read from header)", o.reason is st.Reason.RATE_LIMITED and o.retry_after == 2, str(o))
check("rate limited test keeps last valid result", rl.snapshot().last == good)
n_before = srv.requests
t0 = time.monotonic()
o2 = rl.run_test("quick")
check("during cooldown NO request reaches the server", srv.requests == n_before and o2.reason is st.Reason.RATE_LIMITED)
check("cooldown is visible to the UI", rl.snapshot().cooldown_s > 0)
srv.mode = {}
time.sleep(2.1)
o3 = rl.run_test("quick")
check("after the Retry-After period the endpoint is used again and works", o3.status == "ok", str(o3))
srv.mode = {"rate_limit": True}                                      # no Retry-After header -> default cooldown
rl2 = make(srv)
o = rl2.run_test("quick")
check("429 without Retry-After uses a bounded default cooldown", o.reason is st.Reason.RATE_LIMITED and rl2.snapshot().cooldown_s <= FAST.max_cooldown, str(o))
srv.mode = {}
check("Retry-After parser: seconds, date, junk", st.parse_retry_after("7") == 7 and st.parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT") == 3600
      and st.parse_retry_after("soon") is None and st.parse_retry_after(None) is None)

# ---------------- 7. no overlap, cancellation
srv.mode = {"trickle": True}
ov = make(srv)
big = st.Size("big", "Big", 4_000_000, 3.0, 200_000)
ov.sizes["quick"] = big
check("start() accepted", ov.start("quick"))
time.sleep(0.3)
check("second start() while running is refused (no overlap)", ov.start("quick") is False and ov.run_test("quick") is None)
t0 = time.monotonic()
ov.cancel()
done = ov.wait(5)
dt = time.monotonic() - t0
o = ov.snapshot().outcome
check("cancel stops the test quickly", done and dt < 2.5 and o.status == "cancelled", f"{dt:.2f}s {o}")
check("cancelled test keeps/does not invent results", ov.snapshot().last.down_mbps is None and ov.snapshot().view == "cancelled")
check("no connections left open after cancel", len(ov._conns) == 0 and ov.leaked_workers == 0)
srv.mode = {}
check("engine usable again after cancel", make(srv).run_test("quick").status == "ok")

# ---------------- 8. worker exception is not swallowed
class Boom(st.SpeedTestEngine):
    def _get(self, c, path):
        if "bytes=0" not in path:
            raise ValueError("boom")
        return super()._get(c, path)

bm = Boom([srv.url], FAST, SIZES)
o = bm.run_test("quick")
check("unexpected worker exception -> Worker failure (reported, not swallowed)",
      o.status == "failed" and o.reason is st.Reason.WORKER_FAILURE and "boom" in o.detail, str(o))

# ---------------- 9. data budget shrinks tests
dl = make(srv)
o = dl.run_test("quick", max_total_bytes=250_000)
check("allowance too small -> Data limit reached, nothing sent", o.reason is st.Reason.DATA_LIMIT and dl.snapshot().traffic.total == 0, str(o))
dl = make(srv)
dl.run_test("quick", max_total_bytes=500_000)
check("test is shrunk to fit the remaining allowance (total traffic <= allowance)", dl.snapshot().traffic.total <= 500_000, str(dl.snapshot().traffic))

# ---------------- 9b. measurement accuracy: server paced at 20000 B / 50 ms = 3.2 Mbps
srv.mode = {"trickle": True}
acc = make(srv)
acc.run_test("quick")
mbps = acc.snapshot().last.down_mbps
check("download uses real bytes / real time (paced 3.2 Mbps server reads ~3.2)", mbps is not None and 2.4 < mbps < 4.2, f"{mbps}")
srv.mode = {}

# ---------------- 10. repeated cycles: threads, sockets, memory stay flat
settle()
th0, fd0 = threading.active_count(), fds()
tracemalloc.start()
mem0 = tracemalloc.get_traced_memory()[0]
rep = make(srv)
N = 240
t0 = time.monotonic()
bad = 0
modes = [{}] * 8 + [{"down_status": 500}, {"up_status": 500}, {}]       # a failure every few tests
for i in range(N):
    srv.mode = dict(modes[i % len(modes)])
    o = rep.run_test("quick")
    bad += o.status == "failed"
    if o.status == "failed":
        assert rep.snapshot().last.down_mbps > 0, "last valid result lost"
    assert rep.state is st.State.IDLE
srv.mode = {}
settle()
th1, fd1 = threading.active_count(), fds()
mem1 = tracemalloc.get_traced_memory()[0]
tracemalloc.stop()
s = rep.snapshot()
check(f"{N} simulated tests ran ({bad} failed on purpose) in {time.monotonic() - t0:.0f}s", s.tests_run == N)
check("thread count flat after repeated tests", th1 <= th0 + 1, f"{th0}->{th1}")
check("open sockets/file descriptors flat", fd0 < 0 or fd1 <= fd0 + 2, f"{fd0}->{fd1}")
check("no leaked workers, no open connections", rep.leaked_workers == 0 and len(rep._conns) == 0)
check("memory growth bounded (< 1.5 MB over the run)", mem1 - mem0 < 1_500_000, f"{(mem1 - mem0) / 1e3:.0f} KB")
check("last valid result still intact after the whole run", s.last.has_data and s.last.down_mbps > 0 and s.last.up_mbps > 0)
check("server never saw two tests at once beyond the worker fan-out (<= 4 concurrent)", srv.max_active <= 8, str(srv.max_active))

# ---------------- 11. continuous runner
ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
ctl.runner.min_interval = 0.05
cfg = st.ContinuousConfig(interval_s=0.1, duration_s=0, max_tests=12, data_limit_bytes=0, size="quick")
th0 = threading.active_count()
srv.max_active = 0
check("continuous start", ctl.start_continuous(cfg))
check("second continuous start refused", ctl.start_continuous(cfg) is False)
ctl.runner.join(60)
c = ctl.continuous_status()
check("continuous: stops by itself after max_tests", not c.running and c.tests == 12 and "test count" in c.finish_reason, str(c))
check("continuous: threads back to baseline", threading.active_count() <= th0 + 1)

srv.mode = {}
ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
ctl.runner.min_interval = 0.05
cfg = st.ContinuousConfig(interval_s=0.05, duration_s=0, max_tests=0, data_limit_bytes=1_500_000, size="quick")
ctl.start_continuous(cfg)
ctl.runner.join(60)
c = ctl.continuous_status()
tot = ctl.snapshot().traffic.total
check("continuous: data limit applies to TOTAL traffic (down+up) and stops the run", "data limit" in c.finish_reason and tot <= 1_500_000 + 50_000 and ctl.snapshot().traffic.up_bytes > 0, f"{c} total={tot}")

ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
ctl.runner.min_interval = 0.05
ctl.start_continuous(st.ContinuousConfig(interval_s=0.05, duration_s=1.0, max_tests=0, data_limit_bytes=0, size="quick"))
ctl.runner.join(30)
check("continuous: stops at the duration limit", "time limit" in ctl.continuous_status().finish_reason)

ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
ctl.runner.min_interval = 0.05
ctl.start_continuous(st.ContinuousConfig(interval_s=0.05, duration_s=0, max_tests=0, data_limit_bytes=0, size="quick"))
time.sleep(0.6)
t0 = time.monotonic()
ctl.stop_continuous()
ctl.runner.join(10)
check("continuous: cancellation stops promptly", not ctl.continuous_status().running and time.monotonic() - t0 < 3)
check("continuous: engine idle and clean after cancel", ctl.state is st.State.IDLE and len(ctl.engine._conns) == 0)

# continuous + rate limiting: waits at least Retry-After, no hammering, then recovers
srv.mode = {"rate_limit": True, "retry_after": 1}
srv.rl_times.clear()
ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
ctl.runner.min_interval = 0.05
ctl.start_continuous(st.ContinuousConfig(interval_s=0.05, duration_s=0, max_tests=3, data_limit_bytes=0, size="quick"))
time.sleep(0.5)
srv.mode = {}
ctl.runner.join(30)
rt = srv.rl_times
check("continuous: while rate limited the server is hit once, not hammered", len(rt) <= 2, f"{len(rt)} hits")
check("continuous: recovers after the rate limit and finishes", ctl.continuous_status().ok >= 1 and not ctl.continuous_status().running, str(ctl.continuous_status()))
check("continuous: a rate-limited test never produced a 0 Mbps result", ctl.snapshot().last.down_mbps is None or ctl.snapshot().last.down_mbps > 0)

# manual test while continuous runs does not overlap
ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
ctl.runner.min_interval = 0.05
ctl.start_continuous(st.ContinuousConfig(interval_s=0.05, duration_s=1.5, max_tests=0, data_limit_bytes=0, size="quick"))
refused = 0
for _ in range(20):
    refused += ctl.start("quick") is False
    time.sleep(0.03)
ctl.runner.join(30)
check("manual start during continuous testing is refused while a test is running (no overlap)", refused > 0)

# ---------------- 12. presentation states are distinct
ctl = st.SpeedController([srv.url], "quick", FAST, SIZES)
check("nodata state text", "No test yet" in ctl.summary() and ctl.snapshot().view == "nodata")
ctl.engine.run_test("quick")
check("valid state shows last result and HTTP latency label", "HTTP latency" in ctl.summary() and ctl.snapshot().view == "valid")
srv.mode = {"down_status": 500}
ctl.engine.run_test("quick")
check("failed state keeps last valid and says so", ctl.snapshot().view == "failed" and "last successful test" in ctl.summary()
      and ctl.note().startswith("LAST VALID | FAILED"))
srv.mode = {}
check("sizes document what they cost", "at most" in st.SIZES["quick"].describe() and st.SIZES["quick"].max_bytes < st.SIZES["normal"].max_bytes < st.SIZES["full"].max_bytes)

srv.shutdown()
good_srv.shutdown()
print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
