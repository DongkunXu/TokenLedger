#!/usr/bin/env bash
# Entry point for the hub service (launchd or systemd): runs the hub in the foreground.
set -euo pipefail
source "$(dirname "$0")/env.sh"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
exec "$ROOT/scripts/tokenledger.sh" hub serve
