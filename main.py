#!/usr/bin/env python3
"""Engineering Console entry point."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "--version" in sys.argv:
    from engineering_console import __version__
    print("Engineering Console", __version__)
    sys.exit(0)

if "--mac-sudoers" in sys.argv:       # used by packaging/macos/enable_sensors.sh
    import getpass
    from engineering_console import pmrule
    i = sys.argv.index("--mac-sudoers")
    print(pmrule.sudoers_rule(sys.argv[i + 1] if len(sys.argv) > i + 1 else getpass.getuser()))
    sys.exit(0)

if "--sensors" in sys.argv:          # headless sensor report, no GUI needed
    from engineering_console.sensors import cli_report
    cli_report()
    sys.exit(0)

missing = []
for mod, pip_name, apt_name in (("PySide6.QtWidgets", "PySide6", "python3-pyside6.qtwidgets python3-pyside6.qtgui "
                                                                   "python3-pyside6.qtcore"),
                                ("psutil", "psutil", "python3-psutil")):
    try:
        __import__(mod)
    except ImportError:
        missing.append((pip_name, apt_name))
if missing:
    pip = " ".join(p for p, _ in missing)
    if sys.platform.startswith("linux"):
        print("Engineering Console is missing dependencies. Install them with:\n"
              "  sudo apt install " + " ".join(a for _, a in missing) + "\n"
              "If apt has no PySide6 packages for your release, use a virtual environment instead:\n"
              "  python3 -m venv --system-site-packages ~/.local/share/engineering-console/venv\n"
              "  ~/.local/share/engineering-console/venv/bin/pip install " + pip, file=sys.stderr)
    else:
        print("Engineering Console is missing dependencies. Install them with:\n"
              f"  python -m pip install {pip}", file=sys.stderr)
    sys.exit(1)

from engineering_console.app import main
main()
