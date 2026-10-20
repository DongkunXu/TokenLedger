#!/usr/bin/env bash
# Install the scheduled capture: twice a day (09:00 and 21:00; missed runs happen on wake or boot).
# When signed in to a cloud ledger, every capture also syncs with it.
#   macOS: a launchd agent. Linux: a systemd user timer. Windows: scripts/install-agent.ps1
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$ROOT/.build/logs"

case "$(uname -s)" in
Darwin)
  LABEL="io.github.dongkunxu.tokenledger.capture"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/capture-agent.sh</string></array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$ROOT/.build/logs/agent.log</string>
  <key>StandardErrorPath</key><string>$ROOT/.build/logs/agent.log</string>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed $PLIST"
  ;;
Linux)
  UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNITS"
  cat > "$UNITS/tokenledger-capture.service" <<UNIT
[Unit]
Description=TokenLedger: capture AI usage (and sync with the cloud ledger)

[Service]
Type=oneshot
ExecStart=/bin/bash $ROOT/scripts/capture-agent.sh
Nice=10
UNIT
  cat > "$UNITS/tokenledger-capture.timer" <<UNIT
[Unit]
Description=TokenLedger capture at 09:00 and 21:00

[Timer]
OnCalendar=*-*-* 09:00:00
OnCalendar=*-*-* 21:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now tokenledger-capture.timer
  echo "installed $UNITS/tokenledger-capture.timer"
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "note: user timers run only while you are logged in; for a machine nobody logs in to, run" >&2
    echo "      sudo loginctl enable-linger $USER" >&2
  fi
  ;;
*)
  echo "unsupported system $(uname -s); on Windows use scripts/install-agent.ps1" >&2
  exit 1
  ;;
esac
