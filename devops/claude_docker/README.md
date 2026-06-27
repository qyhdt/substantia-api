# devops/claude_docker — Claude 运行容器 & 订阅登录镜像

substantia-api 让 Claude Code CLI 跑在容器里。架构与里程碑见
[`../../doc/claude-docker-plan.md`](../../doc/claude-docker-plan.md)。

> 本目录从 `digital-platform--generator/claude_docker` 移植并改造：
> 远端仓库路径默认 `~/substantia-api`，seed 脚本路径 `devops/claude_docker/`。

## 文件

| 文件 | 作用 |
|---|---|
| `Dockerfile.claude` | base 镜像 `claude-runner`（node:20 + 全局 `@anthropic-ai/claude-code` + skills 依赖）。api_key slot 直接用。 |
| `Dockerfile.standalone` | 精简版独立镜像，本地手动调试用。 |
| `seed-claude-creds.sh` | 登录镜像 ENTRYPOINT：启动时把烘好的 OAuth 凭据 seed 到运行时 `$HOME/.claude`（仅当镜像更新时覆盖，避免用过期凭据盖掉活凭据）。 |
| `run.sh` | 本地一键起容器跑 `claude`（交互 / `-p` 非交互），自动 source `.env.dev`。 |
| `make-logged-in-image.sh` | 本机烘订阅预登录镜像并 push（本地版）。 |
| `relogin-remote.sh` | 远端一键重登 + 重烘订阅镜像（refresh_token 失效时用）。默认 `HOST=43.155.195.115`。 |
| `.env.dev.example` | `run.sh` 的环境模板（复制为 `.env.dev`，**不入库**）。 |

## 多订阅（per-slot）约定 — 对应方案 A

每个 subscription slot = **一个独占的预登录镜像 + 一个常驻容器**：

- 镜像 tag 建议：`qyhdt/private:claude-loggedin-<slot>`（如 `...-sub-a`、`...-sub-b`）。
  烘制时传 `IMAGE=qyhdt/private:claude-loggedin-sub-a ./relogin-remote.sh`。
- 容器命名 / 编排由后端 `docker_manager` 按 slot 负责（M3 实现）。
- 用户经 **rendezvous(HRW) 哈希**固定路由到某个 slot（M2 实现）。
- **绝不**在一个 HOME 里热插拔/轮换多个 sub 的凭据（rotating refresh_token 会雪崩 401）。

## 本地验证镜像（M1）

```bash
cd devops/claude_docker
# 1) build base 镜像（首次 run.sh 会自动 build）
cp .env.dev.example .env.dev   # 填一个临时 api_key 仅用于验证镜像本身（不入池）
./run.sh -p "print hello"      # 跑通 = 镜像 OK
```

私有仓库镜像务必保持 **private**（烘了订阅 OAuth，谁能 pull 谁能用你的号）。
