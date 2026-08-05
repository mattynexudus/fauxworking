#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo ""
echo "⚠️  This will DELETE all test data (records matching test markers)."
echo "    It will NOT touch any non-test records."
echo ""
read -rp "Type 'yes' to confirm: " confirm
if [[ "$confirm" != "yes" ]]; then
  echo "Aborted."
  exit 1
fi

echo "Teardown not yet implemented — add entity-specific delete loops here."
# TODO: For each entity in reverse dependency order:
#   1. Load data/created-ids/<entity>.json
#   2. Delete each record via nexudus <entity> delete <id> --yes --agent
#   3. Clear the tracking file
