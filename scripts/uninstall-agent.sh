#!/usr/bin/env bash
# Remove the scheduled capture (launchd on macOS, systemd timer on Linux). The ledger in data/ is left untouched.
set -euo pipefail
case "$(uname -s)" in
Darwin)
  LABEL="io.github.dongkunxu.tokenledger.capture"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $PLIST"
  ;;
Linux)
  UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  systemctl --user disable --now tokenledger-capture.timer 2>/dev/null || true
  rm -f "$UNITS/tokenledger-capture.timer" "$UNITS/tokenledger-capture.service"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "removed the tokenledger-capture timer"
  ;;
esac
