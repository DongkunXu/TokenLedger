#!/usr/bin/env bash
# Remove build outputs, environments and caches. Never touches data/ (the ledger).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$ROOT/.build" "$ROOT/backend/.venv" "$ROOT/frontend/node_modules" "$ROOT/frontend/dist"
find "$ROOT/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "cleaned build outputs (data/ untouched)"
