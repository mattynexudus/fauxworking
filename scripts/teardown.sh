#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo ""
echo "⚠️  This will DELETE every record tracked in data/created-ids/*.json."
echo "    Deletion is strictly by tracked ID, never by name pattern — it"
echo "    will not touch anything this project didn't create."
echo ""
read -rp "Type 'yes' to confirm: " confirm
if [[ "$confirm" != "yes" ]]; then
  echo "Aborted."
  exit 1
fi

echo ""
python3 teardown.py
