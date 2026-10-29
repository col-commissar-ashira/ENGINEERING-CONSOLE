## Which file do I download?

| You use | Download | Then |
|---|---|---|
| **Windows 10 / 11 (64-bit)** | `engineering-console-<version>-windows-x64-setup.exe` | Double-click it and follow the installer (no administrator password needed). If Windows says "Windows protected your PC": click **More info**, then **Run anyway**. |
| **Windows, no installation** | `engineering-console-<version>-windows-x64.zip` | Right-click > **Extract All**, open the folder, double-click **Engineering Console.exe**. |
| **Linux (any distribution, 64-bit Intel/AMD)** - Mint, Ubuntu, Debian, Fedora, Arch, ... | `engineering-console-<version>-linux-x86_64.AppImage` | Right-click > Properties > Permissions > tick "Allow executing as program" (or `chmod +x`), then double-click it. |
| **Linux Mint / Ubuntu / Debian** if you prefer a normal installed package | `engineering-console_<version>_all.deb` | Double-click it and press Install (or `sudo apt install ./engineering-console_<version>_all.deb`). |
| **Mac with Apple silicon (M1 or newer)** | `engineering-console-<version>-macos-arm64.dmg` | Open it, drag the app onto Applications, read "READ ME FIRST". |
| **Mac with an Intel processor** | `engineering-console-<version>-macos-x86_64.dmg` | Same as above. |

Not sure which Mac? Apple menu > About This Mac: "Chip: Apple M..." = arm64, "Processor: Intel ..." = x86_64.

* Everything the app needs (Python, Qt, psutil) is inside the download. Nothing else has to be installed.
* The Windows files are **not code-signed** and the macOS app is **not signed with an Apple Developer ID / notarized**, so Windows SmartScreen and macOS ask you to allow the app once (steps in the README).
* On Windows, CPU temperature, fan speed and CPU power need [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) running as administrator; without it those gauges show N/A with that explanation.
* `engineering-console-install-appimage.sh` is optional: it adds the AppImage to your application menu and can remove it again (`--uninstall`).
* `SHA256SUMS.txt` lets you verify a download:  `sha256sum -c SHA256SUMS.txt` (Linux), `shasum -a 256 -c SHA256SUMS.txt` (macOS) or `Get-FileHash <file>` in PowerShell (Windows).
* Licensed under the MIT License. Bundled third-party software (Qt/PySide6, psutil) keeps its own license, see `THIRD_PARTY_NOTICES.md`.
