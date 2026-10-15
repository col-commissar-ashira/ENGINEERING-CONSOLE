# Build a one-folder Windows bundle with PyInstaller. UNTESTED - needs Windows 10/11, Python 3.9+ and internet access.
# Run from the project root in PowerShell:   powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1
# Result: dist\Engineering Console\Engineering Console.exe   (one-folder on purpose: the Qt libraries stay separate,
# replaceable files, which the LGPL requires). The exe is not code-signed, so SmartScreen will warn on first start.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
python -m venv .buildvenv
& .\.buildvenv\Scripts\Activate.ps1
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --windowed --name "Engineering Console" `
  --add-data "engineering_console\icon.svg;engineering_console" `
  --hidden-import PySide6.QtMultimedia main.py
$out = "dist\Engineering Console"
New-Item -ItemType Directory -Force "$out\licenses" | Out-Null
Copy-Item LICENSE, NOTICE, THIRD_PARTY_NOTICES.md $out
Copy-Item licenses\third-party\* "$out\licenses"
python packaging\collect_licenses.py "$out\licenses"
Write-Host "Built: $out"
