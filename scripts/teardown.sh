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

echo "Teardown deletes real records via the Nexudus MCP connector, which"
echo "this shell script cannot call directly (see teardown.py for why every"
echo "live operation in this repo is agent-orchestrated, not CLI-driven)."
echo "Ask the agent to run teardown live, or dry-run it yourself below."
echo ""
python3 teardown.py --dry-run
