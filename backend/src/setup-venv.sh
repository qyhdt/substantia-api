#!/usr/bin/env bash
# 创建 .venv 并安装依赖。
# 用法：./setup-venv.sh
set -euo pipefail

cd "$(dirname "$0")"

PY=${PYTHON:-python3}

if [ ! -d ".venv" ]; then
    "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "✅ venv ready. Activate with:  source .venv/bin/activate"
