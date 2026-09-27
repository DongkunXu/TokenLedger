#!/usr/bin/env bash
# MCP server on stdio. Register with: claude mcp add tokenledger -- <path>/scripts/mcp.sh
set -euo pipefail
source "$(dirname "$0")/env.sh" >/dev/null
cd "$ROOT/backend"
exec uv run --quiet --no-sync tokenledger mcp
