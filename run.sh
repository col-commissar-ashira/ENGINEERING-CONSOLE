#!/usr/bin/env bash
# Run Engineering Console straight from this folder (no install needed). Needs PySide6 + psutil (see README).
cd "$(dirname "$0")" && exec python3 main.py "$@"
