#!/usr/bin/env bash
# Remove build outputs, environments and caches. Never touches data/ (the ledger).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The dashboard and the hub keep their state and log in .build/run: stop them before removing them.
# (A hub installed as a service is restarted by it; remove it first with uninstall-hub-agent.sh.)
if [ -x "$ROOT/backend/.venv/bin/tokenledger" ]; then
  "$ROOT/scripts/dashboard.sh" stop >/dev/null
  "$ROOT/scripts/tokenledger.sh" hub stop >/dev/null
fi
rm -rf "$ROOT/.build" "$ROOT/backend/.venv" "$ROOT/frontend/node_modules" "$ROOT/frontend/dist"
find "$ROOT/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "cleaned build outputs (data/ untouched)"
