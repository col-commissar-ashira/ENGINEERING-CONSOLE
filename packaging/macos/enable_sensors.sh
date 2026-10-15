#!/bin/bash
# ONE-TIME setup so Engineering Console can read CPU/GPU power on a Mac without asking for your password at every launch.
# It adds a sudoers rule that lets YOUR user run Apple's powermetrics with exactly the arguments the app uses - nothing else.
# (Because the arguments are fixed, the rule cannot be used to run other commands or to write files as root.)
#   Install:  bash packaging/macos/enable_sensors.sh          Remove:  bash packaging/macos/enable_sensors.sh --remove
set -euo pipefail
cd "$(dirname "$0")/../.."
FILE=/etc/sudoers.d/engineering-console
if [[ "${1:-}" == "--remove" ]]; then sudo rm -f "$FILE"; echo "Removed $FILE"; exit 0; fi
RULE=$(python3 main.py --mac-sudoers "$(whoami)")
TMP=$(mktemp)
echo "$RULE" > "$TMP"
echo "Rule to install:"; echo "  $RULE"
sudo visudo -cf "$TMP"                                   # refuses to continue if the rule is not valid
sudo install -m 440 -o root -g wheel "$TMP" "$FILE"
rm -f "$TMP"
sudo -k                                                  # forget the password just typed, to prove the rule works alone
if sudo -n -l /usr/bin/powermetrics $(python3 - <<'PY'
import sys; sys.path.insert(0, ".")
from engineering_console import pmrule
print(" ".join(pmrule.args()))
PY
) >/dev/null 2>&1; then echo "OK: Engineering Console will no longer ask for a password."; else echo "Installed, but the check failed - run: sudo -n -l"; fi
