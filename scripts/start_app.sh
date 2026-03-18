#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if [[ "${1:-}" != "--no-install" ]]; then
  "$PYTHON_BIN" -m pip install -e '.[dev]' --no-build-isolation
fi

exec bash scripts/run_ui_server.sh
