#!/usr/bin/env bash
# ─────────────────────────────────────────
#  run.sh — 一键启动 Claude Code 容器
#
#  用法:
#    ./run.sh                      # 交互模式（进入 bash）
#    ./run.sh -p "帮我检查代码"     # 非交互，直接执行任务
#
#  机制：
#    1. 自动 source 同目录下 .env.dev（或 ENV_FILE 指定的文件）
#       —— WORKSPACE_DIR、ANTHROPIC_*、CLAUDE_CODE_* 全在这里配
#    2. -e VAR（不带值）让 docker 从当前 shell 取同名变量传进容器
#    3. WORKSPACE_DIR 未设置则默认挂 $(pwd)
#    4. ENABLE_PROXY=1 时透传 Clash 代理（默认关闭，避免 VPN 未开时报错）
#       用法: ENABLE_PROXY=1 ./run.sh
# ─────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${IMAGE:-claude-runner}"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env.dev}"

if [ -f "$ENV_FILE" ]; then
  # set -a：source 期间自动 export，让 -e VAR 能从当前 env 取到
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo ">>> 未找到 $ENV_FILE；将仅依赖当前 shell 已有的环境变量"
  echo ">>> 建议: cp .env.dev.example .env.dev 后填值"
fi

LOCAL_DIR="${WORKSPACE_DIR:-$(pwd)}"
if [ ! -d "$LOCAL_DIR" ]; then
  echo "ERROR: WORKSPACE_DIR=$LOCAL_DIR 不存在" >&2
  exit 1
fi

# 镜像不存在则构建
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  # 在 host 预拉 Linux 二进制：Docker Desktop for Mac 容器网络拿不到 host 上的内网代理路由
  # 所以镜像构建期不能跑 npm install，改为在 host 拉好后 COPY 进镜像
  CLAUDE_BIN="$SCRIPT_DIR/claude-install/node_modules/@anthropic-ai/claude-code-linux-arm64/claude"
  if [ ! -x "$CLAUDE_BIN" ]; then
    echo ">>> 在 host 预拉 claude-code Linux 二进制..."
    mkdir -p "$SCRIPT_DIR/claude-install"
    ( cd "$SCRIPT_DIR/claude-install" \
        && [ -f package.json ] || echo '{"name":"claude-install","version":"1.0.0","private":true}' > package.json \
        && npm install --os=linux --cpu=arm64 --libc=glibc @anthropic-ai/claude-code )
  fi

  echo ">>> 首次运行，构建镜像..."
  docker build -f "$SCRIPT_DIR/Dockerfile.claude" -t "$IMAGE" "$SCRIPT_DIR"
fi

# 透传给容器的环境变量清单。-e VAR 不带值 = 从当前 shell 取
ENV_PASS=(
  -e ANTHROPIC_BASE_URL
  -e ANTHROPIC_AUTH_TOKEN
  -e ANTHROPIC_API_KEY
  -e ANTHROPIC_MODEL
  -e ANTHROPIC_DEFAULT_OPUS_MODEL
  -e ANTHROPIC_DEFAULT_SONNET_MODEL
  -e ANTHROPIC_DEFAULT_HAIKU_MODEL
  -e CLAUDE_CODE_SUBAGENT_MODEL
  -e CLAUDE_CODE_EFFORT_LEVEL
)

# VPN 代理：ENABLE_PROXY=1 ./run.sh 时启用
# 关闭 VPN 时不传，避免容器内请求报错
if [ "${ENABLE_PROXY:-0}" = "1" ]; then
  echo ">>> 已启用代理 host.docker.internal:7890"
  ENV_PASS+=(
    -e HTTP_PROXY=http://host.docker.internal:7890
    -e HTTPS_PROXY=http://host.docker.internal:7890
    -e http_proxy=http://host.docker.internal:7890
    -e https_proxy=http://host.docker.internal:7890
    -e NO_PROXY=localhost,127.0.0.1
    -e no_proxy=localhost,127.0.0.1
  )
fi

# 非交互：./run.sh -p "..."
if [ "${1:-}" = "-p" ] && [ -n "${2:-}" ]; then
  exec docker run --rm \
    --add-host=host.docker.internal:host-gateway \
    "${ENV_PASS[@]}" \
    -v "$LOCAL_DIR":/workspace \
    "$IMAGE" \
    claude --dangerously-skip-permissions -p "$2"
fi

# 交互模式
exec docker run --rm -it \
  --add-host=host.docker.internal:host-gateway \
  "${ENV_PASS[@]}" \
  -v "$LOCAL_DIR":/workspace \
  "$IMAGE" \
  bash
