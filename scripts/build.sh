#!/usr/bin/env bash
# Install dependencies and build the dashboard into frontend/dist.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$ROOT/backend" && uv sync --quiet
cd "$ROOT/frontend"
# Install when missing or when package-lock.json changed since (a pulled update)
[ node_modules/.package-lock.json -nt package-lock.json ] || npm ci --silent
npm run --silent build
