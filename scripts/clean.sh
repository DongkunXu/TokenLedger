#!/usr/bin/env bash
# Remove build outputs, environments and caches. Never touches data/ (the ledger).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The dashboard keeps its state and log in .build/run: stop it before removing them.
# (.build also holds the Cloudflare sign-in of scripts/cloud.sh; run `scripts/cloud.sh login` again later.)
if [ -x "$ROOT/backend/.venv/bin/tokenledger" ]; then
  "$ROOT/scripts/dashboard.sh" stop >/dev/null
fi
rm -rf "$ROOT/.build" "$ROOT/backend/.venv" "$ROOT/frontend/node_modules" "$ROOT/frontend/dist" \
  "$ROOT/cloud/node_modules" "$ROOT/cloud/.wrangler"
find "$ROOT/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "cleaned build outputs (data/ untouched)"
