#!/bin/bash
# Run Engineering Console from source on macOS (creates a private virtual environment on first run).
# If double-clicking is blocked, open Terminal in this folder and run:   bash run_macos.command
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ or run: xcode-select --install"
  read -r -p "Press Return to close"; exit 1
fi
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -c "import PySide6.QtWidgets, PySide6.QtMultimedia, psutil" 2>/dev/null || pip install -r requirements.txt
exec python main.py "$@"
