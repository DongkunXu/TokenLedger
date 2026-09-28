#!/usr/bin/env bash
# Manage the dashboard server.
#   scripts/dashboard.sh start [--open]   start in the background (detached from this terminal)
#   scripts/dashboard.sh open             start if needed and open the browser
#   scripts/dashboard.sh stop             stop gracefully
#   scripts/dashboard.sh restart          stop, then start (needed after backend changes)
#   scripts/dashboard.sh status           is it running, where, since when
#   scripts/dashboard.sh logs             log of the current / last run
#   scripts/dashboard.sh run              run in this terminal instead (Ctrl+C stops it)
# Options: --port N (default 8765), --idle-hours H (default 12; 0 = never stop by itself)
set -euo pipefail
source "$(dirname "$0")/env.sh"
action="${1:-status}"
[ $# -gt 0 ] && shift

case "$action" in
  start|open|restart|run)
    [ -f "$ROOT/frontend/dist/index.html" ] || "$ROOT/scripts/build.sh" ;;
esac

if [ "$action" = "run" ]; then
  exec "$ROOT/scripts/tokenledger.sh" serve --open "$@"
fi
exec "$ROOT/scripts/tokenledger.sh" dashboard "$action" "$@"
