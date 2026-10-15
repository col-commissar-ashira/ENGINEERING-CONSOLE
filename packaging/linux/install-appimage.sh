#!/usr/bin/env bash
# Optional: add the Engineering Console AppImage to your application menu (per user, no root needed).
#   bash install-appimage.sh [path/to/engineering-console-*.AppImage]     install (default: the AppImage next to this script)
#   bash install-appimage.sh --uninstall                                  remove everything this script added
# Nothing outside your home folder is touched. Settings are kept unless you delete ~/.config/engineering-console yourself.
set -euo pipefail
DEST="$HOME/.local/share/engineering-console-appimage"
DESK="$HOME/.local/share/applications/engineering-console.desktop"
ICON="$HOME/.local/share/icons/hicolor/256x256/apps/engineering-console.png"
refresh() { command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
            command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true; }
if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$DEST"; rm -f "$DESK" "$ICON"; refresh
  echo "Engineering Console removed from your menu. (Your settings in ~/.config/engineering-console were kept.)"; exit 0
fi
SRC="${1:-}"
if [ -z "$SRC" ]; then SRC=$(ls -1 "$(dirname "$0")"/engineering-console-*-linux-x86_64.AppImage 2>/dev/null | head -n1 || true); fi
[ -n "$SRC" ] && [ -f "$SRC" ] || { echo "Could not find the AppImage. Put this script in the same folder as the downloaded .AppImage, or give its path:  bash $0 /path/to/file.AppImage" >&2; exit 1; }
mkdir -p "$DEST" "$(dirname "$DESK")" "$(dirname "$ICON")"
install -m 755 "$SRC" "$DEST/engineering-console.AppImage"
# Pull the icon out of the AppImage (works without FUSE)
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
( cd "$TMP" && "$DEST/engineering-console.AppImage" --appimage-extract engineering-console.png >/dev/null 2>&1 ) || true
if [ -f "$TMP/squashfs-root/engineering-console.png" ]; then install -m 644 "$TMP/squashfs-root/engineering-console.png" "$ICON"; fi
cat > "$DESK" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Engineering Console
GenericName=Hardware Monitor
Comment=Spaceship-style instrument panel for CPU, GPUs, memory, disk, network and sensors
Exec="$DEST/engineering-console.AppImage"
Icon=engineering-console
Terminal=false
Categories=System;Monitor;
StartupWMClass=engineering-console
DESKTOP
refresh
echo "Installed. Find 'Engineering Console' in your application menu (log out and in if it does not show up yet)."
echo "Remove it later with:  bash $0 --uninstall"
