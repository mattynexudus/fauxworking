#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found. Run 'python3 nexudus_auth.py setup' first (one-time login)."
  exit 1
fi

echo "--- Running daily update ---"
python3 generators/daily_update.py "$@"
