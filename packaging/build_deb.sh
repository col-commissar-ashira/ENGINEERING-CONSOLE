#!/usr/bin/env bash
# Build engineering-console_<version>_all.deb (Linux Mint / Ubuntu / Debian). Run from the project root.
#   ./packaging/build_deb.sh            (maintainer: Ashira Resevic <ashiraresevic@proton.me>)
# Override with DEB_MAINTAINER="Name <address>" if someone else builds it.
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c 'import re;print(re.search(r"__version__ = \"([^\"]+)\"", open("engineering_console/__init__.py").read()).group(1))')
MAINT=${DEB_MAINTAINER:-"Ashira Resevic <ashiraresevic@proton.me>"}
PKG=engineering-console
ROOT=build/deb/${PKG}_${VER}
rm -rf "$ROOT"
install -d "$ROOT/DEBIAN" "$ROOT/usr/lib/$PKG" "$ROOT/usr/bin" "$ROOT/usr/share/applications" \
  "$ROOT/usr/share/icons/hicolor/scalable/apps" "$ROOT/usr/share/metainfo" "$ROOT/usr/share/doc/$PKG/licenses"

cp main.py "$ROOT/usr/lib/$PKG/"
cp -r engineering_console "$ROOT/usr/lib/$PKG/"
find "$ROOT/usr/lib/$PKG" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 packaging/engineering-console.sh "$ROOT/usr/bin/engineering-console"
install -m 644 packaging/engineering-console.desktop "$ROOT/usr/share/applications/"
install -m 644 engineering_console/icon.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/engineering-console.svg"
install -m 644 packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml "$ROOT/usr/share/metainfo/"
install -m 644 LICENSE NOTICE THIRD_PARTY_NOTICES.md README.md TROUBLESHOOTING.md "$ROOT/usr/share/doc/$PKG/"
install -m 644 licenses/third-party/* "$ROOT/usr/share/doc/$PKG/licenses/"

cat > "$ROOT/usr/share/doc/$PKG/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Engineering Console
Upstream-Contact: Ashira Resevic <ashiraresevic@proton.me>

Files: *
Copyright: 2026 Ashira Resevic
License: MIT
 The full license text is in /usr/share/doc/$PKG/LICENSE.

Comment: Third-party software (PySide6/Qt, psutil) is not part of this package. It is installed from its own
 packages and keeps its own license; see /usr/share/doc/$PKG/THIRD_PARTY_NOTICES.md.
COPY

SIZE=$(du -sk "$ROOT/usr" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<CTRL
Package: $PKG
Version: $VER
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Maintainer: $MAINT
Depends: python3 (>= 3.9), python3-psutil
Recommends: python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, libqt6svg6, lm-sensors, pulseaudio-utils, pciutils
Description: Spaceship-style hardware monitoring console
 Analog and digital gauges for CPU, RAM, any number of NVIDIA, AMD and Intel
 GPUs, disks, network, fans, temperatures, power, audio levels and an internet
 speed test. Unavailable sensors show N/A with the reason; nothing is invented.
 .
 Needs PySide6 (Qt 6). If the Recommends above cannot be installed on your
 release, run:  python3 -m pip install PySide6   (see /usr/share/doc/$PKG).
CTRL
find "$ROOT" -type d -exec chmod 755 {} +
mkdir -p dist
dpkg-deb --build --root-owner-group "$ROOT" "dist/${PKG}_${VER}_all.deb"
echo "Built dist/${PKG}_${VER}_all.deb"
