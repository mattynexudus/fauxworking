#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

LAYER="${1:?Usage: seed_layer.sh <layer_number> (e.g. 0, 1, 2...)}"

if [[ ! -f .env ]]; then
  echo "No .env found. Run 'python nexudus_auth.py setup' first (one-time login)."
  exit 1
fi

for gen in generators/0${LAYER}_*.py; do
  if [[ -f "$gen" ]]; then
    echo "--- Running $gen ---"
    python "$gen"
  else
    echo "No generator found for layer $LAYER"
    exit 1
  fi
done
