#!/bin/sh
# Launcher installed as /usr/bin/engineering-console.
# Uses a virtual environment with PySide6 if one exists (see /usr/share/doc/engineering-console/README), else system Python.
APP=/usr/lib/engineering-console
USERVENV="${XDG_DATA_HOME:-$HOME/.local/share}/engineering-console/venv"
if [ -x "$USERVENV/bin/python" ]; then exec "$USERVENV/bin/python" "$APP/main.py" "$@"; fi
exec python3 "$APP/main.py" "$@"
