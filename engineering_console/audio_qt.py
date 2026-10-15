"""Audio level meters for macOS and Windows (Qt 6 Multimedia). Levels only - nothing is recorded or stored.

* MIC INPUT: the default microphone. The first start triggers the normal OS microphone permission.
* AUDIO OUT: neither macOS nor Windows lets a Qt program listen to what the speakers play. The meter uses a loopback
  input if one is installed (macOS: BlackHole; Windows: 'Stereo Mix' or a virtual cable) and says so when none exists.
"""
import math
import time
from array import array

from .platform_info import IS_MAC

FLOOR_DB = -60.0
LOOPBACK = ("blackhole", "soundflower", "loopback", "background music", "vb-cable", "ishowu", "stereo mix",
            "what u hear", "wave out mix")
if IS_MAC:
    MIC_HINT = "No audio data: allow it in System Settings > Privacy & Security > Microphone (then re-show this gauge)"
    LOOP_HINT = "macOS needs a loopback input to meter speaker output: install BlackHole 2ch (free), then re-show this gauge"
else:
    MIC_HINT = "No audio data: allow desktop apps in Settings > Privacy & security > Microphone (then re-show this gauge)"
    LOOP_HINT = ("Windows gives apps no way to hear speaker output directly: enable 'Stereo Mix' in Sound settings > "
                 "Recording, or install a virtual loopback cable, then re-show this gauge")


def peak_from_bytes(data, fmt_name):
    """Peak level 0..1 of raw interleaved samples, or None for an unsupported sample format. Pure function."""
    if fmt_name == "UInt8":
        return max((abs(b - 128) for b in data), default=0) / 128.0
    spec = {"Int16": ("h", 32768.0), "Int32": ("i", 2147483648.0), "Float": ("f", 1.0)}.get(fmt_name)
    if spec is None:
        return None
    a = array(spec[0])
    n = len(data) // a.itemsize * a.itemsize
    a.frombytes(data[:n])
    return max(map(abs, a)) / spec[1] if a else 0.0


def peak_to_db(pk):
    return max(FLOOR_DB, 20 * math.log10(pk)) if pk > 0 else FLOOR_DB


class QtAudioMeter:
    def __init__(self, role):
        self.role, self.error = role, None
        try:
            from PySide6.QtMultimedia import QMediaDevices
            self._devices = QMediaDevices
        except ImportError:                                   # Qt Multimedia missing: meters say so instead of crashing
            self._devices, self.error = None, "Qt Multimedia is not installed (pip install PySide6)"
        self._peak, self._got, self._on, self._t0 = 0.0, False, False, 0.0
        self.src = self.io = self.fmt = None

    def _pick(self):
        if self.role == "input":
            return self._devices.defaultAudioInput()
        for d in self._devices.audioInputs():
            if any(k in d.description().lower() for k in LOOPBACK):
                return d
        return None

    def start(self):
        if self._on or self._devices is None:
            return
        self.error, self._got, self._peak = None, False, 0.0
        dev = self._pick()
        if dev is None or dev.isNull():
            self.error = LOOP_HINT if self.role == "output" else "No microphone found"
            return
        try:
            from PySide6.QtMultimedia import QAudioSource
            self.fmt = dev.preferredFormat()
            self.src = QAudioSource(dev, self.fmt)
            self.io = self.src.start()
            self.io.readyRead.connect(self._ready)
            self._on, self._t0 = True, time.monotonic()
        except Exception as e:
            self.error = f"Audio input failed: {e}"

    def stop(self):
        if self.src is not None:
            try:
                self.src.stop()
            except Exception:
                pass
        self.src = self.io = None
        self._on = False

    def _ready(self):
        data = bytes(self.io.readAll().data())
        fmt = self.fmt.sampleFormat()
        pk = peak_from_bytes(data, getattr(fmt, "name", str(fmt)).split(".")[-1])
        if pk is not None:
            self._peak = max(self._peak, pk)
            self._got = True

    def read(self):
        if not self._on:
            return None, self.error or "Meter off (gauge hidden)"
        if not self._got:
            if time.monotonic() - self._t0 > 2.5:
                return None, MIC_HINT
            return None, "Starting audio meter..."
        pk, self._peak = self._peak, 0.0
        return peak_to_db(pk), ""
