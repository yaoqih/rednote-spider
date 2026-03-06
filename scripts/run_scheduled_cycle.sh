#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:-all}"
case "$MODE" in
  discover|opportunity|all) ;;
  *)
    echo "usage: bash scripts/run_scheduled_cycle.sh [discover|opportunity|all]" >&2
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
LOCK_DIR="${LOCK_ROOT%/}/rednote-${MODE}.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[scheduler] skip ${MODE}: lock exists (${LOCK_DIR})" >&2
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

run_discover() {
  local -a args=(
    scripts/run_discover_cycle.py
    --cycles "${SCHED_DISCOVER_CYCLES:-1}"
    --interval-seconds "${SCHED_DISCOVER_INTERVAL_SECONDS:-0}"
    --keyword-limit "${SCHED_DISCOVER_KEYWORD_LIMIT:-20}"
    --note-limit "${SCHED_DISCOVER_NOTE_LIMIT:-20}"
  )
  if [[ -n "${SCHED_DISCOVER_COMMAND_TEMPLATE:-}" ]]; then
    args+=(--command-template "${SCHED_DISCOVER_COMMAND_TEMPLATE}")
  fi
  "$PYTHON_BIN" "${args[@]}"
}

run_opportunity() {
  local task_id="${SCHED_OPPORTUNITY_TASK_ID:-0}"
  local prescreen_threshold="${SCHED_OPPORTUNITY_PRESCREEN_THRESHOLD:-3.2}"
  local match_threshold="${SCHED_OPPORTUNITY_MATCH_THRESHOLD:-0.26}"
  local retry_base_minutes="${SCHED_OPPORTUNITY_RETRY_BACKOFF_BASE_MINUTES:-5}"
  local retry_max_minutes="${SCHED_OPPORTUNITY_RETRY_BACKOFF_MAX_MINUTES:-720}"

  if [[ "$task_id" =~ ^[0-9]+$ ]] && [[ "$task_id" -gt 0 ]]; then
    "$PYTHON_BIN" scripts/run_product_opportunity_cycle.py \
      --task-id "$task_id" \
      --prescreen-threshold "$prescreen_threshold" \
      --match-threshold "$match_threshold" \
      --retry-backoff-base-minutes "$retry_base_minutes" \
      --retry-backoff-max-minutes "$retry_max_minutes"
    return
  fi

  "$PYTHON_BIN" scripts/run_product_opportunity_cycle.py \
    --limit-tasks "${SCHED_OPPORTUNITY_LIMIT_TASKS:-20}" \
    --prescreen-threshold "$prescreen_threshold" \
    --match-threshold "$match_threshold" \
    --retry-backoff-base-minutes "$retry_base_minutes" \
    --retry-backoff-max-minutes "$retry_max_minutes"
}

if [[ "$MODE" == "discover" ]]; then
  run_discover
elif [[ "$MODE" == "opportunity" ]]; then
  run_opportunity
else
  run_discover
  run_opportunity
fi
