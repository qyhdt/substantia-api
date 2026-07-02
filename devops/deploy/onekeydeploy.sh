#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  onekeydeploy.sh — 本地一键远端部署 substantia-api 全栈（db + backend + web）
#
#  在【本地 Mac】执行，通过 ssh 驱动远端把最新代码部署上线：
#     把本地仓库的当前提交同步到远端 → 远端 docker compose 重建并起栈 → 健康检查。
#
#  用法（需已配好免密 ssh work@HOST）：
#     ./devops/deploy/onekeydeploy.sh                 # 部署当前分支最新提交
#     SERVICE=backend ./devops/deploy/onekeydeploy.sh # 只重建某个服务（backend/web/db）
#     NO_BUILD=1 ./devops/deploy/onekeydeploy.sh       # 不重新 build，仅 up（改了 .env 时用）
#     SYNC=rsync ./devops/deploy/onekeydeploy.sh       # 用 rsync 同步工作区（默认 git 同步已提交内容）
#
#  环境变量（都有默认值）：
#     HOST          远端 IP，默认 8.216.44.14
#     REMOTE_USER   远端用户，默认 work
#     REPO_DIR      远端仓库路径，默认 ~/substantia-api
#     BRANCH        要部署的分支，默认当前本地分支
#     SERVICE       只操作某服务（backend|web|db），默认全栈
#     SYNC          git（默认，推送已提交内容）| rsync（同步工作区，含未提交改动）
#     NO_BUILD=1    跳过 --build（仅重启/重拉）
#     HEALTH_URL    部署后健康检查 URL，默认 https://api.substantia.ai/api/health
#
#  前置条件（远端，首次部署需先人工就绪，本脚本不代办）：
#     - 远端已 clone 仓库到 REPO_DIR，且 docker / docker compose 可用
#     - devops/deploy/.env 已在远端配好（密钥不入库，本脚本不覆盖）
#     - 外部网络 substantia_net 已存在、claude slot 镜像/凭据已就绪
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HOST="${HOST:-8.216.44.14}"
REMOTE_USER="${REMOTE_USER:-work}"
REPO_DIR="${REPO_DIR:-\$HOME/substantia-api}"   # 远端展开，故转义 $HOME
SERVICE="${SERVICE:-}"
SYNC="${SYNC:-git}"
NO_BUILD="${NO_BUILD:-0}"
HEALTH_URL="${HEALTH_URL:-https://api.substantia.ai/api/health}"
DEPLOY_SUBDIR="devops/deploy"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=240)
SSH=(ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}")

say()  { printf '\n\033[1;36m>>> %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# 定位本地仓库根（脚本在 devops/deploy/ 下）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
COMMIT="$(git rev-parse --short HEAD)"

# compose 的 --build 与 service 选择
BUILD_FLAG="--build"; [ "$NO_BUILD" = "1" ] && BUILD_FLAG=""
SVC_ARG="${SERVICE:-}"

say "部署目标：${REMOTE_USER}@${HOST}:${REPO_DIR}"
echo "    分支=${BRANCH}  提交=${COMMIT}  同步=${SYNC}  服务=${SERVICE:-全栈}  build=$([ "$NO_BUILD" = 1 ] && echo no || echo yes)"

# ── 0. 连通性自检 ────────────────────────────────────────────────────────────
"${SSH[@]}" "command -v docker >/dev/null || { echo 'remote: docker missing'; exit 1; }
  docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null 2>&1 || { echo 'remote: docker compose missing'; exit 1; }" \
  || die "远端环境自检失败（ssh 不通 / docker 缺失）"

# ── 1. 同步代码到远端 ────────────────────────────────────────────────────────
if [ "$SYNC" = "rsync" ]; then
  say "rsync 同步工作区到远端（含未提交改动，排除 .git/node_modules/.venv 等）"
  command -v rsync >/dev/null || die "本地缺 rsync"
  # 远端先确保目录存在
  "${SSH[@]}" "mkdir -p \"${REPO_DIR}\""
  rsync -az --delete \
    --exclude '.git/' --exclude 'node_modules/' --exclude '.venv/' \
    --exclude 'dist/' --exclude '__pycache__/' --exclude '*.log' \
    --exclude "${DEPLOY_SUBDIR}/.env" --exclude "${DEPLOY_SUBDIR}/logs/" \
    -e "ssh ${SSH_OPTS[*]}" \
    ./ "${REMOTE_USER}@${HOST}:${REPO_DIR}/"
else
  say "git 同步：推送当前提交并在远端 checkout（仅已提交内容）"
  # 本地未提交改动提醒（不阻断，git 模式只部署已提交内容）
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "    ⚠️  工作区有未提交改动，git 模式不会部署它们（如需带上请用 SYNC=rsync）"
  fi
  git push origin "${BRANCH}" || die "git push 失败（远端仓库/权限？）"
  "${SSH[@]}" "set -e
    cd \"${REPO_DIR}\" || { echo 'remote: REPO_DIR 不存在，请先 clone'; exit 1; }
    git fetch --all --prune
    git checkout \"${BRANCH}\"
    git reset --hard \"origin/${BRANCH}\"
    echo \">>> 远端已对齐 origin/${BRANCH} @ \$(git rev-parse --short HEAD)\""
fi

# ── 2. 远端 compose 重建并起栈 ───────────────────────────────────────────────
say "远端 docker compose up ${BUILD_FLAG:-（不 build）} ${SVC_ARG:+（仅 $SVC_ARG）}"
"${SSH[@]}" "set -e
  cd \"${REPO_DIR}/${DEPLOY_SUBDIR}\"
  [ -f .env ] || { echo 'remote: 缺 .env（请先 cp .env.example .env 并填密钥）'; exit 1; }
  DC='docker compose'; docker compose version >/dev/null 2>&1 || DC='docker-compose'
  \$DC up -d ${BUILD_FLAG} ${SVC_ARG}
  echo '>>> compose 状态：'
  \$DC ps"

# ── 3. 健康检查 ──────────────────────────────────────────────────────────────
say "健康检查：${HEALTH_URL}"
ok=0
for i in $(seq 1 10); do
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 8 "${HEALTH_URL}" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then ok=1; break; fi
  echo "    等待服务就绪…（第 $i 次，HTTP ${code:-超时}）"; sleep 3
done
[ "$ok" = "1" ] || die "健康检查未通过（${HEALTH_URL}）。查看远端日志：ssh ${REMOTE_USER}@${HOST} 'cd ${REPO_DIR}/${DEPLOY_SUBDIR} && docker compose logs -n 100 backend'"

say "✅ 部署完成：${BRANCH}@${COMMIT} 已上线 ${HOST}"
echo "    健康检查 200 OK · ${HEALTH_URL}"
echo "    查看日志：ssh ${REMOTE_USER}@${HOST} 'cd ${REPO_DIR}/${DEPLOY_SUBDIR} && docker compose logs -f backend'"
