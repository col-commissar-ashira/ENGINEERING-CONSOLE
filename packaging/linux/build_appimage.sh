#!/usr/bin/env bash
# Build engineering-console-<version>-linux-x86_64.AppImage  (one file, runs on most x86_64 Linux distributions).
# Needs: Linux x86_64, Python 3.9+ with venv, internet access (PySide6, PyInstaller, appimagetool), and the xcb libraries below.
# For the widest compatibility build on an OLD distribution (the release workflow uses Ubuntu 22.04): an AppImage runs
# on systems with the same or a newer glibc than the one it was built on.
#   ./packaging/linux/build_appimage.sh        ->  dist/engineering-console-<version>-linux-x86_64.AppImage
set -euo pipefail
cd "$(dirname "$0")/../.."
[ "$(uname -m)" = x86_64 ] || { echo "This script builds the x86_64 AppImage; this machine is $(uname -m)." >&2; exit 1; }
VER=$(python3 -c 'import re;print(re.search(r"__version__ = \"([^\"]+)\"", open("engineering_console/__init__.py").read()).group(1))')
OUT="dist/engineering-console-${VER}-linux-x86_64.AppImage"
B=build/appimage; rm -rf "$B"; mkdir -p "$B" dist

python3 -m venv "$B/venv"
"$B/venv/bin/pip" install --quiet --upgrade pip
"$B/venv/bin/pip" install --quiet -r requirements.txt pyinstaller

# One-folder bundle on purpose: Qt stays as separate, replaceable shared libraries (LGPL-3.0).
"$B/venv/bin/pyinstaller" --noconfirm --clean --windowed --name engineering-console \
  --distpath "$B/pyi" --workpath "$B/work" --specpath "$B" \
  --add-data "$PWD/engineering_console/icon.svg:engineering_console" main.py

APPDIR="$B/EngineeringConsole.AppDir"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/doc/engineering-console/licenses"
cp -r "$B/pyi/engineering-console" "$APPDIR/usr/lib/engineering-console"

# Qt's X11 plugin needs these small xcb libraries; many distributions do not install them all, so ship them.
for lib in libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-image.so.0 libxcb-keysyms.so.1 libxcb-render-util.so.0 libxcb-xkb.so.1 libxcb-xinerama.so.0 libxkbcommon-x11.so.0; do
  SRC=$(ldconfig -p | awk -v l="$lib" '$1==l {print $NF; exit}')
  if [ -z "$SRC" ]; then
    echo "!! $lib not found on the build machine. Refusing to build an AppImage that may not open a window." >&2; exit 1
  fi
  for d in "$APPDIR/usr/lib/engineering-console" "$APPDIR/usr/lib/engineering-console/_internal"; do
    [ -d "$d" ] && cp -L "$SRC" "$d/" || true
  done
done

cp LICENSE NOTICE THIRD_PARTY_NOTICES.md "$APPDIR/usr/share/doc/engineering-console/"
cp licenses/third-party/* "$APPDIR/usr/share/doc/engineering-console/licenses/"
"$B/venv/bin/python" packaging/collect_licenses.py "$APPDIR/usr/share/doc/engineering-console/licenses"

install -Dm644 engineering_console/icon.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/engineering-console.svg"
"$B/venv/bin/python" packaging/linux/make_png.py engineering_console/icon.svg "$APPDIR/engineering-console.png" 256
cp "$APPDIR/engineering-console.png" "$APPDIR/.DirIcon"
install -Dm644 packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml \
  "$APPDIR/usr/share/metainfo/org.engineeringconsole.EngineeringConsole.appdata.xml"
cp packaging/engineering-console.desktop "$APPDIR/engineering-console.desktop"
sed -i 's/^Exec=.*/Exec=engineering-console/; s/^Icon=.*/Icon=engineering-console/' "$APPDIR/engineering-console.desktop"

cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
APP="$HERE/usr/lib/engineering-console"
export LD_LIBRARY_PATH="$APP/_internal:$APP${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$APP/engineering-console" "$@"
RUN
chmod 755 "$APPDIR/AppRun"

TOOL="$B/appimagetool-x86_64.AppImage"
curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x "$TOOL"
# --appimage-extract-and-run: works on build machines (CI containers) that have no FUSE
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
