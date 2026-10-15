"""Optional Windows sensors: temperatures, fans and CPU power are not available to normal Windows programs.
LibreHardwareMonitor (or OpenHardwareMonitor) publishes them through WMI while it is running, so when it is there
we read them from it - and say so when it is not. Nothing is ever estimated.

The parsing is pure Python (tested on any OS); only the PowerShell helper is Windows-specific."""
import json
import shutil
import subprocess
import threading

from .platform_info import hidden_subprocess_kwargs

PS_LOOP = r"""
$ErrorActionPreference = 'SilentlyContinue'
while ($true) {
  $out = $null
  foreach ($ns in 'root/LibreHardwareMonitor', 'root/OpenHardwareMonitor') {
    $s = Get-CimInstance -Namespace $ns -ClassName Sensor
    if ($s) { $out = $s | Select-Object SensorType, Name, Identifier, Value; break }
  }
  if ($out) { ($out | ConvertTo-Json -Compress) } else { 'NONE' }
  Start-Sleep -Seconds 2
}
"""
NOT_RUNNING = ("LibreHardwareMonitor is not running (start it as administrator; OpenHardwareMonitor also works). "
               "Windows itself does not expose this sensor to normal programs")
_CPU = ("/intelcpu", "/amdcpu")


def parse_sensors(text):
    """JSON from the WMI `Sensor` class -> {cpu_temp, ssd_temp, cpu_power, fan_rpm} (None where absent)."""
    out = {"cpu_temp": None, "ssd_temp": None, "cpu_power": None, "fan_rpm": None}
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    cpu_t, ssd_t, fans, power = [], [], [], []
    for it in data:
        val, typ = it.get("Value"), it.get("SensorType")
        ident, name = (it.get("Identifier") or "").lower(), (it.get("Name") or "")
        if val is None:
            continue
        if typ == "Temperature" and ident.startswith(_CPU):
            cpu_t.append((name.lower(), float(val)))
        elif typ == "Temperature" and ident.startswith(("/nvme", "/hdd", "/ssd")):
            ssd_t.append(float(val))
        elif typ == "Fan":
            fans.append(float(val))
        elif typ == "Power" and ident.startswith(_CPU) and "package" in name.lower():
            power.append(float(val))
    if cpu_t:
        pref = [v for n, v in cpu_t if any(k in n for k in ("package", "tctl", "tdie"))]
        out["cpu_temp"] = max(pref) if pref else max(v for _, v in cpu_t)
    out["ssd_temp"] = max(ssd_t) if ssd_t else None
    out["fan_rpm"] = max(fans) if fans else None
    out["cpu_power"] = max(power) if power else None
    return out


class WinHwMonitor:
    """Runs one long-lived PowerShell loop and keeps the newest reading. Never raises."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data, self._why, self._stop = {}, "Starting hardware sensor reader...", False
        self.proc = None
        self._thread = None
        if shutil.which("powershell"):
            self._thread = threading.Thread(target=self._run, daemon=True, name="winhw")
            self._thread.start()
        else:
            self._why = "PowerShell not found"

    def _run(self):
        try:
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_LOOP],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, **hidden_subprocess_kwargs())
            for line in self.proc.stdout:
                if self._stop:
                    break
                line = line.strip()
                with self._lock:
                    if line == "NONE" or not line:
                        self._data, self._why = {}, NOT_RUNNING
                        continue
                    try:
                        self._data, self._why = parse_sensors(line), ""
                    except (ValueError, TypeError, AttributeError) as e:
                        self._data, self._why = {}, f"Unreadable sensor data ({e.__class__.__name__})"
        except Exception as e:
            with self._lock:
                self._data, self._why = {}, f"Hardware sensor reader failed: {e}"

    def read(self):
        with self._lock:
            return dict(self._data), self._why

    def close(self):
        self._stop = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
