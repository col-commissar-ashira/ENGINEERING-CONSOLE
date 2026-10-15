"""Real audio level meters via parec (PulseAudio / PipeWire-pulse).

Output meter = peak of the default output's monitor; input meter = peak of the
default microphone. Only levels are computed - audio is never stored or sent
anywhere - and a meter only runs while its gauge is visible.
"""
import math
import shutil
import subprocess
import threading
import time
from array import array

FLOOR_DB = -60.0


def peak_to_db(peak):
    if peak <= 0:
        return FLOOR_DB
    return max(FLOOR_DB, 20 * math.log10(peak / 32768.0))


class AudioMeter:
    def __init__(self, device):
        self.device = device
        self.error = None
        self._peak = 0
        self._got = False
        self._on = False
        self._lock = threading.Lock()
        self.proc = None

    def start(self):
        if self._on:
            return
        if not shutil.which("parec"):
            self.error = "parec not found: sudo apt install pulseaudio-utils"
            return
        self._on, self._got, self.error = True, False, None
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._on = False
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def _run(self):
        try:
            self.proc = subprocess.Popen(
                ["parec", "--device=" + self.device, "--format=s16le", "--rate=8000",
                 "--channels=1", "--latency-msec=40"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while self._on:
                buf = self.proc.stdout.read(640)
                if not buf:
                    break
                a = array("h")
                a.frombytes(buf[: len(buf) // 2 * 2])
                pk = max(map(abs, a)) if a else 0
                with self._lock:
                    self._peak = max(self._peak, pk)
                    self._got = True
            if self._on and not self._got:
                self.error = "No audio server reachable (is PulseAudio/PipeWire running?)"
        except Exception as e:
            self.error = str(e)
        self._on = False

    def read(self):
        """-> (dBFS peak since last call, or None) and a reason when None."""
        if not self._on:
            return None, self.error or "Meter off (gauge hidden)"
        with self._lock:
            if not self._got:
                return None, "Starting audio meter..."
            pk, self._peak = self._peak, 0
        return peak_to_db(pk), ""


def make_meters():
    """(output meter, input meter) for this platform."""
    from .platform_info import IS_LINUX
    if not IS_LINUX:                                   # macOS and Windows: Qt Multimedia
        from .audio_qt import QtAudioMeter
        return QtAudioMeter("output"), QtAudioMeter("input")
    return AudioMeter("@DEFAULT_MONITOR@"), AudioMeter("@DEFAULT_SOURCE@")
