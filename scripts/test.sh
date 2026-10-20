#!/usr/bin/env bash
# Run backend tests, and type checks of the frontend and the cloud Worker when their
# dependencies are installed.
#   scripts/test.sh [pytest args]     the usual tests
#   scripts/test.sh --cloud [...]     also the cloud ledger end to end (wrangler's local mode)
set -euo pipefail
source "$(dirname "$0")/env.sh"

if [ "${1:-}" = "--cloud" ]; then
  shift
  export TOKENLEDGER_CLOUD_TEST=1
  [ -d "$ROOT/cloud/node_modules" ] || (cd "$ROOT/cloud" && npm ci --silent)
fi

cd "$ROOT/backend"
uv sync --quiet --group dev
uv run --quiet pytest "$@"

if [ -d "$ROOT/frontend/node_modules" ]; then
  cd "$ROOT/frontend"
  npm run --silent typecheck
fi
if [ -d "$ROOT/cloud/node_modules" ]; then
  cd "$ROOT/cloud"
  npm run --silent typecheck
fi
