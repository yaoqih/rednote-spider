#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-discover}"
case "$MODE" in
  discover|all) ;;
  *)
    echo "usage: bash scripts/run_scheduled_loop.sh [discover|all]" >&2
    exit 2
    ;;
esac

INTERVAL_SECONDS="${SCHED_DISCOVER_LOOP_INTERVAL_SECONDS:-900}"

if [[ ! "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || [[ "$INTERVAL_SECONDS" -lt 1 ]]; then
  echo "loop interval must be a positive integer, got: ${INTERVAL_SECONDS}" >&2
  exit 2
fi

LAST_INTERVAL_SECONDS="$INTERVAL_SECONDS"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" scripts/init_schema.py

run_managed_cycle() {
  "$PYTHON_BIN" scripts/run_managed_scheduler.py --mode discover
}

run_cycle_with_alert() {
  local cycle_log payload next_interval last_line
  cycle_log="$(mktemp)"

  if payload="$(run_managed_cycle 2> >(tee -a "$cycle_log" >&2))"; then
    printf '%s\n' "$payload" | tee -a "$cycle_log"
    last_line="$(printf '%s\n' "$payload" | tail -n 1)"
    next_interval="$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.stdin.read()).get("loop_interval_seconds", 0)))' <<<"$last_line" 2>/dev/null || true)"
    if [[ "$next_interval" =~ ^[0-9]+$ ]] && [[ "$next_interval" -ge 1 ]]; then
      LAST_INTERVAL_SECONDS="$next_interval"
    fi
    rm -f "$cycle_log"
    return 0
  fi

  if [[ -n "${payload:-}" ]]; then
    printf '%s\n' "$payload" | tee -a "$cycle_log"
  fi
  "$PYTHON_BIN" scripts/send_login_expiry_alert.py --mode discover --log-file "$cycle_log" || true
  rm -f "$cycle_log"
  return 1
}

while true; do
  if ! run_cycle_with_alert; then
    echo "[scheduler-loop] mode=${MODE} failed; continue after ${LAST_INTERVAL_SECONDS}s" >&2
  fi
  sleep "$LAST_INTERVAL_SECONDS"
done
