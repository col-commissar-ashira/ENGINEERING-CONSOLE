"""Internet speed test: latency, download and upload measured over HTTP(S) with real bytes and real time.

Design rules (they exist because earlier versions got stuck at 0.00 after repeated tests):

* A FAILED test is never a speed. 0 Mbps is only ever shown for a real zero measurement; "no data" is a failure.
* The last VALID result is kept (per field) and survives failed, cancelled and rate-limited tests.
* Exactly one test runs at a time. Every blocking call has a finite timeout, every connection is registered so
  it can be closed on cancel/finish, every worker thread is joined, and failures are logged, never swallowed.
* Servers that answer HTTP 429 (or 503 + Retry-After) are respected: the endpoint is put on cooldown and no
  request is sent to it until the cooldown has passed. Repeated failures back off, bounded.
* "Latency" here is HTTP request latency (not ICMP ping) and is labelled that way.

State flow:  IDLE -> STARTING -> PING -> DOWNLOAD -> UPLOAD -> COMPLETE -> IDLE
             (any phase) -> FAILED | CANCELLED -> IDLE
"""
import email.utils
import enum
import errno
import http.client
import logging
import socket
import ssl
import statistics
import threading
import time
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from .logsetup import event

log = logging.getLogger("engineering_console.speedtest")

DEFAULT_ENDPOINTS = ("https://speed.cloudflare.com",)     # the only public server known to speak this protocol
HEADERS = {"User-Agent": "EngineeringConsole/3 (speed test)", "Accept": "*/*"}
MB = 10 ** 6


# ------------------------------------------------------------------ states, reasons, messages
class State(enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    PING = "ping"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Reason(enum.Enum):
    OK = "ok"
    SERVER_UNAVAILABLE = "server_unavailable"
    NETWORK_UNAVAILABLE = "network_unavailable"
    TIMEOUT = "timeout"
    WORKER_FAILURE = "worker_failure"
    UPLOAD_FAILURE = "upload_failure"
    DOWNLOAD_FAILURE = "download_failure"
    RATE_LIMITED = "rate_limited"
    NO_DATA = "no_data"
    ALL_ENDPOINTS_FAILED = "all_endpoints_failed"
    CANCELLED = "cancelled"
    DATA_LIMIT = "data_limit"


MESSAGES = {
    Reason.OK: "Completed",
    Reason.SERVER_UNAVAILABLE: "Server unavailable",
    Reason.NETWORK_UNAVAILABLE: "Network unavailable",
    Reason.TIMEOUT: "Timeout",
    Reason.WORKER_FAILURE: "Worker failure",
    Reason.UPLOAD_FAILURE: "Upload failure",
    Reason.DOWNLOAD_FAILURE: "Download failure",
    Reason.RATE_LIMITED: "Rate limited",
    Reason.NO_DATA: "No data received",
    Reason.ALL_ENDPOINTS_FAILED: "All endpoints failed",
    Reason.CANCELLED: "Cancelled",
    Reason.DATA_LIMIT: "Data limit reached",
}
TRANSIENT = {Reason.TIMEOUT, Reason.DOWNLOAD_FAILURE, Reason.UPLOAD_FAILURE}      # worth one bounded retry
PRIORITY = [Reason.RATE_LIMITED, Reason.NETWORK_UNAVAILABLE, Reason.SERVER_UNAVAILABLE, Reason.TIMEOUT,
            Reason.WORKER_FAILURE, Reason.DOWNLOAD_FAILURE, Reason.UPLOAD_FAILURE, Reason.NO_DATA]


class TestFailure(Exception):
    __test__ = False                    # not a pytest test class

    def __init__(self, reason, detail="", retry_after=None):
        super().__init__(f"{reason.value}: {detail}")
        self.reason, self.detail, self.retry_after = reason, detail, retry_after


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ configuration
@dataclass(frozen=True)
class Size:
    """How much a test may transfer. `down_bytes`/`up_bytes` are hard caps; the time window can end it sooner."""
    key: str
    label: str
    down_bytes: int
    down_seconds: float
    up_bytes: int

    @property
    def max_bytes(self):
        return self.down_bytes + self.up_bytes

    def describe(self):
        return (f"{self.label}: up to {self.down_bytes / MB:.0f} MB down + {self.up_bytes / MB:.0f} MB up "
                f"(at most {self.max_bytes / MB:.0f} MB per test)")


SIZES = {
    "quick": Size("quick", "Quick", 10 * MB, 4.0, 2 * MB),
    "normal": Size("normal", "Normal", 40 * MB, 6.0, 8 * MB),
    "full": Size("full", "Full", 120 * MB, 8.0, 16 * MB),
}
DEFAULT_SIZE = "quick"


@dataclass(frozen=True)
class Timing:
    connect_timeout: float = 5.0
    io_timeout: float = 6.0
    ping_samples: int = 6
    down_threads: int = 4
    up_threads: int = 2
    up_chunk: int = 1 * MB
    phase_retries: int = 1              # bounded: at most this many extra attempts per phase
    retry_pause: float = 0.4
    min_down_bytes: int = 100_000       # less than this is "no data", not a (tiny) speed
    default_cooldown: float = 30.0      # used when a rate-limited server sends no Retry-After
    max_cooldown: float = 600.0


@dataclass(frozen=True)
class LastValid:
    down_mbps: float = None
    up_mbps: float = None
    latency_ms: float = None            # HTTP request latency, not ICMP
    down_time: float = None
    up_time: float = None
    latency_time: float = None
    server: str = None

    @property
    def has_data(self):
        return self.down_mbps is not None or self.up_mbps is not None


@dataclass(frozen=True)
class Outcome:
    status: str                         # "ok" | "partial" | "failed" | "cancelled"
    reason: Reason
    message: str
    detail: str = ""
    time: float = 0.0
    retry_after: float = None


@dataclass(frozen=True)
class Traffic:
    down_bytes: int = 0
    up_bytes: int = 0

    @property
    def total(self):
        return self.down_bytes + self.up_bytes


@dataclass(frozen=True)
class Snapshot:
    state: State
    size: str
    last: LastValid
    outcome: Outcome
    traffic: Traffic
    cooldown_s: float
    tests_run: int

    @property
    def busy(self):
        return self.state is not State.IDLE

    @property
    def view(self):
        """testing | failed | cancelled | valid | nodata - the five states a UI must keep apart."""
        if self.busy:
            return "testing"
        o = self.outcome
        if o and o.status == "failed":
            return "failed"
        if o and o.status == "cancelled":
            return "cancelled"
        return "valid" if self.last.has_data else "nodata"


def fmt_bytes(n):
    return f"{n / 1e9:.2f} GB" if n >= 10 ** 9 else f"{n / 1e6:.1f} MB"


# ------------------------------------------------------------------ small helpers
class Budget:
    """Thread-safe byte budget: workers reserve what they will request and refund what they did not use."""

    def __init__(self, total):
        self._left, self._lock = max(0, int(total)), threading.Lock()

    def reserve(self, want):
        with self._lock:
            n = min(int(want), self._left)
            self._left -= n
            return n

    def refund(self, n):
        if n > 0:
            with self._lock:
                self._left += int(n)

    @property
    def left(self):
        with self._lock:
            return self._left


class ConnSet:
    """Every open connection of the running test, so cancel/finish can close them and unblock reads."""

    def __init__(self):
        self._lock, self._conns = threading.Lock(), set()

    def add(self, c):
        with self._lock:
            self._conns.add(c)

    def discard(self, c):
        with self._lock:
            self._conns.discard(c)

    def close_all(self):
        with self._lock:
            conns, self._conns = list(self._conns), set()
        for c in conns:
            try:
                if c.sock is not None:
                    c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except Exception:
                pass

    def __len__(self):
        with self._lock:
            return len(self._conns)


def parse_retry_after(value):
    """Retry-After is either delta-seconds or an HTTP date. Returns seconds (1..3600) or None."""
    if not value:
        return None
    value = value.strip()
    try:
        secs = float(value)
    except ValueError:
        try:
            secs = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return None
    return min(3600.0, max(1.0, secs))


def classify(exc, phase):
    """Map an exception raised inside a worker to a TestFailure with a human-meaningful reason."""
    if isinstance(exc, TestFailure):
        return exc
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TestFailure(Reason.TIMEOUT, f"{phase}: timed out")
    if isinstance(exc, socket.gaierror):
        return TestFailure(Reason.NETWORK_UNAVAILABLE, f"name lookup failed ({exc})")
    if isinstance(exc, ConnectionRefusedError):
        return TestFailure(Reason.SERVER_UNAVAILABLE, "connection refused")
    if isinstance(exc, ssl.SSLError):
        return TestFailure(Reason.SERVER_UNAVAILABLE, f"TLS error ({exc.__class__.__name__})")
    if isinstance(exc, OSError) and exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN):
        return TestFailure(Reason.NETWORK_UNAVAILABLE, f"{exc.strerror or exc}")
    if isinstance(exc, (ConnectionError, http.client.HTTPException, OSError)):
        why = {"ping": Reason.SERVER_UNAVAILABLE, "upload": Reason.UPLOAD_FAILURE}.get(phase, Reason.DOWNLOAD_FAILURE)
        return TestFailure(why, f"{exc.__class__.__name__}: {exc}")
    return TestFailure(Reason.WORKER_FAILURE, f"{exc.__class__.__name__}: {exc}")


_ZEROS = bytes(1 * MB)


# ------------------------------------------------------------------ the engine
class SpeedTestEngine:
    """One speed test at a time. All public methods are thread-safe."""

    def __init__(self, endpoints=DEFAULT_ENDPOINTS, timing=None, sizes=None, clock=time.monotonic):
        self.endpoints = [e.rstrip("/") for e in endpoints]
        self.t = timing or Timing()
        self.sizes = dict(sizes or SIZES)
        self._clock = clock
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()                # held for the whole duration of a test
        self._state = State.IDLE
        self._size = DEFAULT_SIZE
        self._last = LastValid()
        self._outcome = None
        self._traffic = Traffic()
        self._tests_run = 0
        self._cancel = threading.Event()
        self._conns = ConnSet()
        self._cooldown = {}                              # endpoint -> monotonic time it may be used again
        self._rl_hits = {}                               # endpoint -> consecutive rate-limit count
        self._thread = None
        self.transitions = []                            # state names of the latest test (diagnostics/tests)
        self.leaked_workers = 0                          # workers that outlived their phase (must stay 0)

    # -------- public state
    def snapshot(self):
        with self._lock:
            now = self._clock()
            waits = [self._cooldown.get(e, 0.0) - now for e in self.endpoints]
            cd = min(waits) if waits and all(w > 0 for w in waits) else 0.0
            return Snapshot(self._state, self._size, self._last, self._outcome, self._traffic, cd, self._tests_run)

    @property
    def state(self):
        with self._lock:
            return self._state

    def _set_state(self, s):
        with self._lock:
            self._state = s
            if len(self.transitions) < 64:
                self.transitions.append(s.name)

    def set_size(self, key):
        if key in self.sizes:
            with self._lock:
                self._size = key

    def reset_traffic(self):
        with self._lock:
            self._traffic = Traffic()

    # -------- starting / cancelling
    def _begin(self):
        if not self._run_lock.acquire(blocking=False):
            return False
        self._cancel.clear()
        with self._lock:
            self.transitions = []
        self._set_state(State.STARTING)
        return True

    def start(self, size=None, max_total_bytes=None):
        """Run a test in a background thread. False if one is already running (tests never overlap)."""
        if not self._begin():
            return False
        self._thread = threading.Thread(target=self._execute, args=(size, max_total_bytes, True),
                                        daemon=True, name="speedtest")
        self._thread.start()
        return True

    def run_test(self, size=None, max_total_bytes=None):
        """Blocking variant (continuous runner, tests). Returns the Outcome, or None if a test is already running."""
        if not self._begin():
            return None
        return self._execute(size, max_total_bytes, True)

    def cancel(self):
        self._cancel.set()
        self._conns.close_all()

    def wait(self, timeout=None):
        t = self._thread
        if t is not None:
            t.join(timeout)
        return self.state is State.IDLE

    # -------- cooldown (rate limiting)
    def _cooling(self, ep):
        with self._lock:
            return max(0.0, self._cooldown.get(ep, 0.0) - self._clock())

    def _note_rate_limit(self, ep, retry_after):
        with self._lock:
            n = self._rl_hits[ep] = self._rl_hits.get(ep, 0) + 1
            wait = retry_after or self.t.default_cooldown * 2 ** (n - 1)
            wait = min(wait, self.t.max_cooldown)
            self._cooldown[ep] = self._clock() + wait
        event(log, "speedtest.rate_limited", logging.WARNING, endpoint=ep, retry_after=retry_after,
              cooldown_s=round(wait, 1), consecutive=n)

    # -------- the test itself
    def _execute(self, size_key, cap, release):
        try:
            size = self.sizes.get(size_key or self._size) or self.sizes[DEFAULT_SIZE]
            outcome = self._run(size, cap)
        except Cancelled:
            outcome = Outcome("cancelled", Reason.CANCELLED, MESSAGES[Reason.CANCELLED], time=time.time())
        except TestFailure as f:
            outcome = Outcome("failed", f.reason, MESSAGES[f.reason], f.detail, time.time(), f.retry_after)
        except Exception as e:                                       # a bug must show up in the log, not vanish
            event(log, "speedtest.internal_error", logging.ERROR, exc_info=True, error=repr(e))
            outcome = Outcome("failed", Reason.WORKER_FAILURE, MESSAGES[Reason.WORKER_FAILURE], repr(e), time.time())
        finally:
            self._conns.close_all()
        end_state = {"ok": State.COMPLETE, "partial": State.COMPLETE, "cancelled": State.CANCELLED}.get(
            outcome.status, State.FAILED)
        with self._lock:
            self._outcome = outcome
            self._tests_run += 1
        self._set_state(end_state)
        event(log, "speedtest.finished", logging.INFO if outcome.status in ("ok", "partial") else logging.WARNING,
              status=outcome.status, reason=outcome.reason.value, detail=outcome.detail,
              traffic_total=self._traffic.total)
        self._set_state(State.IDLE)                                   # terminal states are momentary
        if release:
            self._run_lock.release()
        return outcome

    def _check_cancel(self):
        if self._cancel.is_set():
            raise Cancelled()

    def _run(self, size, cap):
        if cap is not None:
            if cap < 300_000:
                raise TestFailure(Reason.DATA_LIMIT, "not enough data allowance left for another test")
            if cap < size.max_bytes:                                  # shrink the test to fit the allowance
                down = int(cap * 0.8)
                size = replace(size, down_bytes=down, up_bytes=int(cap) - down)
        usable = [e for e in self.endpoints if self._cooling(e) <= 0]
        if not usable:
            wait = min(self._cooling(e) for e in self.endpoints)
            raise TestFailure(Reason.RATE_LIMITED, f"server asked us to wait; try again in {wait:.0f} s", wait)
        failures = []
        for ep in usable:
            self._check_cancel()
            try:
                return self._run_endpoint(ep, size)
            except TestFailure as f:
                failures.append((ep, f))
                event(log, "speedtest.endpoint_failed", logging.WARNING, endpoint=ep, reason=f.reason.value,
                      detail=f.detail)
        if len(failures) == 1:
            raise failures[0][1]
        worst = min((f for _, f in failures), key=lambda f: PRIORITY.index(f.reason) if f.reason in PRIORITY else 99)
        detail = "; ".join(f"{urlparse(e).hostname}: {f.reason.value}" for e, f in failures)
        raise TestFailure(Reason.ALL_ENDPOINTS_FAILED, detail, worst.retry_after)

    def _run_endpoint(self, ep, size):
        host = urlparse(ep).hostname
        event(log, "speedtest.start", endpoint=ep, size=size.key)
        self._set_state(State.PING)
        latency = self._with_retry(lambda: self._ping(ep), ep, "ping")
        self._check_cancel()
        self._set_state(State.DOWNLOAD)
        down = self._with_retry(lambda: self._download(ep, size), ep, "download")
        self._check_cancel()
        self._set_state(State.UPLOAD)
        up, up_error = None, None
        try:
            up = self._with_retry(lambda: self._upload(ep, size), ep, "upload")
        except TestFailure as f:                                       # the download is still a valid measurement
            up_error = f
        now = time.time()
        with self._lock:
            self._rl_hits.pop(ep, None)
            last = replace(self._last, latency_ms=latency, latency_time=now, down_mbps=down, down_time=now, server=host)
            if up is not None:
                last = replace(last, up_mbps=up, up_time=now)
            self._last = last
        if up_error is None:
            return Outcome("ok", Reason.OK, MESSAGES[Reason.OK], "", now)
        why = Reason.RATE_LIMITED if up_error.reason is Reason.RATE_LIMITED else Reason.UPLOAD_FAILURE
        detail = up_error.detail if why is Reason.UPLOAD_FAILURE and up_error.reason is Reason.UPLOAD_FAILURE \
            else f"{up_error.reason.value}: {up_error.detail}"
        return Outcome("partial", why, f"Upload failed ({MESSAGES[up_error.reason].lower()})", detail, now,
                       up_error.retry_after)

    def _with_retry(self, fn, ep, phase):
        for attempt in range(self.t.phase_retries + 1):
            self._check_cancel()
            try:
                return fn()
            except TestFailure as f:
                if f.reason is Reason.RATE_LIMITED:
                    self._note_rate_limit(ep, f.retry_after)
                elif f.reason in TRANSIENT and attempt < self.t.phase_retries:
                    event(log, "speedtest.retry", logging.WARNING, endpoint=ep, phase=phase,
                          reason=f.reason.value, attempt=attempt + 1)
                    if self._cancel.wait(self.t.retry_pause * (attempt + 1)):
                        raise Cancelled()
                    continue
                raise

    # -------- HTTP primitives
    def _connect(self, ep):
        u = urlparse(ep)
        port = u.port or (443 if u.scheme == "https" else 80)
        if u.scheme == "https":
            c = http.client.HTTPSConnection(u.hostname, port, timeout=self.t.connect_timeout,
                                            context=ssl.create_default_context())
        else:
            c = http.client.HTTPConnection(u.hostname, port, timeout=self.t.connect_timeout)
        self._conns.add(c)
        try:
            c.connect()
            c.sock.settimeout(self.t.io_timeout)
        except BaseException:
            self._drop(c)
            raise
        return c

    def _drop(self, c):
        if c is not None:
            self._conns.discard(c)
            try:
                c.close()
            except Exception:
                pass

    @staticmethod
    def _check_status(resp):
        """Insist on a 2xx answer - an error page must never be mistaken for data."""
        if 200 <= resp.status < 300:
            return
        ra = parse_retry_after(resp.getheader("Retry-After"))
        try:
            resp.read(200)
        except Exception:
            pass
        if resp.status == 429 or (resp.status == 503 and ra):
            raise TestFailure(Reason.RATE_LIMITED, f"HTTP {resp.status}", ra)
        raise TestFailure(Reason.SERVER_UNAVAILABLE, f"server answered HTTP {resp.status} {resp.reason}")

    def _get(self, c, path):
        c.request("GET", path, headers=HEADERS)
        r = c.getresponse()
        self._check_status(r)
        return r

    # -------- phases
    def _ping(self, ep):
        c, times = None, []
        try:
            c = self._connect(ep)
            for i in range(self.t.ping_samples):
                self._check_cancel()
                t0 = time.perf_counter()
                self._get(c, "/__down?bytes=0").read()
                if i:                                                  # the first request pays for TCP/TLS setup
                    times.append((time.perf_counter() - t0) * 1000)
        except (Cancelled, TestFailure):
            raise
        except Exception as e:
            if self._cancel.is_set():
                raise Cancelled()
            raise classify(e, "ping")
        finally:
            self._drop(c)
        if len(times) < 2:
            raise TestFailure(Reason.NO_DATA, "latency probe returned too few samples")
        return statistics.median(times)

    def _run_workers(self, n, target, phase, deadline_s):
        """Start n worker threads, wait for them (or the deadline, or cancel), then stop and JOIN every one."""
        stop = threading.Event()
        errors, elock = [], threading.Lock()

        def wrap():
            try:
                target(stop)
            except Cancelled:
                pass
            except BaseException as e:
                f = classify(e, phase)
                with elock:
                    if len(errors) < 20:                                # bounded
                        errors.append(f)
                event(log, "speedtest.worker_error", logging.WARNING, exc_info=f.reason is Reason.WORKER_FAILURE,
                      phase=phase, reason=f.reason.value, detail=f.detail)

        threads = [threading.Thread(target=wrap, daemon=True, name=f"speedtest-{phase}-{i}") for i in range(n)]
        t_start = self._clock()
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            if self._cancel.is_set() or self._clock() - t_start > deadline_s:
                break
            time.sleep(0.02)
        stop.set()
        self._conns.close_all()                                        # unblocks a read stuck on a stalled server
        for t in threads:
            t.join(self.t.connect_timeout + 2)
            if t.is_alive():
                self.leaked_workers += 1
                event(log, "speedtest.worker_leaked", logging.ERROR, phase=phase, thread=t.name)
        if self._cancel.is_set():
            raise Cancelled()
        return errors

    @staticmethod
    def _worst(errors):
        for r in PRIORITY:
            for f in errors:
                if f.reason is r:
                    return f
        return errors[0] if errors else None

    def _add_traffic(self, down=0, up=0):
        with self._lock:
            self._traffic = Traffic(self._traffic.down_bytes + down, self._traffic.up_bytes + up)

    def _download(self, ep, size):
        t = self.t
        budget = Budget(size.down_bytes)
        per = max(MB, size.down_bytes // t.down_threads)
        lock = threading.Lock()
        st = {"total": 0, "t0": None, "t0_total": 0, "t_last": None, "samples": [], "next_sample": 0.0, "completed": 0}

        def worker(stop):
            c = None
            try:
                c = self._connect(ep)
                while not stop.is_set():
                    want = budget.reserve(per)
                    if want <= 0:
                        break
                    got = 0
                    try:
                        resp = self._get(c, f"/__down?bytes={want}")
                        while not stop.is_set():
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            now = time.perf_counter()
                            got += len(chunk)
                            with lock:
                                st["total"] += len(chunk)
                                if st["t0"] is None:
                                    st["t0"], st["t0_total"] = now, st["total"]
                                st["t_last"] = now
                                if now >= st["next_sample"]:               # bounded sample list (~50 per second)
                                    st["samples"].append((now, st["total"]))
                                    st["next_sample"] = now + 0.02
                        if got < want and not stop.is_set():
                            raise TestFailure(Reason.DOWNLOAD_FAILURE, f"partial transfer ({got} of {want} bytes)")
                        if got == want:
                            with lock:
                                st["completed"] += 1
                    finally:
                        budget.refund(want - got)
                        self._add_traffic(down=got)
            finally:
                self._drop(c)

        errors = self._run_workers(t.down_threads, worker, "download", size.down_seconds)
        with lock:
            total, t0, tn, t0_total = st["total"], st["t0"], st["t_last"], st["t0_total"]
            samples, completed = list(st["samples"]), st["completed"]
        if t0 is None or tn is None or total < t.min_down_bytes:
            f = self._worst(errors)
            raise f if f else TestFailure(Reason.NO_DATA, "almost no data received (offline, or blocked by a firewall/VPN?)")
        if errors and completed == 0 and tn - t0 < 1.0:             # a brief burst that the server cut off proves nothing
            raise self._worst(errors)
        base_t, base_b = t0, t0_total
        if tn - t0 > 2.5:                                              # skip TCP ramp-up only if we can afford it
            for s in samples:
                if s[0] >= t0 + 1.0:
                    base_t, base_b = s
                    break
        dt = max(tn - base_t, 0.01)                                    # near-instant transfers are lower bounds
        nbytes = total - base_b
        mbps = nbytes * 8 / dt / 1e6
        if nbytes <= 0 or not (0 < mbps < float("inf")):
            raise TestFailure(Reason.NO_DATA, "download measured no throughput")
        for f in errors:                                               # data is valid, but honour what the server said
            if f.reason is Reason.RATE_LIMITED:
                self._note_rate_limit(ep, f.retry_after)
        if errors:
            event(log, "speedtest.download_degraded", logging.WARNING, workers_failed=len(errors),
                  first=errors[0].reason.value)
        return mbps

    def _upload(self, ep, size):
        t = self.t
        budget = Budget(size.up_bytes)
        lock = threading.Lock()
        st = {"ok": 0, "first": None, "last": None}

        def worker(stop):
            c = None
            try:
                c = self._connect(ep)
                while not stop.is_set():
                    n = budget.reserve(t.up_chunk)
                    if n <= 0:
                        break
                    done = False
                    t_send = time.perf_counter()
                    try:
                        c.request("POST", "/__up", body=_ZEROS[:n],
                                  headers={**HEADERS, "Content-Type": "application/octet-stream"})
                        r = c.getresponse()
                        self._check_status(r)
                        r.read()
                        done = True
                    finally:
                        if not done:
                            budget.refund(n)
                    t_end = time.perf_counter()
                    self._add_traffic(up=n)
                    with lock:
                        st["ok"] += n
                        st["first"] = t_send if st["first"] is None else min(st["first"], t_send)
                        st["last"] = t_end if st["last"] is None else max(st["last"], t_end)
            finally:
                self._drop(c)

        errors = self._run_workers(t.up_threads, worker, "upload", size.down_seconds + 6)
        with lock:
            ok, t_first, t_last = st["ok"], st["first"], st["last"]
        if ok <= 0 or t_first is None:
            f = self._worst(errors)
            raise f if f else TestFailure(Reason.UPLOAD_FAILURE, "no upload request completed")
        mbps = ok * 8 / max(t_last - t_first, 0.01) / 1e6
        if not (0 < mbps < float("inf")):
            raise TestFailure(Reason.UPLOAD_FAILURE, "upload measured no throughput")
        for f in errors:
            if f.reason is Reason.RATE_LIMITED:
                self._note_rate_limit(ep, f.retry_after)
        return mbps


# ------------------------------------------------------------------ continuous testing
@dataclass
class ContinuousConfig:
    interval_s: float = 60.0
    duration_s: float = 20 * 60.0       # 0 = no time limit
    max_tests: int = 0                  # 0 = no count limit
    data_limit_bytes: int = 500 * MB    # total traffic (down + up); 0 = no limit
    size: str = DEFAULT_SIZE


@dataclass(frozen=True)
class RunStatus:
    running: bool = False
    tests: int = 0
    ok: int = 0
    failed: int = 0
    traffic: Traffic = Traffic()
    started: float = 0.0
    finish_reason: str = ""
    next_in: float = 0.0


class ContinuousRunner:
    """Runs tests back to back on ONE thread (so threads never pile up). Spacing is start-to-start, but a new
    test never begins before the previous one finished. Failures back off (bounded); Retry-After is honoured."""
    MIN_INTERVAL = 2.0
    BACKOFF_CAP = 120.0

    def __init__(self, engine, min_interval=MIN_INTERVAL):
        self.engine, self.min_interval = engine, min_interval
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._status = RunStatus()
        self._next_at = 0.0

    def status(self):
        with self._lock:
            s = self._status
            nxt = max(0.0, self._next_at - time.monotonic()) if s.running and self._next_at else 0.0
            return replace(s, next_in=nxt)

    def start(self, cfg):
        with self._lock:
            if self._status.running:
                return False
            self._status = RunStatus(running=True, started=time.time())
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(cfg,), daemon=True, name="speedtest-continuous")
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        self.engine.cancel()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def _set(self, **kw):
        with self._lock:
            self._status = replace(self._status, **kw)

    def _loop(self, cfg):
        eng, t0 = self.engine, time.monotonic()
        b0 = eng.snapshot().traffic
        interval = max(self.min_interval, float(cfg.interval_s))
        tests = ok = failed = consecutive = 0
        reason = "stopped"
        event(log, "continuous.start", interval_s=interval, duration_s=cfg.duration_s, max_tests=cfg.max_tests,
              data_limit=cfg.data_limit_bytes, size=cfg.size)
        try:
            while not self._stop.is_set():
                tr = eng.snapshot().traffic
                used = tr.total - b0.total
                if cfg.duration_s and time.monotonic() - t0 >= cfg.duration_s:
                    reason = "finished (time limit reached)"
                    break
                if cfg.max_tests and tests >= cfg.max_tests:
                    reason = "finished (test count reached)"
                    break
                if cfg.data_limit_bytes and used >= cfg.data_limit_bytes:
                    reason = "stopped (data limit reached)"
                    break
                remaining = (cfg.data_limit_bytes - used) if cfg.data_limit_bytes else None
                started = time.monotonic()
                out = eng.run_test(cfg.size, remaining)
                if out is None:                                         # a manual test is running: wait, never overlap
                    self._stop.wait(0.5)
                    continue
                if out.status == "cancelled":
                    reason = "stopped"
                    break
                if out.reason is Reason.DATA_LIMIT:
                    reason = "stopped (data limit reached)"
                    break
                tests += 1
                bad = out.status == "failed" or (out.status == "partial" and out.reason is Reason.RATE_LIMITED)
                failed, ok = failed + (out.status == "failed"), ok + (out.status != "failed")
                consecutive = consecutive + 1 if bad else 0
                tr = eng.snapshot().traffic
                self._set(tests=tests, ok=ok, failed=failed,
                          traffic=Traffic(tr.down_bytes - b0.down_bytes, tr.up_bytes - b0.up_bytes))
                wait = max(0.2, started + interval - time.monotonic())
                if consecutive >= 2:                                    # bounded exponential back-off
                    wait = max(wait, min(self.BACKOFF_CAP, interval * 2 ** (consecutive - 1)))
                if out.reason is Reason.RATE_LIMITED and out.retry_after:
                    wait = max(wait, out.retry_after)
                with self._lock:
                    self._next_at = time.monotonic() + wait
                if self._stop.wait(wait):
                    break
        except Exception:
            event(log, "continuous.error", logging.ERROR, exc_info=True)
            reason = "stopped (internal error, see log)"
        finally:
            with self._lock:
                self._next_at = 0.0
            self._set(running=False, finish_reason=reason, tests=tests, ok=ok, failed=failed)
            event(log, "continuous.end", reason=reason, tests=tests, ok=ok, failed=failed)


# ------------------------------------------------------------------ facade used by the application
class SpeedController:
    """Engine + continuous runner behind one object, plus the text the UI shows."""

    def __init__(self, endpoints=DEFAULT_ENDPOINTS, size=DEFAULT_SIZE, timing=None, sizes=None):
        self.engine = SpeedTestEngine(endpoints, timing, sizes)
        self.engine.set_size(size)
        self.runner = ContinuousRunner(self.engine)

    def start(self, size=None):
        return self.engine.start(size)

    def cancel(self):
        if self.runner.status().running:
            self.runner.stop()
        else:
            self.engine.cancel()

    def set_size(self, key):
        self.engine.set_size(key)

    @property
    def state(self):
        return self.engine.state

    @property
    def running(self):
        return self.engine.state is not State.IDLE

    def snapshot(self):
        return self.engine.snapshot()

    def start_continuous(self, cfg):
        return self.runner.start(cfg)

    def stop_continuous(self):
        self.runner.stop()

    def continuous_status(self):
        return self.runner.status()

    def close(self):
        self.runner.stop()
        self.engine.cancel()
        self.runner.join(5)
        self.engine.wait(5)

    # ---- presentation
    def note(self):
        """Short text shown under the gauge value."""
        s = self.snapshot()
        if s.busy:
            return {"starting": "STARTING...", "ping": "TESTING: LATENCY", "download": "TESTING: DOWNLOAD",
                    "upload": "TESTING: UPLOAD"}.get(s.state.value, "TESTING...")
        o, keep = s.outcome, "LAST VALID | " if s.last.has_data else ""
        if s.view == "failed":
            return keep + "FAILED: " + o.message.upper()
        if s.view == "cancelled":
            return keep + "CANCELLED"
        if o and o.status == "partial":
            return "UPLOAD FAILED"
        return ""

    def summary(self):
        s = self.snapshot()
        l, o = s.last, s.outcome
        lines = []
        if s.busy:
            lines.append(f"Testing - {s.state.value.upper()} ({self.engine.sizes[s.size].label})...")
        if l.has_data:
            when = time.strftime("%H:%M:%S", time.localtime(l.down_time or l.up_time))
            down = f"{l.down_mbps:.1f} Mbps" if l.down_mbps is not None else "n/a"
            up = f"{l.up_mbps:.1f} Mbps" if l.up_mbps is not None else "n/a"
            lat = f"{l.latency_ms:.0f} ms" if l.latency_ms is not None else "n/a"
            lines += [f"Last valid result {when} via {l.server}:",
                      f"Download {down}   Upload {up}   HTTP latency {lat}"]
        elif o is None and not s.busy:
            lines.append("No test yet. Right-click > Run speed test now (or Tools > Run speed test).")
        else:
            lines.append("No valid result yet.")
        if o and not s.busy:
            if o.status == "failed":
                extra = f" - retry in {o.retry_after:.0f} s" if o.retry_after else ""
                lines.append(f"Latest test FAILED: {o.message}{extra}" + (f" ({o.detail})" if o.detail else ""))
                if l.has_data:
                    lines.append("(the values above are from the last successful test)")
            elif o.status == "cancelled":
                lines.append("Latest test was cancelled.")
            elif o.status == "partial":
                lines.append(f"Latest test partly failed: {o.message}")
        if s.cooldown_s > 0:
            lines.append(f"Server rate limit: waiting {s.cooldown_s:.0f} s before the next request.")
        t = s.traffic
        lines.append(f"Data used since launch: down {fmt_bytes(t.down_bytes)}  up {fmt_bytes(t.up_bytes)}  "
                     f"total {fmt_bytes(t.total)}")
        c = self.continuous_status()
        if c.running:
            lines.append(f"Continuous test running: {c.tests} done ({c.ok} ok, {c.failed} failed), next in {c.next_in:.0f} s")
        elif c.finish_reason:
            lines.append(f"Continuous test {c.finish_reason} after {c.tests} tests")
        lines.append("Size: " + self.engine.sizes[s.size].describe())
        return "\n".join(lines)

    def na_reason(self):
        """Why the gauge shows N/A (only used while there is no valid result)."""
        s = self.snapshot()
        if s.busy:
            return self.note()
        if s.view in ("failed", "cancelled") and s.outcome:
            o = s.outcome
            return f"No valid result yet - {o.message}" + (f": {o.detail}" if o.detail else "")
        return "No test yet (right-click > Run speed test now)"
