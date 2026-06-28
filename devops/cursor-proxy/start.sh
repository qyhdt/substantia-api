#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${SUBSTANTIA_API_KEY:?Set SUBSTANTIA_API_KEY in devops/cursor-proxy/.env}"

PY="${PY:-python3}"
if [[ -x "../../backend/.venv/bin/python" ]]; then
  PY="../../backend/.venv/bin/python"
elif [[ -x "../../../fixbot/.venv/bin/python" ]]; then
  PY="../../../fixbot/.venv/bin/python"
fi

exec "$PY" -m uvicorn proxy:app --host 127.0.0.1 --port "${CURSOR_PROXY_PORT:-8765}" --app-dir "$(pwd)"
