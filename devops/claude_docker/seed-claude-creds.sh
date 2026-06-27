#!/bin/sh
# 把镜像里烘好的 claude 登录态 seed 到运行时 HOME。
#
# 为什么需要：平台起容器时会把 HOME 设成 /workspace 并挂载用户目录，
# 会盖掉镜像里 /home/node/.claude 下的凭据。这个脚本在容器启动时（ENTRYPOINT）
# 把烘进镜像的凭据复制一份到当前 $HOME/.claude，让 claude 直接是登录态。
#
# 凭据（.credentials.json）是平台级的：全平台共用一个订阅账号，源头 = 镜像。
# 但 Anthropic OAuth 的 refresh token 是一次性轮换的：claude 续期一次就把旧的作废、
# 把新凭据写回 workspace。若每次启动都无脑用镜像覆盖，重启/重建容器就会用「已被消耗
# 的过期镜像凭据」盖掉 workspace 里的活凭据 → 401。
# 所以策略：**仅当镜像凭据比 workspace 里的更新（expiresAt 更大，例如刚重新登录重烘）
# 或 workspace 没有/坏掉时才覆盖**；否则保留 workspace 里续期写回的活凭据。
# 账号信息 / settings 则只在缺失时 seed，不覆盖用户运行期状态 / 主题偏好。
# 注意：不要 set -e —— seed 是 best-effort，失败也要继续 exec claude，
# 否则极端情况下（如 workspace 权限异常）整个容器入口会崩。
SEED_DIR=/home/node/.claude

if [ -n "$HOME" ] && [ "$HOME" != "/home/node" ] && [ -d "$SEED_DIR" ]; then
    mkdir -p "$HOME/.claude" 2>/dev/null || true

    # 凭据：仅当镜像里的更新（或 workspace 缺失/损坏）时才覆盖，避免用过期凭据盖掉活的
    SEED_CRED="$SEED_DIR/.credentials.json"
    DEST_CRED="$HOME/.claude/.credentials.json"
    if [ -f "$SEED_CRED" ]; then
        SHOULD_COPY=1
        if [ -f "$DEST_CRED" ]; then
            # 比较 expiresAt：镜像 > workspace 才覆盖；解析失败（workspace 坏）则兜底覆盖
            SHOULD_COPY=$(node -e '
              try {
                const fs=require("fs");
                const img=(JSON.parse(fs.readFileSync(process.argv[1],"utf8")).claudeAiOauth)||{};
                const ws =(JSON.parse(fs.readFileSync(process.argv[2],"utf8")).claudeAiOauth)||{};
                process.stdout.write(Number(img.expiresAt||0) > Number(ws.expiresAt||0) ? "1" : "0");
              } catch(e) { process.stdout.write("1"); }
            ' "$SEED_CRED" "$DEST_CRED" 2>/dev/null || echo 1)
        fi
        if [ "$SHOULD_COPY" = "1" ]; then
            cp -f "$SEED_CRED" "$DEST_CRED" 2>/dev/null || true
            chmod 600 "$DEST_CRED" 2>/dev/null || true
        fi
    fi

    # 账号级配置（~/.claude.json，登录后的账号信息）：仅缺失时 seed（同一订阅账号不变；
    # 不覆盖 claude 运行期写回的状态）
    if [ -f /home/node/.claude.json ] && [ ! -f "$HOME/.claude.json" ]; then
        cp /home/node/.claude.json "$HOME/.claude.json" 2>/dev/null || true
    fi

    # 默认模型设置（settings.json，里面固定了 model=opus → 最新 Opus/4.8）也 seed 一份
    if [ -f "$SEED_DIR/settings.json" ] && [ ! -f "$HOME/.claude/settings.json" ]; then
        cp "$SEED_DIR/settings.json" "$HOME/.claude/settings.json" 2>/dev/null || true
    fi

    # skills：仅 withskills 镜像里会有 $SEED_DIR/skills；仅 workspace 缺时 seed 一份（不覆盖用户改过的）。
    # 注意：若平台同时配了 VIBE_SKILLS_HOST_DIR，docker 会把 host 目录只读挂到 /workspace/.claude/skills，
    # 覆盖这里 seed 的内容——这是设计的（host 挂载 > 镜像内置）。
    if [ -d "$SEED_DIR/skills" ] && [ ! -d "$HOME/.claude/skills" ]; then
        cp -r "$SEED_DIR/skills" "$HOME/.claude/skills" 2>/dev/null || true
    fi
fi

# 没传命令时兜底（理论上 commit 时已设 CMD）
if [ "$#" -eq 0 ]; then
    set -- sleep infinity
fi

exec "$@"
