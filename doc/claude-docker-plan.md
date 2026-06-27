# Claude 容器运行方案 — Plan（活文档）

> 本文是 substantia-api 跑 Claude Code 容器的**设计 + 实施计划**。
> 约定：**以后每次改动都要回来更新这里的「实施步骤」勾选状态和「变更记录」。**
> 参考来源：`digital-platform--generator/claude_docker` 与 `backend/src/services/vibe/docker_manager.py`。

最后更新：2026-06-27 · 状态：**M0–M5 代码完成 + 远端 sub-a slot 端到端跑通（真 Opus）· 待登录更多 sub + 部署 backend**

**已定决策（2026-06-27）**：① 拓扑 = **方案 A**（每 sub 一个容器）；② **保留 api_key slot 接口能力，初期不启用**（池里先只放 subscription slot，GLM/ChatGPT/DeepSeek 以后再加）；③ 部署机 `43.155.195.115`，镜像推**私有仓库**（默认 `qyhdt/private`，可改）。

---

## 1. 目标

在 substantia-api 后端里，让 Claude Code CLI 跑在隔离容器中对外提供能力，并满足：

1. **多订阅扩展**：部署多个 subscription 账号，分摊额度，防止单个 sub token 被用光。
2. **用户分流**：用户按 **hash 固定分配**到某个 sub（sticky），把负载均匀打散。
3. **混合 provider**：同一套池子里既能放 subscription（官方订阅），也能放 api_key（GLM / ChatGPT / DeepSeek 等其它模型）。
4. **故障转移**：某个 sub 401 / 限额 / 过期时，自动把它的用户切到其它健康 slot。

---

## 2. 关键约束（先理解，否则方案会错）

**Anthropic 订阅用 rotating refresh_token（一次性轮换）。** 一旦某进程用 refresh_token 续期，旧 token 立即作废、写回一份新的。推论：

- ❌ **不能**让多个 claude 进程共用同一份 `.credentials.json` 又各自续期 —— 谁先续期谁作废别人 → 雪崩 401。
  （参考项目 README 已记录这个失败教训："谁续期作废谁"。）
- ❌ **不能**在「一个 HOME」里热插拔/动态轮换多个 sub 的凭据 —— 同上，会互相打架。
- ✅ **正确做法**：每个 sub 独占一份 `.claude` 凭据目录 + 独立保活探针，**各管各的轮换**，绝不交叉。
- ✅ 同一个 sub 内部、多用户**只读复用**这一份凭据是 OK 的（参考项目就是这么做的）：续期由**单一来源**串行写回，多用户工作目录各自隔离。

> 结论：扩展单位是 **sub（凭据身份）**，不是 user，也不是 container。
> 参考项目是 per-user 容器但**全平台只共享 1 个 sub** → 正好撞上「单 token 用光」的瓶颈，这正是我们要改的点。

---

## 3. 架构决策

### 3.1 Slot 抽象（核心）

把每个「凭据身份」抽象成一个 **slot**：

| 字段 | 说明 |
|---|---|
| `id` | slot 唯一标识（如 `sub-a` / `glm-1`），hash 路由用 |
| `type` | `subscription` \| `api_key` |
| `enabled` | 是否参与路由 |
| `weight` | 权重（额度大的 sub 多分用户；默认 1） |
| `creds_dir` | （subscription）独占的 `.claude` 卷 host 路径 |
| `env` | （api_key）`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` 等 |
| `health` | 运行时健康态（healthy / unhealthy / cooldown_until） |

- **subscription slot**：用预登录镜像（烘了该账号 OAuth）+ 独占 `.claude` 卷 + 专属保活探针。
- **api_key slot**（⚠️ 初期**只保留接口能力、不启用**，池里先只放 subscription）：用 base 镜像 `claude-runner` + 注入该 slot 的 `ANTHROPIC_*`。
  - **GLM**：走 z.ai / bigmodel 的 **Anthropic 兼容端点**（`ANTHROPIC_BASE_URL=...`，`ANTHROPIC_MODEL=glm-4.x`）。
  - **ChatGPT / OpenAI**：OpenAI 协议与 Anthropic 不同，需经 **LiteLLM 中转**（Anthropic↔OpenAI），slot 的 base_url 指向 LiteLLM。
  - **DeepSeek 等**：本就有 Anthropic 兼容端点，直接配。

### 3.2 路由：Rendezvous（HRW）哈希，sticky

```
slot = argmax over enabled slots of  weight_i * hash(user_id + ":" + slot_i.id)
```

- **sticky**：同一 `user_id` 永远落到同一 slot → 会话缓存连续、单 sub 负载是稳定的用户子集。
- **增删 slot 只搬 ~1/N 用户**（HRW 的天然性质），无需重排全部用户、无需维护 hash ring 状态。
- **支持权重**：额度大的 sub 给更高 weight，多吃用户。
- **故障转移**：某 slot 标 unhealthy 后，把它从候选集剔除再 argmax → 它的用户**平摊**到其余健康 slot；恢复后自动回流（因为 HRW 是确定性的）。

### 3.3 容器拓扑（回答「一个容器 vs 多个容器」）

| | 方案 A · 每 sub 一个容器（**推荐生产**） | 方案 B · 单容器多 HOME（轻量） |
|---|---|---|
| 结构 | 每个 slot 一个常驻容器，独占 `.claude` 卷 | 一个容器内放多个隔离 HOME：`/creds/<slot>`；每次 exec 设 `HOME=` |
| 隔离 | 强：崩溃/OOM/restart 互不影响 | 弱：一个容器挂全挂 |
| 资源 | 容器数 = slot 数 | 省，1 个容器 |
| 保活 | 每容器一个探针，简单 | 一个探针管 N 个 creds 目录 |
| 适用 | 生产、slot 不多（个位到几十） | 资源紧 / slot 很多 / 开发期 |

两个方案**都不是热插拔轮换**，而是「每 sub 独占隔离凭据 + 按 hash 静态分配」。
→ **已选定方案 A（每 sub 一个容器）**。若后续资源吃紧再考虑降级到 B。

### 3.4 单 sub 容器内的多用户隔离（复用参考项目做法）

一个 subscription 容器服务它名下所有用户：

- 凭据/配置：挂该 slot 的共享 `.claude`（只读复用，单一来源续期写回）。
- 用户工作区 & 对话记录：`/workspace/<user_id>` 各自 bind；`.claude/projects`、`.claude/todos` 按用户**嵌套挂载**覆盖（A 看不到 B 的对话）。
- 每条请求 = 一次 `docker exec`（`claude -p ...`），HOME=/workspace，user=node。

---

## 4. 复用参考项目的哪些部分

| 直接复用/改造 | 说明 |
|---|---|
| `Dockerfile.claude` | base 镜像（node:20 + claude-code + skills 依赖）。api_key slot 直接用。 |
| 预登录镜像流程（`relogin-remote.sh` / `seed-claude-creds.sh`） | 每个 sub 各烘一个 `claude-loggedin-<slot>` 镜像或各自 seed。 |
| `docker_manager.ensure_container` 编排骨架 | 幂等创建/启动、labels、mem/cpu、OOM 计分、就绪等待。**改造点**：容器单位从 user → slot。 |
| 共享 `.claude` + `harvest/seed/probe` 保活 | 改造成 **per-slot**：每个 sub 一份 `.shared-claude-creds-<slot>` + 各自探针。 |
| `claude_fallback` / `advance_fallback` 思路 | 改造成 **per-slot 健康态 + HRW 剔除**，而非全局单链。 |
| `_build_env` | 按 slot.type 注入（subscription 不注入端点；api_key 注入该 slot 的 env）。 |

---

## 5. 配置 / 数据模型

- **slots 配置**：DB 表 `provider_slots` 或 admin 可改的 JSON 配置（类似参考项目的 `claude_runtime` 设置项，但变成**列表**）。
- 字段见 §3.1。admin 增删 slot → 触发对应容器 ensure / remove。
- 路由结果可缓存（`user_id → slot_id`），但必须是 HRW 的纯函数派生，方便 slot 变动时重算。

---

## 6. 实施步骤（每次改动回来勾选）

### M0 · 设计确认（✅ 完成）
- [x] 读透参考 `claude_docker` + `docker_manager`
- [x] 产出本 plan
- [x] 与用户确认：拓扑 = **A**；初期 slot 池 = **仅 subscription**（api_key 保留接口不启用）；部署机 `43.155.195.115` + 私有仓库

### M1 · 镜像与本地跑通（单 slot）（✅ 完成）
- [x] 把 `claude_docker/` 移植进 `substantia-api/devops/claude_docker/`（Dockerfile.claude / run.sh / seed-claude-creds.sh / make-logged-in / relogin-remote），改项目路径为 `~/substantia-api`、seed 路径加 `devops/`，示例 token 打码，加 README（含 per-slot 镜像 tag 约定）
- [x] base 镜像 `claude-runner` 远端已存在（复用，api_key slot 用）
- [x] **sub-a 订阅预登录镜像 `qyhdt/private:claude-loggedin-sub-a` 已烘**（用户交互 /login，NO_PUSH 留远端本地）
- [x] **远端实测**：按 `ensure_slot_container` 同参起 `claude-slot-sub-a` → 凭据自动 seed、真跑 `claude -p` 返回 pong（订阅 Opus，不 401）、`restart=unless-stopped` 永远存活、活凭据写回 host bind 目录
- [x] 修 relogin 脚本：删容器只按 `substantia.claude=slot-container` label（之前误删了同机 digital-platform 的 `claude-usr-*`/`claude-testuser`，那些会按需自动重建，无数据损失）
- [x] 远端写好 `/var/lib/substantia/claude/slots.json`（sub-a），backend 部署即自动加载

### M2 · slot 抽象 + 路由（✅ 完成）
- [x] `backend/src/services/claude/slots.py`：`Slot` 模型（subscription/api_key 两型 + 运行时健康态 + `is_routable`/`mark_unhealthy`/`mark_healthy`）
- [x] `router.py`：加权 **HRW(rendezvous)** 路由（weight 比例分布 + unhealthy 剔除 + sticky）
- [x] `registry.py`：进程级单例 + `CLAUDE_SLOTS_JSON` 加载 + `configure()` 热更新（reconfigure 保留健康态）
- [x] `tests/test_claude_router.py`：7 用例全绿 —— sticky / 等权均匀 / 加权比例 / 删 slot 只搬 ~1/N（仅被删 slot 用户动）/ 增 slot ~1/(N+1) / 健康剔除与回流 / 空池抛错
- [ ] DB 持久化 `provider_slots`：**暂用 env/JSON + `configure()`**，留到 M5 admin 接管时再落 DB

### M3 · 容器编排（slot 为单位）（✅ 代码完成，待 Docker 环境实测）
- [x] `services/claude/docker_manager.py`：`ensure_slot_container(slot)` 幂等创建/启动（labels、mem/cpu、OOM 计分、restart=unless-stopped、就绪等待）
- [x] subscription slot：per-slot **独占** `.claude` 凭据目录挂到 `/workspace/.claude`（容器内多用户共用一份凭据 → 单一来源续期，避开 rotating token 雪崩）
- [x] api_key slot：注入该 slot 的 `ANTHROPIC_*`（订阅档严格剔除 ANTHROPIC_*，只放 `CLAUDE_CODE_*`）
- [x] `exec_claude(user_id, prompt)`：路由 → ensure 容器 → 在 `/workspace/users/<uid>` 跑 `claude -p`（argv 传参，无注入）
- [x] `ensure_all_enabled()` / `list_slot_containers()` / `is_docker_reachable()`：一把拉起所有 enabled slot 容器 + 状态查询
- [x] 配置：`CLAUDE_BASE_IMAGE` / `CLAUDE_WORKSPACE_ROOT` / 容器资源 / `CLAUDE_SLOTS_JSON`；requirements 加 `docker>=7.1.0`
- [x] `tests/test_claude_docker_manager.py`：7 用例（安全 id / 命名 / 镜像解析 / 订阅剔除 ANTHROPIC_* / api_key 注入 / 卷映射 / 路径）
- [ ] **实测**：需 Docker 守护进程 + 已 build/pull 的镜像才能真起容器（留待镜像就绪）

> **设计取舍（多用户/单容器）**：方案 A 下一个 slot 容器服务多个用户。凭据**按 slot 共享**（正确，
> 单一来源续期）；用户隔离靠**独立工作目录** `/workspace/users/<uid>` + claude 按 cwd 路径分目录存转录。
> 同一 slot 内的用户转录在容器内**互相可读**（非访问控制级隔离）——记为 M6 加固项（如需强隔离，
> 可改 per-user 子容器或 `CLAUDE_CONFIG_DIR` 方案）。跨 slot 天然隔离。

### M4 · 保活 + 健康 + 故障转移（✅ 代码完成）
- [x] `services/claude/health.py`：`probe_slot`（真跑极简 claude 验活，顺带触发订阅 OAuth 续期=保活）+ `probe_and_update`（回写健康态）+ `probe_loop`（后台周期任务）
- [x] 即时故障转移：`exec_claude` 撞 401/鉴权失败 → 标 slot unhealthy + cooldown → 下一轮 `route()` 自动绕过（在 docker_manager 内，非鉴权失败不重试）
- [x] 恢复：cooldown 过后 `is_routable` 乐观放行，probe_loop 探到健康即 `mark_healthy` 回流
- [x] 凭据续期：slot 的 `.claude` 目录直接挂 `/workspace/.claude`，claude 就地写回 host（rename 在挂载目录内安全），**无需跨用户 harvest**
- [x] `tests/test_claude_failover.py`：故障转移 / 鉴权检测 / 非鉴权不重试 / 全挂抛错（19 单测全绿）

### M5 · API + Admin（✅ 代码完成）
- [x] 对外：`POST /api/claude/chat`（鉴权，路由→exec→返回；503=无可用 slot，502=exec 失败）
- [x] admin：`GET/PUT/DELETE /api/admin/claude/slots`（CRUD + 健康看板，文件持久化 `slots.json`）
- [x] admin 容器：`GET /containers`、`POST /containers/ensure`、`POST /slots/{id}/probe`
- [x] 启动钩子：docker 可达且有 slot 时 `ensure_all_enabled` + 起 `probe_loop`；停机 cancel
- [x] slot 持久化 `store.py`（文件存储，DB 可后续替换；接口不变）
- [ ] SSE 流式 `/chat`（复用 `frame/sse.py`）—— 后续按需加
- [ ] devops：compose / 部署脚本、多 sub 凭据下发（待镜像就绪时一起做）
- [ ] admin relogin 入口（现可手动跑 `relogin-remote.sh`，UI 化后续）

### M6 · 加固
- [ ] 资源闸（并发/内存）、`--network` 收紧、容量上限
- [ ] 单 user 并发上限、注册防刷（如需要）

---

## 7. 安全注意
- 预登录镜像烘了订阅 OAuth → 镜像仓库**必须私有**。
- slot 的 api_key / token 存 `.env` 或加密配置，**绝不入库**（`.gitignore` 已含 `.env`）。
- 容器以 `node`(uid 1000) 跑，禁止 root。

---

## 8. 待确认问题（✅ 已答 2026-06-27）
1. ✅ 拓扑 = **方案 A**（每 sub 一个容器）。
2. ✅ **保留 api_key 接口能力，初期不启用**；池里先只放 subscription slot。具体几个 sub = 配置驱动，部署时填。
3. ✅ 部署机 `43.155.195.115`；镜像推**私有仓库**（默认 `qyhdt/private`，需要时改）。

仍待补（不阻塞 M1）：初期 subscription slot 的**数量**与各自登录账号（部署时按需增减，配置驱动）。

---

## 9. 变更记录（changelog）
- **2026-06-27** · 初版 plan：确立 slot 池 + HRW 哈希分流 + per-sub 隔离凭据架构；明确「不可热插拔轮换订阅凭据」；列出 M0–M6 步骤。等待拓扑/​slot 清单确认。
- **2026-06-27** · 用户拍板：拓扑 = 方案 A；初期 slot 池仅 subscription（api_key 保留接口不启用）；部署机 `43.155.195.115` + 私有仓库。M0 完成，进入 M1。
- **2026-06-27** · M1：移植 `claude_docker` → `devops/claude_docker/`（改路径/打码/加 README + per-slot tag 约定）。镜像 build/push 与远端部署待用户确认后执行。
- **2026-06-27** · M2 完成：`services/claude/{slots,router,registry}.py` + 7 个单测全绿。加权 HRW 路由，sticky、增删 slot 只搬 ~1/N、健康剔除/回流均验证通过。slot 持久化暂用 env/JSON，DB 落到 M5。
- **2026-06-27** · M3 代码完成：`services/claude/docker_manager.py`（slot 为单位幂等编排 + `exec_claude` + `ensure_all_enabled`）+ 7 个纯逻辑单测（共 14 全绿）。加 `docker` 依赖与 `CLAUDE_*` 配置。记录多用户/单容器的转录隔离取舍（M6 加固）。容器实测待 Docker 镜像就绪。
- **2026-06-27** · M4+M5 代码完成：`health.py`（探针/保活/`probe_loop`）、`exec_claude` 即时故障转移、`store.py`（slot 文件持久化）、`controller/claude.py`（用户 `/claude/chat` + admin slot CRUD/健康/容器）、`main.py` 启动钩子（ensure + probe_loop）。+5 单测（共 19 全绿）。剩余：远端 build 镜像 + 配 slot + 实测；SSE 流式与 admin relogin UI 后续按需。
  - 旁注：用户在 settings 增了 `AK_*`（下游 APIKey 分发/计费/网关）配置 —— 属另一条线，本 plan 暂不覆盖。
- **2026-06-27** · 远端落地 sub-a：用户交互登录烘出 `qyhdt/private:claude-loggedin-sub-a`；远端 clone 仓库、建 `/var/lib/substantia/claude`（owner uid1000）、写 `slots.json`；按 manager 同参起 `claude-slot-sub-a` 实测端到端通过（真 Opus、永远存活、凭据 host 持久+轮换写回）。修复 relogin 误删跨项目容器的 bug。**剩余：登录更多 sub（sub-b…）+ 部署 substantia backend 让 `/api/claude/chat` 真正对外。**
