# Shared environment for every TokenLedger script.
# Source it from bash; do not execute it. Keeps all caches and temp files inside the project.

if [ -z "${BASH_VERSION:-}" ] || [ -z "${BASH_SOURCE[0]:-}" ]; then
  echo "scripts/env.sh must be sourced from bash (e.g. bash -c 'source scripts/env.sh; ...')" >&2
  return 1 2>/dev/null || exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$ROOT/backend/pyproject.toml" ]; then
  echo "scripts/env.sh: could not locate the TokenLedger root (got $ROOT)" >&2
  return 1 2>/dev/null || exit 1
fi
export TOKENLEDGER_ROOT="$ROOT"
BUILD="$ROOT/.build"

export TMPDIR="$BUILD/tmp"
export UV_CACHE_DIR="$BUILD/uv-cache"
export UV_PYTHON_PREFERENCE="only-system"
export UV_PROJECT_ENVIRONMENT="$ROOT/backend/.venv"
export PYTHONPYCACHEPREFIX="$BUILD/pycache"
export npm_config_cache="$BUILD/npm-cache"
export npm_config_update_notifier="false"
export npm_config_fund="false"
export npm_config_audit="false"

mkdir -p "$TMPDIR" "$UV_CACHE_DIR" "$PYTHONPYCACHEPREFIX" "$npm_config_cache"

# Remove this script run's temp files on exit; the directory itself is reused.
tokenledger_cleanup_tmp() {
  find "$TMPDIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
}
trap tokenledger_cleanup_tmp EXIT
