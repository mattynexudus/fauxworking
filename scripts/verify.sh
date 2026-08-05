#!/usr/bin/env bash
set -euo pipefail

echo "=== Record Count Verification ==="
echo ""

# TODO: Replace with actual nexudus MCP calls via Python.
# For now, this checks the local created-ids tracking files.

cd "$(dirname "$0")/.."

for f in data/created-ids/*.json; do
  if [[ -f "$f" ]]; then
    entity=$(basename "$f" .json)
    count=$(python -c "import json; print(len(json.load(open('$f'))))")
    printf "%-40s %s\n" "$entity" "$count"
  fi
done
