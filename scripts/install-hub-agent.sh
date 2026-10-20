#!/usr/bin/env bash
# Keep the sync hub running on this machine: started at login (macOS launchd agent) or with
# the user's services (Linux systemd user service), and restarted if it stops.
# Run `scripts/tokenledger.sh sync hub-init` first. Windows: scripts/install-hub-task.ps1
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$ROOT/.build/logs"
"$ROOT/scripts/tokenledger.sh" hub stop >/dev/null 2>&1 || true  # the service takes over

case "$(uname -s)" in
Darwin)
  LABEL="io.github.dongkunxu.tokenledger.hub"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/hub-agent.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$ROOT/.build/logs/hub-agent.log</string>
  <key>StandardErrorPath</key><string>$ROOT/.build/logs/hub-agent.log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed $PLIST"
  echo "If macOS asks whether Python may accept incoming connections, allow it."
  ;;
Linux)
  UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNITS"
  cat > "$UNITS/tokenledger-hub.service" <<UNIT
[Unit]
Description=TokenLedger sync hub
After=network-online.target

[Service]
ExecStart=/bin/bash $ROOT/scripts/hub-agent.sh
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now tokenledger-hub.service
  echo "installed $UNITS/tokenledger-hub.service"
  echo "To keep it running while nobody is logged in: sudo loginctl enable-linger $USER"
  ;;
*)
  echo "unsupported system $(uname -s); on Windows use scripts/install-hub-task.ps1" >&2
  exit 1
  ;;
esac
