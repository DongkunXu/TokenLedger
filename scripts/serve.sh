#!/usr/bin/env bash
# Build the dashboard if needed, then serve it at http://127.0.0.1:8765/ and open the browser.
set -euo pipefail
source "$(dirname "$0")/env.sh"
[ -f "$ROOT/frontend/dist/index.html" ] || "$ROOT/scripts/build.sh"
exec "$ROOT/scripts/tokenledger.sh" serve --open "$@"
