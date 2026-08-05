#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found. Run 'python3 nexudus_auth.py setup' first (one-time login)."
  exit 1
fi

echo "=== Nexudus Test Data Seeder ==="
echo "Running all layers 0-7 in one pass..."
echo ""

python3 pipeline.py

echo ""
echo "=== All layers complete ==="
echo "Run 'bash scripts/verify.sh' to validate record counts."
