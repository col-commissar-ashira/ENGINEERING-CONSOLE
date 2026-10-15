#!/usr/bin/env bash
# Engineering Console installer for Linux Mint / Ubuntu / Debian. Installs per-user.
# Usage:  ./install.sh [--enable-rapl]
#   Packages come from apt when available (needs sudo). If your release has no PySide6 packages, a private
#   virtual environment with PySide6 from PyPI is created inside the install folder instead (needs internet).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/.local/share/engineering-console"
BIN="$HOME/.local/bin/engineering-console"
DESK="$HOME/.local/share/applications/engineering-console.desktop"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
PYSIDE_APT=(python3-pyside6.qtcore python3-pyside6.qtgui python3-pyside6.qtwidgets)

command -v python3 >/dev/null || { echo "python3 missing: sudo apt install python3"; exit 1; }

need=()
python3 -c "import psutil" 2>/dev/null || need+=(python3-psutil)
command -v sensors >/dev/null || need+=(lm-sensors)
command -v parec >/dev/null || need+=(pulseaudio-utils)   # audio level gauges
command -v lspci >/dev/null || need+=(pciutils)            # GPU model names
USE_VENV=0
if ! python3 -c "import PySide6.QtWidgets" 2>/dev/null; then
  echo ">> Refreshing package lists (sudo password may be asked)"
  sudo apt-get update || true
  if apt-cache show "${PYSIDE_APT[0]}" >/dev/null 2>&1; then
    need+=("${PYSIDE_APT[@]}")
  else
    USE_VENV=1
    python3 -c "import ensurepip, venv" 2>/dev/null || need+=(python3-venv)     # Mint/Ubuntu ship venv separately
    need+=(python3-pip)
  fi
fi
# Qt 6.5+ needs this library to open its window under X11 (Mint/Ubuntu do not always have it)
dpkg -s libxcb-cursor0 >/dev/null 2>&1 || need+=(libxcb-cursor0)
if ((${#need[@]})); then
  echo ">> Installing missing packages: ${need[*]}"
  sudo apt-get install -y "${need[@]}"
fi

echo ">> Copying program to $APP_DIR"
rm -rf "$APP_DIR/engineering_console" "$APP_DIR/main.py"
mkdir -p "$APP_DIR" "$(dirname "$BIN")" "$(dirname "$DESK")" "$ICON_DIR"
cp -r "$HERE/engineering_console" "$APP_DIR/"
cp "$HERE/main.py" "$APP_DIR/"
mkdir -p "$APP_DIR/doc"; cp "$HERE/LICENSE" "$HERE/NOTICE" "$HERE/THIRD_PARTY_NOTICES.md" "$APP_DIR/doc/"
cp -r "$HERE/licenses" "$APP_DIR/doc/"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/engineering_console/icon.svg" "$ICON_DIR/engineering-console.svg"

if ((USE_VENV)); then
  echo ">> No PySide6 packages in apt on this system: creating a private virtual environment (internet needed)"
  rm -rf "$APP_DIR/venv"                                    # remove any half-made environment from an earlier failed try
  python3 -m venv --system-site-packages "$APP_DIR/venv"
  "$APP_DIR/venv/bin/pip" install -r "$HERE/requirements.txt"
fi

cat > "$BIN" <<LAUNCH
#!/usr/bin/env bash
if [ -x "$APP_DIR/venv/bin/python" ]; then exec "$APP_DIR/venv/bin/python" "$APP_DIR/main.py" "\$@"; fi
exec python3 "$APP_DIR/main.py" "\$@"
LAUNCH
chmod +x "$BIN"

cat > "$DESK" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Engineering Console
Comment=Spaceship-style hardware monitoring console
Exec=$BIN
Icon=$ICON_DIR/engineering-console.svg
Terminal=false
Categories=System;Monitor;
StartupWMClass=engineering-console
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
command -v nvidia-smi >/dev/null || echo "!! nvidia-smi not found - NVIDIA metrics will show N/A (install the NVIDIA driver via Driver Manager)."

if [[ "${1:-}" == "--enable-rapl" ]]; then
  echo ">> Making Intel RAPL (CPU package power) readable by all users."
  echo "   Security note: this re-enables a counter the kernel restricts (PLATYPUS side channel)."
  echo 'z /sys/class/powercap/intel-rapl:0/energy_uj 0444 root root -' | sudo tee /etc/tmpfiles.d/engineering-console-rapl.conf >/dev/null
  sudo systemd-tmpfiles --create /etc/tmpfiles.d/engineering-console-rapl.conf || true
fi

echo
echo "Installed. Launch from the menu (System > Engineering Console) or run:  $BIN"
echo "Check what your machine exposes:  $BIN --sensors"
