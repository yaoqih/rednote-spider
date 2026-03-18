#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if [[ -z "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="src"
fi

mapfile -t STREAMLIT_CONFIG < <("$PYTHON_BIN" - <<'PY'
from rednote_spider.config import settings
print(settings.streamlit_server_address)
print(settings.streamlit_server_port)
print("true" if settings.streamlit_server_headless else "false")
PY
)

STREAMLIT_ADDRESS="${STREAMLIT_SERVER_ADDRESS:-${STREAMLIT_CONFIG[0]:-127.0.0.1}}"
STREAMLIT_PORT="${STREAMLIT_SERVER_PORT:-${STREAMLIT_CONFIG[1]:-8501}}"
STREAMLIT_HEADLESS="${STREAMLIT_SERVER_HEADLESS:-${STREAMLIT_CONFIG[2]:-true}}"

"$PYTHON_BIN" scripts/init_schema.py
exec "$PYTHON_BIN" -m streamlit run ui/app.py \
  --server.address "$STREAMLIT_ADDRESS" \
  --server.port "$STREAMLIT_PORT" \
  --server.headless "$STREAMLIT_HEADLESS"
