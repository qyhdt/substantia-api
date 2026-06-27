#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  make-logged-in-image.sh — 做一个"已登录 claude"的镜像并推到 docker.io（私有仓）
#
#  流程：
#    1. 准备一个 base 镜像（带 claude-code）
#    2. 起一个临时容器，把你 drop 进去交互式跑 claude /login（浏览器授权）
#    3. 登录完检查 ~/.claude/.credentials.json 存在
#    4. 把 seed 脚本拷进容器，commit 成新镜像（带 ENTRYPOINT 做凭据 seed）
#    5. docker push 到你的私有仓
#
#  用法：
#    DOCKER_REPO=youruser/claude-loggedin ./make-logged-in-image.sh
#    DOCKER_REPO=youruser/claude-loggedin TAG=v1 ./make-logged-in-image.sh
#
#  环境变量：
#    DOCKER_REPO  必填，docker.io 上的私有仓库名，如 youruser/claude-loggedin
#    TAG          镜像 tag，默认 latest
#    BASE_IMAGE   base 镜像，默认本地 build claude_docker/Dockerfile.claude
#    NO_PUSH=1    只 commit 本地，不推送（先验证）
#
#  ⚠️ 安全：镜像里烘了你的 claude OAuth 凭据，仓库必须私有。任何能 pull 的人都能用你的号。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAG="${TAG:-latest}"
BASE_IMAGE="${BASE_IMAGE:-claude-runner}"
TMP_NAME="claude-login-build-$$"

if [ -z "${DOCKER_REPO:-}" ]; then
    echo "ERROR: 必须设置 DOCKER_REPO，例如：" >&2
    echo "  DOCKER_REPO=youruser/claude-loggedin ./make-logged-in-image.sh" >&2
    exit 1
fi
TARGET="docker.io/${DOCKER_REPO}:${TAG}"

cleanup() {
    docker rm -f "$TMP_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── 1. base 镜像 ─────────────────────────────────────────────────────────────
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    echo ">>> base 镜像 $BASE_IMAGE 不存在，用 Dockerfile.claude 构建…"
    docker build -f "$SCRIPT_DIR/Dockerfile.claude" -t "$BASE_IMAGE" "$SCRIPT_DIR"
fi

# ── 2. 起临时容器（HOME=/home/node，让登录凭据落在镜像内置路径）────────────────
echo ">>> 启动临时容器 $TMP_NAME …"
docker run -d --name "$TMP_NAME" \
    --add-host=host.docker.internal:host-gateway \
    -e HOME=/home/node \
    --user node \
    "$BASE_IMAGE" \
    sleep infinity >/dev/null

# ── 3. 交互式登录 ─────────────────────────────────────────────────────────────
cat <<'EOF'

──────────────────────────────────────────────────────────────────
  接下来会把你 drop 进容器里跑 claude。在里面：
    1. 如果没自动弹登录，输入  /login  回车
    2. 选 "Subscription"（订阅登录）
    3. 复制它给的 URL 到浏览器，授权，把 code 贴回来
    4. 看到登录成功后，输入  /exit  （或 Ctrl+C 两次）退出
  退出后本脚本会自动检查凭据并打包镜像。
──────────────────────────────────────────────────────────────────

EOF
read -rp "准备好了按回车进入容器… " _

docker exec -it "$TMP_NAME" claude || true

# ── 4. 验证凭据 ──────────────────────────────────────────────────────────────
echo ">>> 检查登录凭据…"
if ! docker exec "$TMP_NAME" test -f /home/node/.claude/.credentials.json; then
    echo "ERROR: 没找到 /home/node/.claude/.credentials.json —— 登录没成功？" >&2
    echo "  可以重新跑本脚本，或先  docker exec -it $TMP_NAME claude  手动登录确认" >&2
    exit 1
fi
echo ">>> 凭据 OK"

# ── 4.5 固定默认模型为 Opus(4.8) ─────────────────────────────────────────────
# subscription 登录后默认模型未必是最新 Opus；把镜像内置 ~/.claude/settings.json
# 的 model 固定成 opus（alias，始终指向最新 Opus，目前即 4.8），与 host /model 选 4.8 一致。
# 用 node 做 JSON 合并，保留 settings.json 里其他可能已有的键。
echo ">>> 固定默认模型为 Opus(4.8)…"
docker exec --user node "$TMP_NAME" node -e '
  const fs=require("fs"),os=require("os");
  const dir=os.homedir()+"/.claude"; fs.mkdirSync(dir,{recursive:true});
  const f=dir+"/settings.json"; let s={};
  try{s=JSON.parse(fs.readFileSync(f,"utf8"))}catch{}
  s.model="opus";
  fs.writeFileSync(f,JSON.stringify(s,null,2));
  console.log("settings.json model=opus -> "+f);
'

# ── 5. 注入 seed 脚本 + commit ───────────────────────────────────────────────
echo ">>> 注入 seed 脚本并 commit 成 $TARGET …"
docker cp "$SCRIPT_DIR/seed-claude-creds.sh" "$TMP_NAME:/usr/local/bin/seed-claude-creds.sh"
docker exec --user root "$TMP_NAME" chmod +x /usr/local/bin/seed-claude-creds.sh

docker commit \
    --change 'ENTRYPOINT ["/usr/local/bin/seed-claude-creds.sh"]' \
    --change 'CMD ["sleep", "infinity"]' \
    --change 'USER node' \
    "$TMP_NAME" "$TARGET"

echo ">>> 已生成本地镜像：$TARGET"

# ── 6. 推送 ──────────────────────────────────────────────────────────────────
if [ "${NO_PUSH:-0}" = "1" ]; then
    echo ">>> NO_PUSH=1，跳过推送。要推手动跑：docker push $TARGET"
    exit 0
fi

echo ">>> 推送到 docker.io（私有仓，需要先 docker login）…"
if ! docker push "$TARGET"; then
    echo "推送失败，可能没登录。先跑：docker login" >&2
    echo "然后手动：docker push $TARGET" >&2
    exit 1
fi

echo ""
echo "✅ 完成：$TARGET"
echo "   之后把平台的 VIBE_CLAUDE_IMAGE 指到这个镜像即可（admin 选镜像那步我下一步做）。"
