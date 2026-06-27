#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  relogin-remote.sh — 在【远端服务器】上重新烘 claude 订阅登录镜像
#
#  适用：订阅 OAuth 凭据过期（claude 报 401 Invalid authentication credentials）时，
#       重新登录并把新登录态烘进 qyhdt/private:claude-loggedin，推送 + 让平台容器重建。
#
#  你只需做一件事：脚本中途把你 drop 进 claude，交互式 /login 一次。其余全自动。
#
#  用法（在本地 Mac 跑，通过 ssh 驱动远端；需已配好免密 ssh work@HOST）：
#     ./relogin-remote.sh
#     HOST=1.2.3.4 ./relogin-remote.sh           # 换台机器
#     NO_PUSH=1 ./relogin-remote.sh              # 只本地 commit 不推送（验证用）
#
#  环境变量（都有默认值）：
#     HOST         远端 IP，默认 43.155.195.115
#     REMOTE_USER  远端用户，默认 work
#     IMAGE        目标镜像，默认 qyhdt/private:claude-loggedin
#     BASE_IMAGE   登录用的 base 镜像，默认 claude-runner
#     REPO_DIR     远端仓库路径，默认 ~/substantia-api（取 seed 脚本用）
#     NO_PUSH=1    跳过 docker push
#
#  ⚠️ 镜像里烘了你账号的 OAuth 凭据，IMAGE 必须是私有仓库。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HOST="${HOST:-43.155.195.115}"
REMOTE_USER="${REMOTE_USER:-work}"
IMAGE="${IMAGE:-qyhdt/private:claude-loggedin}"
BASE_IMAGE="${BASE_IMAGE:-claude-runner}"
TMP="claude-login-build"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=240)
SSH=(ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}")

say() { printf '\n\033[1;36m>>> %s\033[0m\n' "$*"; }

# tmp 容器：默认保留（中途网断也能恢复登录态），仅在 push 成功后由步骤 3.7 显式删。
# 异常退出时打印恢复提示，绝不自动 rm —— 否则一个 ssh 断就要重做 /login。
cleanup() {
  if [ "${TMP_DONE:-0}" = "1" ]; then return; fi
  say "保留远端临时容器 ${TMP}（登录态在里面，别动）"
  echo "  如要继续：./devops/claude_docker/relogin-remote.sh   # 脚本会幂等"
  echo "  如要放弃：ssh ${REMOTE_USER}@${HOST} 'docker rm -f ${TMP}'"
}
trap cleanup EXIT

# ── 1. 起临时登录容器（幂等：已存在就复用，里面的登录态不动）─────────────────
say "在远端 ${HOST} 准备临时容器 ${TMP}（base=${BASE_IMAGE}）"
"${SSH[@]}" "
  docker image inspect $BASE_IMAGE >/dev/null 2>&1 || { echo 'ERROR: 远端缺 base 镜像 $BASE_IMAGE'; exit 1; }
  if docker inspect $TMP >/dev/null 2>&1; then
    docker start $TMP >/dev/null 2>&1 || true
    if docker exec --user node $TMP test -f /home/node/.claude/.credentials.json 2>/dev/null; then
      echo '复用已存在的临时容器（登录态保留）'
    else
      echo '复用已存在的临时容器（尚未登录）'
    fi
  else
    docker run -d --name $TMP --add-host=host.docker.internal:host-gateway \
      -e HOME=/home/node --user node $BASE_IMAGE sleep infinity >/dev/null
    echo '临时容器已新建'
  fi
"

# ── 2. 交互式登录（唯一需要你操作的一步）────────────────────────────────────
cat <<'TIP'

──────────────────────────────────────────────────────────────────
  接下来进入 claude，请：
    1. 若没自动弹登录，输入  /login  回车
    2. 选 "Subscription"（订阅登录）
    3. 复制它给的 URL 到浏览器授权，把返回的 code 贴回来
    4. 看到登录成功后，输入  /exit  退出（或 Ctrl+C 两次）
  退出后脚本会自动验证、打包、推送、让平台容器重建。
──────────────────────────────────────────────────────────────────
TIP
read -rp "准备好了按回车进入 claude… " _
ssh -t "${SSH_OPTS[@]}" "${REMOTE_USER}@${HOST}" "docker exec -it $TMP claude" || true

# ── 3. 其余全自动（验证凭据 → opus → seed → commit → push → 重建 → 清理）─────
say "验证登录态并打包镜像…"
"${SSH[@]}" "IMAGE=$(printf %q "$IMAGE") TMP=$(printf %q "$TMP") NO_PUSH=$(printf %q "${NO_PUSH:-0}") REPO_DIR=$(printf %q "${REPO_DIR:-}") bash -s" <<'REMOTE'
set -euo pipefail
REPO_DIR="${REPO_DIR:-$HOME/substantia-api}"
SEED="$REPO_DIR/devops/claude_docker/seed-claude-creds.sh"

# 3.1 凭据必须存在，否则登录没成功，中止（不删容器，方便你重试登录）
if ! docker exec --user node "$TMP" test -f /home/node/.claude/.credentials.json; then
  echo "ERROR: 没找到 /home/node/.claude/.credentials.json —— 登录没成功。" >&2
  echo "  可重跑脚本，或先手动: docker exec -it $TMP claude" >&2
  exit 1
fi
echo ">>> 凭据 OK"

# 3.2 实跑一次确认订阅可用（不注入 ANTHROPIC_*，走 OAuth）
echo ">>> 实测订阅是否可用…"
docker exec --user node -e HOME=/home/node -w /home/node "$TMP" \
  claude --print --dangerously-skip-permissions --output-format=json -p "reply with exactly: OK" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(">>> is_error:",d.get("is_error"),"api_status:",d.get("api_error_status"),"result:",d.get("result")); sys.exit(1 if d.get("is_error") else 0)' \
  || { echo "ERROR: 登录态实测失败（仍 401？）。中止，未覆盖旧镜像。" >&2; exit 1; }

# 3.3 固定默认模型 opus（始终指向最新 Opus）
docker exec --user node "$TMP" node -e 'const fs=require("fs"),os=require("os");const dir=os.homedir()+"/.claude";fs.mkdirSync(dir,{recursive:true});const f=dir+"/settings.json";let s={};try{s=JSON.parse(fs.readFileSync(f,"utf8"))}catch{}s.model="opus";fs.writeFileSync(f,JSON.stringify(s,null,2));console.log(">>> settings.json model=opus")'

# 3.4 注入 seed 脚本 + commit
if [ ! -f "$SEED" ]; then echo "ERROR: 找不到 seed 脚本 $SEED" >&2; exit 1; fi
docker cp "$SEED" "$TMP:/usr/local/bin/seed-claude-creds.sh"
docker exec --user root "$TMP" chmod +x /usr/local/bin/seed-claude-creds.sh
docker commit \
  --change 'ENTRYPOINT ["/usr/local/bin/seed-claude-creds.sh"]' \
  --change 'CMD ["sleep", "infinity"]' \
  --change 'USER node' \
  "$TMP" "$IMAGE" >/dev/null
echo ">>> 已 commit 本地镜像: $IMAGE"

# 3.5 推送
if [ "${NO_PUSH:-0}" = "1" ]; then
  echo ">>> NO_PUSH=1，跳过推送"
else
  docker push "$IMAGE"
  echo ">>> 已推送: $IMAGE"
fi

# 3.6 删掉所有 per-user claude 容器 → 下次请求按新镜像重建（订阅模式下用新 OAuth）
n=$(docker ps -a --filter label=vibe.platform=claude-runner --format '{{.Names}}' | wc -l | tr -d ' ')
docker ps -a --filter label=vibe.platform=claude-runner --format '{{.Names}}' | xargs -r docker rm -f >/dev/null
echo ">>> 已删除 $n 个存量 claude 容器（下次请求自动重建）"

# 3.7 清理临时容器
docker rm -f "$TMP" >/dev/null 2>&1 || true
echo ">>> 临时容器已清理"
REMOTE

TMP_DONE=1  # 至此 push 已成功 & tmp 容器已删，trap 不再提示"保留"
say "✅ 完成。确认平台认证模式为 subscription（admin 配置页），即可回平台重新生成。"
echo "    新容器会用刚烘的镜像 + 新 OAuth（真 Claude Opus 4.8）。"
