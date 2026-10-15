# Troubleshooting

## Linux - AppImage

**Nothing happens when I double-click it.** The file must be executable: right-click > Properties > Permissions >
"Allow executing as program" (or `chmod +x engineering-console-*.AppImage`). Some file managers ask whether to
"Run" or "Display" - choose Run. Start it from a terminal to see error messages: `./engineering-console-*.AppImage`.

**"AppImages require FUSE to run" / "dlopen(): error loading libfuse.so.2".** Newer Ubuntu/Debian/Mint releases no longer
ship FUSE 2. Either install it - Ubuntu/Debian/Mint: `sudo apt install libfuse2` (Ubuntu 24.04+: `libfuse2t64`);
Fedora: `sudo dnf install fuse-libs`; Arch: `sudo pacman -S fuse2` - or run without FUSE:
`./engineering-console-*.AppImage --appimage-extract-and-run`.

**No menu entry or icon.** AppImages are single files and do not install themselves. Use the optional
`engineering-console-install-appimage.sh` (see README) or just keep double-clicking the file.

**Window does not open on Wayland / "could not load the Qt platform plugin".** Try
`QT_QPA_PLATFORM=xcb ./engineering-console-*.AppImage`. Run it from a terminal and send the message you see when you report a bug.

**Many gauges say N/A.** That is deliberate: the app never invents readings. Hover the gauge for the reason. Typical fixes:
NVIDIA data needs the NVIDIA driver (`nvidia-smi`); temperatures/fans need `lm-sensors` (`sudo apt install lm-sensors && sudo sensors-detect`);
audio meters need `parec` (`pulseaudio-utils`); CPU package power may be restricted by the kernel.
`engineering-console --sensors` (or `./file.AppImage --sensors`) prints what your machine exposes.

## Linux - .deb

**"Dependency is not satisfiable: python3-pyside6..."** Your release has no PySide6 packages in apt. Use the AppImage instead.
**It installs but does not start.** Run `engineering-console` in a terminal; the message names the missing package.

## macOS

**"Engineering Console cannot be opened because the developer cannot be verified" / "is damaged".** The app is not
notarized by Apple. macOS 15+: System Settings > Privacy & Security > "Open Anyway". macOS 14 and earlier: right-click > Open.
Or, in Terminal: `xattr -dr com.apple.quarantine "/Applications/Engineering Console.app"`.
If you moved the app *out of the DMG window* before copying it, run it from Applications.

**Wrong download.** Apple-silicon Macs use the `arm64` DMG, Intel Macs the `x86_64` DMG. (Intel apps also run on Apple silicon via Rosetta, but not the other way round.)

**CPU/GPU power shows N/A.** macOS only allows this through `powermetrics`, which needs administrator rights. The app asks
for your password when you enable it; it never reads anything without asking. Microphone level needs the microphone permission.

## Settings and logs

Linux `~/.config/engineering-console`, macOS `~/Library/Application Support/Engineering Console`. Logs: Linux
`~/.local/share/engineering-console/logs`. Delete the settings folder to reset the app.
