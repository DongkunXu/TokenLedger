#!/usr/bin/env bash
# Deploy and run your own cloud ledger on Cloudflare (Workers + D1, the free plan is enough).
#   scripts/cloud.sh login                 sign in to Cloudflare (opens the browser once)
#   scripts/cloud.sh deploy <domain>       first deploy, e.g. tokens.example.com (a domain on your account)
#   scripts/cloud.sh deploy                deploy again after an update
#   scripts/cloud.sh google <client-id>    sign in with Google instead of passkeys (see docs/MULTI_MACHINE.md)
#   scripts/cloud.sh setup-code            a new link for creating the first account
#   scripts/cloud.sh recover <name>        a link that lets <name> add a passkey (lost every passkey)
#   scripts/cloud.sh status                what is deployed where
#   scripts/cloud.sh dev                   run it locally on http://127.0.0.1:8787 (local database)
# Your domain and database id are kept in data/cloud.json; the Cloudflare sign-in in .build/wrangler.
set -euo pipefail
source "$(dirname "$0")/env.sh"

export XDG_CONFIG_HOME="$BUILD/wrangler/config"
export WRANGLER_LOG_PATH="$BUILD/wrangler/logs"
export WRANGLER_SEND_METRICS="false"
mkdir -p "$XDG_CONFIG_HOME" "$WRANGLER_LOG_PATH"

cd "$ROOT/cloud"
[ node_modules/.package-lock.json -nt package-lock.json ] || npm ci --silent
cd "$ROOT/backend" && uv sync --quiet
exec uv run --quiet python "$ROOT/scripts/cloud.py" "$@"
