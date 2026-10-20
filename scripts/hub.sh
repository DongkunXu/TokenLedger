#!/usr/bin/env bash
# Manage the sync hub (on the machine that keeps the combined ledger).
#   scripts/hub.sh start      start in the background (it never stops by itself)
#   scripts/hub.sh stop       stop
#   scripts/hub.sh restart    restart; needed after backend code changes
#   scripts/hub.sh status     whether it runs, where, since when
#   scripts/hub.sh logs       log of the current / last run
#   scripts/hub.sh serve      run in this terminal instead; Ctrl+C stops it
# To start it with the machine, use scripts/install-hub-agent.sh instead of start.
set -euo pipefail
exec "$(dirname "$0")/tokenledger.sh" hub "${1:-status}" "${@:2}"
