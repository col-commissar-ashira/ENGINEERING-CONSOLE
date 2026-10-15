#!/usr/bin/env bash
# Usage: ./uninstall.sh [--purge]   (--purge also deletes settings and logs, including those of the pre-3.0 "System Cockpit")
set -u
rm -rf "$HOME/.local/share/engineering-console/main.py" "$HOME/.local/share/engineering-console/engineering_console" \
       "$HOME/.local/share/engineering-console/venv" "$HOME/.local/share/engineering-console/doc"
rm -f "$HOME/.local/bin/engineering-console" "$HOME/.local/share/applications/engineering-console.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/engineering-console.svg"
if [[ -f /etc/tmpfiles.d/engineering-console-rapl.conf ]]; then
  sudo rm -f /etc/tmpfiles.d/engineering-console-rapl.conf && echo "Removed RAPL rule (takes full effect after reboot)."
fi
if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "$HOME/.config/engineering-console" "$HOME/.local/share/engineering-console" "$HOME/.cache/engineering-console"
  rm -rf "$HOME/.config/system-cockpit" "$HOME/.local/share/system-cockpit" "$HOME/.cache/system-cockpit"
  echo "Settings and logs deleted."
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
echo "Engineering Console uninstalled."
