#!/usr/bin/env python3
"""Fail unless the release tag matches the version inside the project, so file names and the app never disagree.
usage: check_version.py [vX.Y.Z]      (no argument: only check that the project files agree with each other)
Prints the version on success."""
import re
import sys

def find(path, pat):
    m = re.search(pat, open(path, encoding="utf-8").read())
    if not m:
        sys.exit(f"ERROR: cannot find the version in {path}")
    return m.group(1)

versions = {
    "engineering_console/__init__.py": find("engineering_console/__init__.py", r'__version__ = "([^"]+)"'),
    "pyproject.toml": find("pyproject.toml", r'(?m)^version = "([^"]+)"'),
    "AppStream metainfo": find("packaging/org.engineeringconsole.EngineeringConsole.metainfo.xml", r'<release version="([^"]+)"'),
}
if len(set(versions.values())) != 1:
    sys.exit("ERROR: version numbers disagree inside the project: " + ", ".join(f"{k}={v}" for k, v in versions.items()))
ver = next(iter(versions.values()))
if len(sys.argv) > 1:
    tag = sys.argv[1].removeprefix("refs/tags/")
    if tag != "v" + ver:
        sys.exit(f"ERROR: release tag is '{tag}' but the project version is {ver}. Change the version in the 3 files above "
                 f"(or use the tag v{ver}), commit, and publish the release again.")
print(ver)
