#!/usr/bin/env python3
"""DEV ONLY: launch the real UI with N fake GPUs to test layouts.  python3 tools/dev_mock.py 10"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import engineering_console.sensors as sensors
from engineering_console.gpu import detect_gpus
from tests.mock_backend import MockBackend

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def fake_detect(interval_ms=1000, root="/sys"):
    be = MockBackend(N)
    devs = be.detect()
    for i, d in enumerate(devs):
        d.index = i
    return devs, [be]


sensors.detect_gpus = fake_detect
from engineering_console import app
app.APP_TITLE_SUFFIX = "  [MOCK DATA - DEV ONLY]"
app.main()
