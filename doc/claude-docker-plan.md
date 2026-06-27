# Claude 容器运行方案 — Plan（活文档）

> 本文是 substantia-api 跑 Claude Code 容器的**设计 + 实施计划**。
> 约定：**以后每次改动都要回来更新这里的「实施步骤」勾选状态和「变更记录」。**
> 参考来源：`digital-platform--generator/claude_docker` 与 `backend/src/services/vibe/docker_manager.py`。

最后更新：2026-06-27 · 状态：**Plan 待确认（尚未开始实现）**

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
- **api_key slot**：用 base 镜像 `claude-runner` + 注入该 slot 的 `ANTHROPIC_*`。
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
→ **默认走方案 A**；若后续资源吃紧再降级到 B。**（此项待你拍板，见文末）**

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

### M0 · 设计确认（当前）
- [x] 读透参考 `claude_docker` + `docker_manager`
- [x] 产出本 plan
- [ ] **与用户确认拓扑方案（A / B）与 slot 初始清单** ← 阻塞后续

### M1 · 镜像与本地跑通（单 slot）
- [ ] 把 `claude_docker/` 移植进 `substantia-api/devops/claude_docker/`（Dockerfile.claude / run.sh / seed-claude-creds.sh 等）
- [ ] build `claude-runner` base 镜像，`run.sh` 用一个 api_key slot（如 GLM/DeepSeek）跑通 `claude -p`
- [ ] 烘一个 subscription 预登录镜像，确认订阅档不报 401

### M2 · slot 抽象 + 路由
- [ ] `backend/src/services/claude/slots.py`：slot 配置模型 + 加载（DB/配置）
- [ ] `router.py`：HRW 哈希路由（含 weight、unhealthy 剔除），单测覆盖「增删 slot 只搬 ~1/N」
- [ ] DB migration：`provider_slots`（若走 DB）

### M3 · 容器编排（slot 为单位）
- [ ] `docker_manager.py`（移植改造）：`ensure_slot_container(slot)` 幂等创建/启动
- [ ] subscription slot：per-slot 共享 `.claude` 卷 + 多用户嵌套挂载隔离
- [ ] api_key slot：注入该 slot 的 `ANTHROPIC_*`
- [ ] `exec_claude(user_id, prompt)`：路由 → ensure slot 容器 → exec

### M4 · 保活 + 健康 + 故障转移
- [ ] per-slot probe_loop：保活订阅凭据（harvest/seed 改 per-slot）
- [ ] 健康探针：401/限额 → 标 unhealthy + cooldown，HRW 自动绕过
- [ ] 恢复探活：cooldown 后重探，healthy 则自动回流

### M5 · API + Admin
- [ ] 对外接口：`POST /api/claude/chat`（或 SSE 流式，复用 `frame/sse.py`）
- [ ] admin：slot 列表 CRUD、健康看板、relogin 入口
- [ ] devops：compose / 部署脚本、多 sub 凭据下发流程

### M6 · 加固
- [ ] 资源闸（并发/内存）、`--network` 收紧、容量上限
- [ ] 单 user 并发上限、注册防刷（如需要）

---

## 7. 安全注意
- 预登录镜像烘了订阅 OAuth → 镜像仓库**必须私有**。
- slot 的 api_key / token 存 `.env` 或加密配置，**绝不入库**（`.gitignore` 已含 `.env`）。
- 容器以 `node`(uid 1000) 跑，禁止 root。

---

## 8. 待确认问题（给用户）
1. **拓扑选 A（每 sub 一个容器，推荐）还是 B（单容器多 HOME）？**
2. 初始 slot 清单：几个 subscription？是否要 GLM / ChatGPT / DeepSeek 的 api_key slot？各自端点/模型？
3. 部署目标机：仍是 `43.155.195.115`（`ide.substantia.ai`）那台？docker 仓库用哪个私有 repo？

---

## 9. 变更记录（changelog）
- **2026-06-27** · 初版 plan：确立 slot 池 + HRW 哈希分流 + per-sub 隔离凭据架构；明确「不可热插拔轮换订阅凭据」；列出 M0–M6 步骤。等待拓扑/​slot 清单确认。
