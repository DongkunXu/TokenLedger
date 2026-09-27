#!/usr/bin/env bash
# Remove the scheduled capture agent. The ledger data in data/ is left untouched.
set -euo pipefail
LABEL="io.github.dongkunxu.tokenledger.capture"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "removed $PLIST"
