# Engineering Console

**A spaceship-style hardware monitor for Linux, macOS and Windows.**
Big analog and digital gauges for CPU, RAM, any number of NVIDIA / AMD / Intel GPUs, disks, network, fans, temperatures, power and audio levels, plus a built-in internet speed test.

> **It never fakes a reading.** If your hardware or operating system does not expose a sensor, the gauge shows `N/A` and the tooltip tells you why and what to do about it.

Built with Python, PySide6 (Qt 6) and psutil · MIT License · Version 3.0.0
(Formerly "System Cockpit" - your settings are copied over automatically the first time you start 3.0.)

## Download and install

No Python, no compiling, no terminal needed. Open the **[latest release](../../releases/latest)**, scroll to **Assets**, and download the file for your computer:

| You use | Download this file | Then |
|---|---|---|
| **Linux - any distribution** (Mint, Ubuntu, Debian, Fedora, Arch, ...; 64-bit Intel/AMD) | `engineering-console-<version>-linux-x86_64.AppImage` | Right-click the file > **Properties > Permissions > Allow executing as program**, then double-click it. |
| **Linux Mint / Ubuntu / Debian** (installs like a normal program) | `engineering-console_<version>_all.deb` | Double-click it and press **Install**. |
| **Mac with Apple silicon** (M1 or newer) | `engineering-console-<version>-macos-arm64.dmg` | Open it and drag **Engineering Console** onto **Applications**. |
| **Mac with an Intel processor** | `engineering-console-<version>-macos-x86_64.dmg` | Same as above. |

*Not sure which Mac you have?* Apple menu > **About This Mac**. "Chip: Apple M..." means Apple silicon; "Processor: Intel ..." means Intel.
Not every release necessarily includes the Intel DMG - check the Assets list.

### Linux extras (optional)
* **Menu entry and icon for the AppImage:** download `engineering-console-install-appimage.sh` into the same folder as the AppImage and run `bash engineering-console-install-appimage.sh`. Undo it any time with `bash engineering-console-install-appimage.sh --uninstall`. It only writes inside your home folder and needs no root.
* **Remove the .deb:** `sudo apt remove engineering-console`
* **Verify a download:** put `SHA256SUMS.txt` in the same folder and run `sha256sum -c SHA256SUMS.txt --ignore-missing`.

### macOS: first launch
The app is **not signed with an Apple Developer ID and not notarized by Apple** (that requires a paid Apple developer account), so macOS will say it cannot verify it. Allow it once:

* **macOS 15 (Sequoia) and later:** try to open the app, click **Done**, then open **System Settings > Privacy & Security**, scroll down and click **Open Anyway** next to Engineering Console.
* **macOS 14 and earlier:** right-click (Control-click) the app > **Open** > **Open**.
* **Terminal alternative:** `xattr -dr com.apple.quarantine "/Applications/Engineering Console.app"`

Only do this for files you downloaded from this repository's Releases page. To verify, run `shasum -a 256 <the DMG file>` in Terminal and compare the result with the matching line in `SHA256SUMS.txt`.

### Something does not work?
See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** (AppImage and FUSE, Wayland, macOS "cannot be opened", gauges showing N/A, where settings and logs live).

## Platform status - what has and has not been tested

| Platform | State |
|---|---|
| **Linux** | Developed here. The `.deb` has been built and test-installed. The AppImage is built and start-tested automatically by GitHub Actions, but not by hand on every distribution. |
| **macOS** | Built and start-tested on GitHub's macOS machines, but **not yet checked by a person on a real Mac screen**. Not notarized. |
| **Windows 10 / 11** | Code and a build script exist, but it has **never been run on Windows** and **no Windows download is published**. |

The 3.0 PySide6 interface has been checked with automated tests that use a fake Qt (logic, painting code, themes, persistence). Those tests cannot prove how it looks or how dragging feels - if something looks off on your machine, please open an issue and include your OS and the output of `--sensors`.

## Building from source (developers and advanced users)

Python 3.9+ is required on every platform.

**Linux**
```bash
./install.sh            # per-user install; uses apt PySide6 packages if your release has them, else a private venv
./run.sh                # or run straight from this folder
```
or build and install the package: `./packaging/build_deb.sh && sudo apt install ./dist/engineering-console_3.0.0_all.deb`
(set `DEB_MAINTAINER` if you are not the original maintainer).
If PySide6 is not available from apt on your release, the app prints the exact commands for a virtual environment.

**Windows** - double-click `run_windows.bat` (creates a virtual environment and installs `requirements.txt`).
To build a folder you can copy around: `powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1`.

**macOS** - `bash run_macos.command` (creates a virtual environment). `bash packaging/macos/build_app.sh` builds the `.app` and the DMG for the Mac it runs on.

**Release builds** (what the Releases page offers): `bash packaging/linux/build_appimage.sh` (Linux x86_64 AppImage), `bash packaging/build_deb.sh` (`.deb`),
`bash packaging/macos/build_app.sh` (macOS). `.github/workflows/release.yml` runs all of them on GitHub when a release is **published** (tag `v` + the version in
`engineering_console/__init__.py`, e.g. `v3.0.0`) and attaches the files and `SHA256SUMS.txt` to that release. Maintainers: bump the
version in `__init__.py`, `pyproject.toml` and the AppStream metainfo first; the workflow refuses a tag that does not match.

Check what a machine exposes without opening the GUI: `python3 main.py --sensors`.

## Using it

* **Reorder gauges** - hold the **left mouse button** on a gauge, drag it onto another cell, release. A frame shows
  where it will land. The order is saved. Pinned gauges always stay in front (unpin one to move it across).
* **Right-click a gauge** - digital/analog, pin, warning/critical levels, maximum, reset peak, hide, run/cancel speed test.
* **Profiles** (Gaming, AI / Multi-GPU, Server, ...), **themes** (Engineering, Tactical, Minimal, High Contrast, Gold,
  Red Alert, Ice, Apple, Apple Dark, plus your own custom themes), a low-resolution mode, and a **Red Alert** for dangerous load.
  The Apple theme (light, the default Apple look) and Apple Dark share one complete Apple-style skin and do not affect the other themes. The old *Amber* and *Monochrome*
  themes were removed in 3.0; settings that used them fall back to Engineering.
* **Settings** are stored per user: Linux `~/.config/engineering-console`, Windows `%APPDATA%\Engineering Console`,
  macOS `~/Library/Application Support/Engineering Console`. Detailed diagnostics (JSON lines) go to `logs/` next
  to the data folder (Linux `~/.local/share/engineering-console/logs`, Windows `%LOCALAPPDATA%\Engineering Console\logs`).

### When a gauge has no data (View ▸ N/A gauges)

Pick how a gauge with no reading should look:

* **Show why (animated overlay)** - the default. The gauge is dimmed under drifting hazard stripes and a slow scan
  line, with a pulsing warning triangle, a headline (*sensor error*, *setup needed*, *permission needed*, *waiting for
  data*, *not exposed by this system*) and the sensor's own reason - for example the actual error text if a read
  failed. The reason is never made up: if a sensor gives none, the overlay says so.
* **Auto-hide until data returns** - a gauge that has had no data for 20 seconds steps aside and comes back by itself
  the moment data returns. Your gauge selection, order and profiles are never changed by this. The speed-test and
  audio gauges are exempt (they only start working while visible), and so are GPU panels and the per-core bank.
* **Plain N/A** - the classic look.

Read failures (a sensor that raised an error rather than simply not existing) are shown with the error text and
written once, with a traceback, to the log file.

### RAM reporting (comparable across Windows, macOS and Linux)

Every OS defines "used" memory differently, so Engineering Console uses one definition everywhere:

> **USED = total physical RAM − available RAM**

The gauges are *RAM USED*, *RAM FREE*, *THIS APP RAM* (Engineering Console plus its helper processes, measured as
**USS** - the memory that would be freed if it exited - or RSS, labelled as such, where USS cannot be read) and
*RAM USED (NO APP)* = used − this app. Hover a RAM gauge for the OS-specific breakdown (cache, wired, ...) - that
part is informational only. To compare operating systems at idle: close everything else, open only Engineering
Console, and compare **RAM USED (NO APP)**. Windows and macOS compress memory and Linux counts cache as available, so
differences of a few hundred MB between OSes are normal and not a measurement error.

### Speed test

Measures **HTTP latency** (not ICMP ping), download and upload with the bytes actually transferred and the real
elapsed time, against Cloudflare's public speed endpoint (you can list other compatible endpoints in the settings
file under `speed_endpoints`; they are tried in order).

* A failed test is **never shown as 0 Mbps**. The last valid result stays on the gauge, with a note such as
  `LAST VALID | FAILED: RATE LIMITED`. Testing, valid, failed, cancelled and "no data yet" look different.
* One test at a time; every request has a timeout; cancelling closes the connections immediately.
* If the server answers HTTP 429 (or 503 with Retry-After) the endpoint is left alone until the wait is over.
* **Sizes** (Tools ▸ Speed test size): **Quick** ≤ 12 MB per test (default, kind to data caps; may under-read very fast
  links), **Normal** ≤ 48 MB, **Full** ≤ 136 MB.
* **Continuous testing** (Tools ▸ Continuous speed test and data use): interval, duration, number of tests and a data
  limit that counts **download + upload together**. Session and all-time traffic totals are shown in MB/GB.
  Repeated failures back off (bounded) instead of hammering the server.

### Windows notes

Windows does not give normal programs CPU temperature, fan speed or CPU power. Start
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (as administrator) and
Engineering Console reads them from it; otherwise those gauges show N/A with this explanation. NVIDIA GPUs are read
through `nvidia-smi`; AMD and Intel GPUs show load and dedicated-VRAM use from Windows' GPU counters, while their
temperature/clock/power show N/A (vendor APIs are not integrated). Speaker-output level needs "Stereo Mix" or a
loopback device; the microphone meter works with the normal Windows microphone permission.

## Development

```bash
python3 tests/test_speedtest.py   # speed test vs local mock servers (240-cycle leak test, rate limits, cancel ...)
python3 tests/test_console.py     # themes, reorder + persistence, settings, RAM, Windows parsers, logging
python3 tests/test_na.py          # N/A overlay, auto-hide, error reasons
python3 tests/test_ui_logic.py    # window logic with a fake Qt
python3 tests/test_skins.py       # theme isolation (Apple vs the others)
python3 tests/test_backends.py    # GPU/sysfs/audio backends with fake hardware
python3 tests/test_macos.py       # macOS parsers with recorded output
python3 tests/test_repo_audit.py  # old names, fake-data patterns, licenses, packaging metadata
python3 tools/dev_mock.py 10      # real window with 10 fake GPUs (needs PySide6 and a display)
```
The UI tests use `tests/qt_stub`, a fake PySide6 that draws nothing. It proves the logic and the paint code run and
that no removed Qt 5 enum names are used; it cannot prove how anything looks or how dragging feels.

## License

Engineering Console is released under the **MIT License** - see [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright (c) 2026 Ashira Resevic.

The MIT License covers only this project's own code. Third-party components keep their own licenses and are not relicensed: PySide6 / Qt under the LGPL-3.0, psutil under BSD-3-Clause, and others. The AppImage and macOS app bundle them unmodified as separate, replaceable libraries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for details and license texts.
