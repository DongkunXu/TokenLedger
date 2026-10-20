#!/usr/bin/env bash
# Remove the hub service. The ledger in data/ and the sync settings are left untouched.
set -euo pipefail
case "$(uname -s)" in
Darwin)
  LABEL="io.github.dongkunxu.tokenledger.hub"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $PLIST"
  ;;
Linux)
  UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  systemctl --user disable --now tokenledger-hub.service 2>/dev/null || true
  rm -f "$UNITS/tokenledger-hub.service"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "removed the tokenledger-hub service"
  ;;
esac
