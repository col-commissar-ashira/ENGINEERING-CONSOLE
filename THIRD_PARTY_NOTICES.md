# Third-party software

Engineering Console itself is licensed under the MIT License (`LICENSE`, `NOTICE`). The components below are
**not** part of this project, are **not** relicensed, and keep their own licenses. Nothing here replaces or overrides
them.

| Component | Used for | License | How it is used |
|---|---|---|---|
| **PySide6** (Qt for Python) and **Qt 6** | the whole GUI | LGPL-3.0 (also offered under GPL-3.0 / commercial terms by The Qt Company) | Not copied into this repository. Installed as a normal dependency from source/`.deb`; **bundled unmodified** (as separate, replaceable shared libraries) inside the AppImage and the macOS app. |
| **psutil** | CPU, memory, disk, network, process readings | BSD-3-Clause (`licenses/third-party/psutil-BSD-3-Clause.txt`) | Installed as a normal dependency. |
| `nvidia-smi` (NVIDIA driver) | NVIDIA GPU readings | NVIDIA proprietary driver license | Run as a separate program if the user's driver provides it. Not distributed. |
| LibreHardwareMonitor / OpenHardwareMonitor | optional Windows temperatures/fans/CPU power | MPL-2.0 | Never bundled. If the user runs it, its WMI sensors are read. |
| `parec` (PulseAudio/PipeWire), `lspci`, `powermetrics` (macOS) | audio meters, GPU names, Apple GPU/power data | their respective OS licenses | Separate programs already on the user's system. |
| PyInstaller | *build-time only*, to make AppImage/Windows/macOS bundles | GPL-2.0 with a bootloader exception that allows distributing the bundled app under any license | Not part of the application. |

## Why PySide6 and not PyQt5

PyQt5 is licensed GPL-3.0 (or commercial). Shipping an MIT-licensed application together with PyQt5 in a Windows or
macOS bundle would put that whole bundle under the GPL. PySide6 is LGPL-3.0, which allows an MIT-licensed application
to use and ship it as long as the LGPL conditions are met.

## If you distribute a bundle (Linux AppImage, Windows `.exe`, macOS `.app`)

Whoever distributes a bundle that contains PySide6/Qt must satisfy the LGPL-3.0 for those libraries:

* Build **one-folder** bundles (the provided scripts do) so the Qt libraries stay separate, replaceable files and
  are not merged into one executable.
* Ship the LGPL-3.0 and GPL-3.0 texts (`licenses/third-party/LGPL-3.0.txt`, `GPL-3.0.txt`), the license files that
  come inside the PySide6 and shiboken6 packages (the build scripts copy them into the bundle's `licenses/` folder),
  and state that the app uses Qt under the LGPL.
* Tell recipients where to get the corresponding Qt for Python / Qt source code: https://code.qt.io and
  https://pypi.org/project/PySide6/ .
* Do not add terms that prevent replacing the Qt libraries.

This file is a good-faith summary, not legal advice.
