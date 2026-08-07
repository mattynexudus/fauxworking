#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Record Count Verification ==="
echo "(counts tracked records in data/created-ids/*.json; for the configurable"
echo " headline volumes — see config.CONFIGURABLE_VOLUME_KEYS — the target is"
echo " whatever this account was actually generated with, not a fixed default)"
echo ""

python3 -c "
import sys
sys.path.insert(0, '.')
from report_lib import report_lines
print('\n'.join(report_lines()))
"
