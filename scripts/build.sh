#!/usr/bin/env bash
# Install dependencies and build the dashboard into frontend/dist.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$ROOT/backend" && uv sync --quiet
cd "$ROOT/frontend"
[ -d node_modules ] || npm ci --silent
npm run --silent build
