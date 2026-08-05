#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "--- Running daily update ---"
python generators/daily_update.py "$@"
