# Build the Windows downloads. Needs Windows 10/11 (64-bit), Python 3.9+ and internet access. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1
# Results (in dist\):
#   engineering-console-<version>-windows-x64-setup.exe   installer (per-user, no administrator rights needed)
#   engineering-console-<version>-windows-x64.zip         portable: unzip anywhere, run "Engineering Console.exe"
# Everything the app needs (Python, PySide6/Qt, psutil) is INSIDE these files; the user installs nothing else.
# One-folder bundle on purpose: the Qt libraries stay separate, replaceable files (LGPL-3.0).
# The exe is not code-signed, so Windows SmartScreen warns on first start (README explains the click path).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

function Run($exe, $argList) {                       # native commands do not stop the script on failure by themselves
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}

$ver = (Select-String -Path "engineering_console\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$name = "Engineering Console"
Write-Host "== Engineering Console $ver"

Remove-Item -Recurse -Force .buildvenv, "build\windows", "dist\$name" -ErrorAction SilentlyContinue
Run python @("-m", "venv", ".buildvenv")
$py = Join-Path $PWD ".buildvenv\Scripts\python.exe"
Run $py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Run $py @("-m", "pip", "install", "--quiet", "-r", "requirements.txt", "pyinstaller")

# App icon (.ico) rendered from icon.svg with PySide6's own renderer
New-Item -ItemType Directory -Force "build\windows" | Out-Null
Run $py @("packaging\windows\make_ico.py", "engineering_console\icon.svg", "build\windows\icon.ico")

Run $py @("-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", $name,
    "--distpath", "dist", "--workpath", "build\windows\work", "--specpath", "build\windows",
    "--icon", (Join-Path $PWD "build\windows\icon.ico"),
    "--add-data", "$PWD\engineering_console\icon.svg;engineering_console",
    "--hidden-import", "PySide6.QtMultimedia", "main.py")

$out = "dist\$name"
if (-not (Test-Path "$out\$name.exe")) { throw "PyInstaller did not produce $out\$name.exe" }
New-Item -ItemType Directory -Force "$out\licenses" | Out-Null
Copy-Item LICENSE, NOTICE, THIRD_PARTY_NOTICES.md $out
Copy-Item licenses\third-party\* "$out\licenses"
Run $py @("packaging\collect_licenses.py", "$out\licenses")

# Portable zip
$zip = "dist\engineering-console-$ver-windows-x64.zip"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $out -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Built: $zip"

# Installer (Inno Setup 6; installed on demand through Chocolatey if missing, e.g. on a fresh CI machine)
$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($c in "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                   "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") { if (Test-Path $c) { $iscc = $c; break } }
}
if (-not $iscc -and (Get-Command choco -ErrorAction SilentlyContinue)) {
    choco install innosetup --no-progress -y | Out-Host
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
if (-not $iscc -or -not (Test-Path $iscc)) {
    Write-Warning "Inno Setup 6 not found (https://jrsoftware.org/isdl.php) - only the portable zip was built."
    exit 0
}
Run $iscc @("/Qp", "/DAppVersion=$ver", "packaging\windows\installer.iss")
Write-Host "Built: dist\engineering-console-$ver-windows-x64-setup.exe"
