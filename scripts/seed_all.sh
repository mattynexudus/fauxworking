#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Nexudus Test Data Seeder ==="
echo "Running all layers in order..."

for gen in generators/0{0,1,2,3,4,5,6,7}_*.py; do
  echo ""
  echo "--- Running $gen ---"
  python "$gen"
done

echo ""
echo "=== All layers complete ==="
echo "Run 'bash scripts/verify.sh' to validate record counts."
