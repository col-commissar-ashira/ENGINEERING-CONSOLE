#!/bin/bash
# Build "Engineering Console.app" and a DMG with PyInstaller. Run on a Mac (Python 3.9+, internet access).
# Result:  dist/Engineering Console.app
#          dist/engineering-console-<version>-macos-<arm64|x86_64>.dmg   (build on an Apple-silicon Mac for arm64, on an Intel Mac for x86_64)
# The app is ad-hoc signed only - NOT signed with an Apple Developer ID and NOT notarized (see README: first launch).
# One-folder app bundle on purpose: the Qt libraries stay separate, replaceable files (LGPL).
set -euo pipefail
cd "$(dirname "$0")/../.."
ARCH=$(uname -m)                          # arm64 or x86_64
VER=$(python3 -c 'import re;print(re.search(r"__version__ = \"([^\"]+)\"", open("engineering_console/__init__.py").read()).group(1))')
NAME="Engineering Console"
rm -rf .buildvenv build/macos "dist/$NAME.app" "dist/$NAME"
python3 -m venv .buildvenv && . .buildvenv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt pyinstaller
# App icon (Finder / Dock): render the SVG to .icns. If this fails the app is still built, just with the default icon.
mkdir -p build/macos
ICON_ARGS=()
if python packaging/macos/make_icns.py engineering_console/icon.svg build/macos/icon.icns; then ICON_ARGS=(--icon build/macos/icon.icns); else echo "!! icon creation failed - building without it"; fi
pyinstaller --noconfirm --clean --windowed --name "$NAME" --osx-bundle-identifier org.engineeringconsole.EngineeringConsole \
  --distpath dist --workpath build/macos/work --specpath build/macos \
  ${ICON_ARGS[@]+"${ICON_ARGS[@]}"} --add-data "$PWD/engineering_console/icon.svg:engineering_console" --hidden-import PySide6.QtMultimedia main.py
APP="dist/$NAME.app"
PLIST="$APP/Contents/Info.plist"
plutil -insert NSMicrophoneUsageDescription -string "Engineering Console measures the microphone volume level for the MIC INPUT gauge. Audio is never recorded or stored." "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VER" "$PLIST"
plutil -replace CFBundleVersion -string "$VER" "$PLIST"
plutil -insert LSMinimumSystemVersion -string "11.0" "$PLIST" || plutil -replace LSMinimumSystemVersion -string "11.0" "$PLIST"
plutil -insert NSHumanReadableCopyright -string "Copyright (c) 2026 Ashira Resevic. MIT License." "$PLIST" || true
RES="$APP/Contents/Resources/licenses"; mkdir -p "$RES"
cp LICENSE NOTICE THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/"
cp licenses/third-party/* "$RES/"
python packaging/collect_licenses.py "$RES"
# Ad-hoc signature (no Apple account needed). Apple-silicon Macs refuse to run completely unsigned code.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

# DMG with an Applications shortcut: open it, drag the app onto Applications.
STAGE=build/macos/dmg; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp packaging/macos/FIRST_LAUNCH.txt "$STAGE/READ ME FIRST.txt"
DMG="dist/engineering-console-${VER}-macos-${ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "$NAME $VER" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Built: $APP"
echo "Built: $DMG"
