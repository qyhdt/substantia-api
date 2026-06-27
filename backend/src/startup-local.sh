#!/usr/bin/env bash
# 本地启动：uvicorn 单进程 + 热重载
# 用法：./startup-local.sh
set -euo pipefail

cd "$(dirname "$0")"

# 加载 .env 中的 PORT（若有）；默认 9999
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi
PORT=${PORT:-9999}
HOST=${HOST:-0.0.0.0}

# 优先使用本目录 venv
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

exec uvicorn main:app --host "$HOST" --port "$PORT" --reload
