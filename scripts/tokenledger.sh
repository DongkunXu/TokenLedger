#!/usr/bin/env bash
# Run the tokenledger CLI inside the project environment: ./scripts/tokenledger.sh <command> [...]
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$ROOT/backend"
uv sync --quiet --frozen 2>/dev/null || uv sync --quiet
exec uv run --quiet --no-sync tokenledger "$@"
