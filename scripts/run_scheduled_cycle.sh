#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-discover}"
case "$MODE" in
  discover|all) ;;
  *)
    echo "usage: bash scripts/run_scheduled_cycle.sh [discover|all]" >&2
    exit 2
    ;;
esac

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if [[ -z "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="src"
fi

LOCK_ROOT="${SCHED_LOCK_ROOT:-/tmp}"
LOCK_MODE="discover"
LOCK_DIR="${LOCK_ROOT%/}/rednote-${LOCK_MODE}.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[scheduler] skip ${MODE}: lock exists (${LOCK_DIR})" >&2
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" scripts/run_managed_scheduler.py --mode discover
