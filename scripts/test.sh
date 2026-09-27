#!/usr/bin/env bash
# Run backend tests (and frontend type checks when the frontend exists).
set -euo pipefail
source "$(dirname "$0")/env.sh"

cd "$ROOT/backend"
uv sync --quiet --group dev
uv run --quiet pytest "$@"

if [ -d "$ROOT/frontend/node_modules" ]; then
  cd "$ROOT/frontend"
  npm run --silent typecheck
fi
