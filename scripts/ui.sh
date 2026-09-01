#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# No .env gate here on purpose — the panel shows an auth banner and can log
# you in from the browser. Everything it *runs* still checks auth itself.
#
# --host / --port pass straight through to the server (defaults: 127.0.0.1:8765,
# or $FAUXWORKING_UI_HOST / $FAUXWORKING_UI_PORT).
exec python3 webui/server.py "$@"
