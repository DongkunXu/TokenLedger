#!/usr/bin/env bash
# Entry point for the scheduled capture (launchd). Keeps only the last run's output.
set -euo pipefail
source "$(dirname "$0")/env.sh"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
mkdir -p "$ROOT/.build/logs"
"$ROOT/scripts/tokenledger.sh" capture > "$ROOT/.build/logs/capture-last.log" 2>&1
