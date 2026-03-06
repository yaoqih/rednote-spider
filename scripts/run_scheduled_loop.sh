#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-all}"
case "$MODE" in
  discover|opportunity|all) ;;
  *)
    echo "usage: bash scripts/run_scheduled_loop.sh [discover|opportunity|all]" >&2
    exit 2
    ;;
esac

if [[ "$MODE" == "discover" ]]; then
  INTERVAL_SECONDS="${SCHED_DISCOVER_LOOP_INTERVAL_SECONDS:-900}"
elif [[ "$MODE" == "opportunity" ]]; then
  INTERVAL_SECONDS="${SCHED_OPPORTUNITY_LOOP_INTERVAL_SECONDS:-600}"
else
  INTERVAL_SECONDS="${SCHED_ALL_LOOP_INTERVAL_SECONDS:-900}"
fi

if [[ ! "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || [[ "$INTERVAL_SECONDS" -lt 1 ]]; then
  echo "loop interval must be a positive integer, got: ${INTERVAL_SECONDS}" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

run_cycle_with_alert() {
  local cycle_log
  cycle_log="$(mktemp)"
  if bash scripts/run_scheduled_cycle.sh "$MODE" > >(tee "$cycle_log") 2> >(tee -a "$cycle_log" >&2); then
    rm -f "$cycle_log"
    return 0
  fi

  "$PYTHON_BIN" scripts/send_login_expiry_alert.py --mode "$MODE" --log-file "$cycle_log" || true
  rm -f "$cycle_log"
  return 1
}

while true; do
  if ! run_cycle_with_alert; then
    echo "[scheduler-loop] mode=${MODE} failed; continue after ${INTERVAL_SECONDS}s" >&2
  fi
  sleep "$INTERVAL_SECONDS"
done
