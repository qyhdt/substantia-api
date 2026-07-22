# APIKey 分发与网关 — Plan（活文档）

> 本文是 substantia-api 的**「类 oneapi」密钥分发 + 网关 + 用户/管理员门户**的设计与实施计划。
> 约定：**以后每次改动都要回来更新「实施里程碑」勾选状态和「变更记录」。**
> 姊妹文档：容器/订阅运行层见 [`claude-docker-plan.md`](./claude-docker-plan.md)（由容器团队负责，本计划只**消费与管理**，不实现容器本身）。

最后更新：2026-06-27 · 状态：**✅ 已上线远端并端到端联调通过（真 Opus + 真实计费扣款）**

远端 `8.216.44.14` 全栈跑起：`substantia-api-{db,backend,web}` 三容器 Up（compose 在 `devops/deploy/`）。
真实链路验证：注册→sk-key→`/v1/messages`→路由 sub-a→`docker exec claude-slot-sub-a`→真 Opus 4.8→
解析 token→**按实际命中模型 `claude-opus-4-8` 计价**→扣余额（一次 pong 扣 $0.317）→记 usage 日志。
剩最后一公里：edge-nginx 把域名反代到 `substantia-api-web`/`-backend`（归运维/容器侧配）。

**实现要点（2026-06-27）**：容器团队已落地 `services/claude/*`（slot 池 + HRW 路由 + 容器编排 +
`exec_claude` + 健康探针）与 `controller/claude.py`（`/api/claude/chat` + `/api/admin/claude/*` slot/容器管理）。
**本系统不再自建 `ak_credentials`/`ak_containers`，slot 即上游凭据，直接消费容器团队的引擎**：
- 网关 `controller/gateway.py` → `services/apikey/runner.py`（复用其路由+容器生命周期，但跑
  `claude -p --output-format json` 以拿 token 用量来计费），「sub 用光接 apikey」= slot 池级故障转移。
- admin 的「上游凭据/容器」页直接对接容器团队的 `/api/admin/claude/*`。

**已定决策（2026-06-27）**：① 投递方式 = **`docker exec`**（HTTPS 进来 → 后端直接 exec 容器内 `claude -p` → 返回）；② **sub→apikey 降级在网关/后端层做**（exec 重试时注入 apikey 的 `ANTHROPIC_*` env）；③ 下游协议 = **只做 Anthropic 兼容**，OpenAI 兼容延后(P2/暂不做)；④ **计费按 token，逐模型定价**（不同模型不同输入/输出单价）；⑤ **用户自助注册**；⑥ **注册自动送 $20 余额试用**（无需审核），申请/审核流程改为「**申请加额度/充值**」由 admin 审核；⑦ **路由按 user_id**（HRW sticky）。

---

## 0. 边界（先划清，否则会和容器团队撞车）

| 层 | 谁负责 | 内容 |
|---|---|---|
| **容器运行层** | 容器团队（另一个进程在写） | 容器内 claude code、订阅(sub)凭据的烘制/保活/轮换、`docker exec claude -p`、slot 抽象。**本计划假设它已存在/将存在。** |
| **管理 + 网关层（本计划）** | 本项目 backend/frontend | 下游用户密钥(sk-)的签发/鉴权/额度、申请与审核、上游凭据(sub/apikey)的**配置管理**、容器的**编排策略配置**、统一网关入口、用量计费日志、用户端 + 管理端两套界面。 |

一句话：**容器团队把"算力"做出来，我们把它包成 oneapi —— 发 key、管额度、配渠道、出账单。**
我们对容器团队的唯一依赖是一个**约定接口**（见 §8），用它把"某用户的一次请求"投递进某容器并拿回结果。该接口形态需与容器团队对齐（§10 待确认 1）。

---

## 1. 目标

1. **用户端**：注册/登录 → **申请 apikey**（填用途/额度）→ 查看自己的 key、用量、额度、状态。
2. **管理端**：**审核**用户申请（批准/驳回）、**直接分配/签发 key** 给用户、调额度、禁用/吊销。
3. **上游管理**：管理 **sub 认证**（订阅凭据健康态、重登入口）、管理 **container**（每个容器用 sub 还是 apikey、fallback 顺序）、管理 **GLM / 通义千问(qianwen) / ChatGPT / DeepSeek 等 apikey**。
4. **网关**：对外暴露兼容端点；用户拿 `sk-` key 调用 → 鉴权 → 限额 → **路由到容器** → 容器内 **sub 优先，sub 不够自动接 apikey**（后台可配）→ 回结果 → 记账。
5. **计费/审计**：每次调用落 usage 日志（key、容器、上游凭据、tokens、延迟、状态），支撑额度扣减与看板。

---

## 2. 概念模型（与 oneapi 对照）

| 本项目概念 | oneapi 对应 | 说明 |
|---|---|---|
| **用户令牌 / API Key（下游）** | 令牌(Token) | 用户实际拿去用的 `sk-...`。绑定用户、有额度/模型白名单/有效期/状态。 |
| **上游凭据 Credential** | 渠道(Channel) | 一份"算力身份"：要么是 **subscription slot**（容器里的订阅），要么是 **api_key**（GLM/千问/ChatGPT/DeepSeek）。等同于 claude-docker-plan 里的 **slot**。 |
| **容器 Container** | （oneapi 无对应） | 运行单位。绑定一个**主 sub** + 一组**fallback apikey**，定义"sub 不够时接谁"。 |
| **申请单 Application** | （oneapi 无对应，我们新增） | 用户申请 key 的工单，走 pending→approved/rejected。 |
| **用量日志 Usage Log** | 日志/额度 | 每次调用一条，driver 额度扣减与计费。 |

> 关键映射：**"一切皆 sub，sub 不够接 apikey"** = 一个 Container 的**凭据优先级链** = `[subscription, apikey_a, apikey_b, ...]`，按序取第一个健康的。后台可逐容器配置（A 容器纯 sub，B 容器 sub→GLM，C 容器纯 apikey…）。

---

## 3. 数据模型（Postgres，沿用 `db/migrations/NNNN_*.sql` 轻量迁移）

> 现有库**还没有任何业务表**（migrations 目录为空，仅 `.gitkeep`）。`security/admin.py` 提到的 `vibe_users` 也尚未建。本计划一并补上 users。命名前缀统一 `ak_`（apikey 域），避免与容器团队的 `provider_slots` 等冲突。

1. **`ak_users`** — 用户账号（**自助注册**）
   `id, email(uniq), password_hash, role(user|admin), status(active|disabled),`
   `balance_micro_usd(BIGINT, 微美元；注册自动充 $20 = 20_000_000), created_at`
   （余额用整数微美元避免浮点；bootstrap admin 仍走 `.env ADMIN_EMAILS`，与现有 `require_admin` 一致；DB role=admin 为运行时提升。）

2. **`ak_api_keys`** — 下游用户令牌（**只存 hash**）
   `id, user_id→ak_users, name, key_prefix(展示用 sk-xxxx…), key_hash, status(active|disabled|revoked),`
   `quota_cap_micro_usd(可空，单 key 子限额上限；空=不限，直接吃用户 balance),`
   `rate_limit_rpm, allowed_models(jsonb/text[]，空=全部), expires_at, last_used_at, created_at`
   — **扣费从 `ak_users.balance` 走**；key 上的 `quota_cap` 只是该 key 的封顶，不是独立钱包。
   — 注册成功时**自动签发一把默认 key**，配合 $20 余额即可立即试用。

3. **`ak_model_prices`** — 逐模型定价（**计费核心**）
   `id, model(uniq, 如 claude-opus-4 / glm-4.6 / qwen-max), display_name,`
   `input_micro_usd_per_1k(输入每 1k token 微美元), output_micro_usd_per_1k(输出每 1k token),`
   `enabled, updated_at`
   — 单次成本 `cost = in_tok/1000*in_price + out_tok/1000*out_price`，从 balance 原子扣减。
   — 降级到 apikey 时模型会变（如 GLM），**按实际命中模型的价**计费。admin 可改价。

4. **`ak_topup_requests`** — 加额度/充值申请单（原"申请单"，$20 试用是自动给的不走这里）
   `id, user_id, requested_micro_usd, reason, status(pending|approved|rejected),`
   `review_note, reviewed_by, reviewed_at, created_at`
   批准时 service 把 `requested_micro_usd` 加到 `ak_users.balance`。

5. **`ak_credentials`** — 上游凭据（= slot）
   `id, kind(subscription|api_key), name, enabled, weight,`
   `provider(claude|glm|qianwen|chatgpt|deepseek|...),`
   `base_url, model, auth_ref(指向密钥的引用，**密文不入库见 §9**), anthropic_compatible(bool),`
   `health(healthy|unhealthy|cooldown), cooldown_until, last_checked_at`
   — subscription 行：凭据实体在容器里，本表只存元数据 + 健康态。
   — api_key 行：`base_url/model/auth_ref` 用于注入容器或经 LiteLLM 中转。

6. **`ak_containers`** — 容器注册 + 凭据优先级链
   `id, name, endpoint/handle(投递用), status(up|down|draining),`
   `credential_chain(jsonb: 有序 credential_id 列表，sub 在前 apikey 兜底),`
   `fallback_trigger(jsonb: 触发降级的条件，如 sub 401/限额/超时),`
   `max_concurrency, created_at`

7. **`ak_usage_logs`** — 用量/计费
   `id, api_key_id, user_id, container_id, credential_id, model,`
   `prompt_tokens, completion_tokens, total_tokens, cost_micro_usd, latency_ms, status, error_code, request_id, created_at`
   （高频写：先入 Redis/队列再批量落库，复用现有 redis util；扣费用 `ak_users.balance` 原子 `UPDATE ... SET balance = balance - $cost WHERE balance >= $cost` 更新。）

---

## 4. 网关与路由（核心链路）

```
用户带 sk-key 调 /v1/...  ──▶  ① 鉴权 sk-key → user、查 status/expiry
                              ② 限额：balance > 0？rate_limit_rpm？model 在白名单？key 未超 quota_cap？
                              ③ 选容器：按 user_id 做 sticky 路由（HRW/哈希，复用容器层约定）
                              ④ 沿 container.credential_chain 取第一个 healthy 凭据
                                    └ subscription 优先：docker exec（容器烘好的 sub，不注入 ANTHROPIC_*）
                                    └ 命中 fallback_trigger(401/限额/超时) → 顺位到下一个 apikey：
                                       同容器 docker exec，但注入该 apikey 的 ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL env
                              ⑤ docker exec `claude -p ...`（HOME=/workspace/<user_id>）→ 拿结果（SSE 流式复用 frame/sse.py）
                              ⑥ 按命中模型查 ak_model_prices 算 cost → 记 usage_logs、
                                 原子扣 ak_users.balance、更新 last_used_at
```

- **投递方式（已定）**：后端**直接 `docker exec`**，不经 HTTP 中转。选容器 + 选凭据 + 注入 env + exec + 解析输出，全在后端 gateway service 里。
- **下游兼容协议（已定）**：**只做 Anthropic Messages 兼容**（claude code 原生）。OpenAI 兼容延后(P2/暂不做)。
- **sub→apikey 降级（已定，在网关/后端层）**：sub 烘在容器里，exec 不注入端点即用 sub；触发 fallback 时**对同一容器 exec 重试**，注入该 apikey 的 `ANTHROPIC_*` env 覆盖。降级会切换实际模型（如 GLM），属已知产品取舍，符合需求描述。
- **健康与故障转移**：`ak_credentials.health` 由探针/调用结果驱动；unhealthy 的凭据在链中被跳过，整容器不可用时路由层换容器（HRW 剔除）。

---

## 5. API 设计（FastAPI，沿用 `controller/ → services/`，统一 `/api` 前缀）

### 5.0 账户 `/api/auth/*`（无需鉴权）
- `POST /auth/register` 自助注册 → **自动充 $20 余额 + 自动签发首把 key**（明文只回一次）
- `POST /auth/login` 签发 JWT（复用 `jwt_handler`）

### 5.1 用户端 `/api/portal/*`（`Depends(require_access_token)`）
- `GET  /portal/me` 账户 + **余额** 概览
- `GET  /portal/keys` 我的 key 列表（脱敏，只回 prefix） / `POST /portal/keys` 自助新建 key
- `POST /portal/keys/{id}/rotate` 轮换（可选 P2） / `PATCH` 改名/禁用
- `GET  /portal/keys/{id}/usage` 我的用量（token + 花费）
- `POST /portal/topups` 提交加额度/充值申请 / `GET /portal/topups` 我的充值申请列表

### 5.2 管理端 `/api/admin/*`（`Depends(require_admin)`）
- 充值审核：`GET /admin/topups?status=pending`、`POST /admin/topups/{id}/approve|reject`（批准即加 balance）
- 直接调额：`POST /admin/users/{id}/grant`（手动给某用户加余额）
- Key 管理：`POST /admin/keys`（指定 user、模型白名单、有效期）、`PATCH /admin/keys/{id}`（禁用/吊销/封顶）
- **模型定价**：`GET/POST/PATCH /admin/model-prices`（逐模型设输入/输出单价、上下架）
- 上游凭据：`GET/POST/PATCH /admin/credentials`（含 sub 健康看板、`POST /admin/credentials/{id}/relogin` 触发容器层重登）
- 容器：`GET/POST/PATCH /admin/containers`（配 `credential_chain`：纯 sub / sub→GLM / 纯 apikey…）
- 用户：`GET /admin/users`、`PATCH /admin/users/{id}`（提升 admin/禁用）
- 看板：`GET /admin/usage`（按 user/key/容器/凭据/模型聚合 token + 花费）

### 5.3 网关 `/v1/*`（**自有 sk-key 鉴权，不走 JWT**）
- `POST /v1/messages`（Anthropic 兼容，含 stream）— **唯一对外端点**
- ~~`/v1/chat/completions`（OpenAI 兼容）~~ 延后(P2/暂不做)

> 鉴权拆分：门户用现有 JWT（cookie/Bearer）；网关用 `sk-` key（新增 `security/api_key_auth.py`，查 `ak_api_keys.key_hash`）。两套互不影响。

---

## 6. 前端页面（React + Vite + TS，`/api` 反代）

- **用户端**：注册/登录 →「我的 Key」（**余额**显示、新建/吊销）→「用量明细」（token + 花费）→「充值申请」。
- **管理端**（admin 可见）：「充值审核」工单台 →「Key/用户管理」（调余额）→「模型定价」→「上游凭据」（sub 健康灯 + apikey 列表）→「容器编排」（选择凭据链）→「用量看板」。

---

## 7. 实施里程碑（每次改动回来勾选）

### A0 · 设计确认（✅ 完成）
- [x] 摸清现有后端骨架（JWT/admin/migrate/sse/redis 可复用）
- [x] 产出本 plan
- [x] 用户确认 §10 全部 4 项（计费=token逐模型 / 自助注册 / 注册送$20 / 按user路由）
- [x] 发现容器团队已落地 `services/claude/*` 引擎 → 重定边界：本系统消费而非自建上游

### A1 · 数据与账户底座（✅ 完成）
- [x] migration `0001_apikey_core.sql`：`ak_users(含 balance) / ak_api_keys / ak_model_prices / ak_topup_requests / ak_usage_logs`（含预置模型价）
- [x] `controller/auth.py` 注册/登录/登出（复用 `jwt_handler`/`password`）：**注册自动充 $20 + 签发首把 key**，写 httponly cookie
- [x] `security/api_key_auth.py`：sk-key 生成（前缀+sha256 hash）、`authenticate_key` 校验依赖

### A2 · 用户端：Key + 余额 + 充值申请（✅ 完成）
- [x] `controller/portal.py` + `services/apikey/{users,keys,topups,usage}.py`：me/余额、key 列表+新建+禁用、用量、topup 申请
- [x] 前端 `pages/UserDashboard.tsx`（我的 Key / 用量 / 充值）

### A3 · 管理端：审核 + 定价 + 用户（✅ 完成）
- [x] `controller/admin_apikey.py`：topup 审核(批准即加余额)、调余额、key 管理、模型定价 CRUD、用户管理、用量看板聚合
- [x] `services/apikey/pricing.py` 逐模型计价；前端 `pages/AdminDashboard.tsx`

### A4 · 上游与容器配置（✅ 由容器团队提供，本系统对接）
- [x] ~~自建 ak_credentials/ak_containers~~ → 改为消费 `services/claude/*`（slot=上游凭据）
- [x] 前端「上游凭据/容器」页对接 `/api/admin/claude/slots`、`/containers`（健康/拉起/删除）

### A5 · 网关打通（✅ 完成，待真实 slot 联调）
- [x] `controller/gateway.py`：`/v1/messages`（Anthropic 兼容，含 SSE 流式）+ 裸 `/v1/messages` 给 SDK base_url
- [x] `services/apikey/runner.py`：复用容器团队路由+容器，跑 `claude -p --output-format json` 拿 usage；sub→apikey 为 slot 池级故障转移
- [x] `services/apikey/usage.py`：按命中模型计价、原子扣 balance、`ak_usage_logs` 落库；前置校验余额/封顶/模型白名单
- [ ] **真实 slot 镜像端到端联调**（需容器团队预登录镜像就位）

### 测试（✅）
- [x] `tests/test_apikey_unit.py`（10）：计价/sk-key/prompt 摊平/JSON 解析/微美元换算（无需 DB）
- [x] `tests/test_apikey_e2e.py`（4）：注册→充值→审核→计费全链（真 PG）+ 网关鉴权 + admin 守卫 + 墨行双账本守恒
- [x] 全套 97 测试通过（含真实 PostgreSQL）；真实 uvicorn cookie 鉴权 + 网关 401 冒烟通过

### A6 · 加固（待办，P2）
- [ ] 限流（`rate_limit_rpm` 已建字段，未接执行）、并发闸、注册/申请防刷
- [ ] usage 高频写改 Redis 缓冲批量落库（当前同步写）
- [x] OpenAI 兼容端点 `/v1/chat/completions`（直连墨行 GLM/Kimi，保留订阅与 Gemini 兜底）
- [ ] 多轮 messages→单 prompt 目前为摊平，富对话场景可优化

### A7 · GLM/Kimi 路由与用户分层（✅ 完成）
- [x] 墨行直连 `glm-5.2` / `kimi-k3`，同时支持 Anthropic Messages 与 OpenAI Chat Completions
- [x] 免费及赠送余额用户强制路由 `glm-5.2`；已确认真实充值用户打 `full_model_access` 标签后可用全部模型
- [x] 人工充值审核、Polar 与迅虎支付成功时自动授予全模型权限；管理端支持查看与调整标签
- [x] Claude 与 GLM 首页按官网价 8 折展示；Kimi K3 按官网原价展示并提示资源短缺
- [x] 保留现有 subscription → 墨行 → Gemini 故障转移链路，并为墨行直连补齐流式响应与精确计费

### A8 · 墨行供应商成本与对账（✅ 完成）
- [x] 独立供应商资金账本：充值、调账、请求消耗均生成不可覆盖的流水；错误只能用反向调账修正
- [x] 每次墨行请求同时保存客户销售额、官网价、上游商务折扣、供应商成本、销售折扣、用户折扣以及实付/赠送扣款
- [x] 上游实际模型与客户请求模型分别保存，覆盖 `direct-moxing`、`fallback-moxing` 等所有墨行 slot
- [x] 支持按 RMB/USD 录入充值，人民币记录保留当时汇率；墨行余额仅由充值、调账和请求成本自动计算，不接受人工填写
- [x] 商务后台可分别调整官网价、墨行给我们的成本折扣和我们对客户的销售折扣；销售价自动同步到客户计费表
- [x] 日维度、模型维度和请求级明细展示销售额、供应商成本、毛利及实付贡献；成本未定价/usage 缺失单独告警
- [x] 墨行官网价、供应商余额和成本流水使用原生 micro-CNY：GLM 5.2 为 ¥8/¥28/¥2，Kimi K3 为 ¥20/¥100/¥2（输入/输出/缓存读，每百万 token）；不再经美元汇率近似成本
- [x] 缓存读写 token 逐笔独立保存；Kimi K3 对标账单可精确复算 `524513×20 + 523776×2 + 561×100 = 11593912 micro-CNY = ¥11.593912`

**对账定义**：`充值 + 调账 - 墨行请求成本 = 墨行余额`；`客户销售额 - 供应商成本 = 毛利`；
`客户实付扣款 - 供应商成本 = 现金贡献`。供应商余额允许为负数，
用于明确提示漏录充值，不阻断客户请求。migration `0020` 上线前的历史请求不伪造供应商成本，精确追踪从
`tracking_started_at` 开始。

---

## 8. 与容器团队的集成（已落地 = 复用 services.claude 引擎）

容器团队已实现 `services/claude/*`：`registry.get_router()`（HRW 路由，按 user_id）、
`docker_manager`（容器生命周期 + `exec_claude`）、`health`（探针保活）。本系统：

- `services/apikey/runner.py` **复用其路由 + 容器生命周期**，但把执行命令换成
  `claude --dangerously-skip-permissions --output-format json -p <prompt>`（订阅档可加 `--model`），
  从 JSON 输出里解析 `usage.input_tokens / output_tokens` 与结果文本 → 供计费。
- **「sub 用光接 apikey」= slot 池级故障转移**：sub slot 撞 401/限额 → 标 unhealthy →
  HRW 从候选剔除 → 落到其它健康 slot（可为 api_key slot）。与 `docker_manager.exec_claude` 同构。
- 用户身份统一用 `controller/claude.py` 的 `_safe_uid(user)`（sha256→`u-...`），保证经
  `/v1/messages` 与 `/claude/chat` 路由到**同一 slot、同一容器工作目录**。

**集成缝**：runner 触达 `services.claude` 的 `_client/_chown_tree` 等内部 helper。若容器团队
日后提供「带 usage 的官方 exec」，可改为直接调用并删掉 runner 里的重复执行逻辑。

**计价模型来源**：订阅档 = 请求模型；api_key 档（降级后）= 该 slot 注入的 `ANTHROPIC_MODEL`
（`runner._billed_model`），符合「降级按实际命中模型计价」。

---

## 9. 安全

- sk-key **只存 hash**（如 sha256），库里不留明文；签发时只回一次明文给用户。
- 上游 apikey / sub token **绝不入库**：由容器团队的 slot 配置（`slots.json` 的 `env` / 预登录镜像）承载，本系统不碰明文。
- 网关与门户**鉴权隔离**；admin 接口走 `require_admin`（白名单 + DB role 双判，已存在）。
- 额度/限流防滥用；申请与注册加防刷。
- 审计：所有签发/审核/调额/吊销留操作日志。

---

## 10. 待确认（✅ 已全部拍板 2026-06-27）

- ~~容器投递接口形态~~ → **docker exec，降级在网关/后端层**
- ~~下游兼容协议~~ → **只做 Anthropic 兼容**
- ~~额度单位~~ → **按 token，逐模型定价**（不同模型不同输入/输出单价）
- ~~用户来源~~ → **自助注册 `ak_users`**
- ~~申请策略~~ → **注册自动送 $20 余额试用（无需审核）**；额外额度走 topup 申请 + admin 审核
- ~~路由 sticky 维度~~ → **按 user_id**（HRW）

仅剩与容器团队对齐的 exec 细节（§8），不阻塞 A1 起步。

---

## 11. 变更记录（changelog）
- **2026-07-22** · 用户调用明细拆分展示输入、输出和缓存 token；管理后台模型定价统一改为每百万 token，并补充缓存读/写价格。用量看板新增全站调用明细，可按用户邮箱和北京时间日期区间筛选，并按当前筛选与显示币种导出标准 XLSX。
- **2026-07-22** · 墨行供应商成本改为原生人民币账本：官网价、供应商折扣、余额、充值和逐笔成本统一以 micro-CNY 精确计算；客户统一美元余额仅在请求时按汇率换算并固化快照。Kimi K3 对标调用 `524513` 未缓存输入、`523776` 缓存读、`561` 输出，精确得到 `¥11.593912`。
- **2026-07-22** · 全站显示币种默认改为人民币，保留 RMB/USD 一键切换并全局同步；首页、顶部余额、Key 花费、钱包、账单、价格、管理后台及墨行商务价格统一按选择币种和当前汇率显示。墨行余额改为“登记充值 + 调账 - 每次请求成本”的自动余额，移除人工填写余额快照入口。
- **2026-07-22** · **墨行供应商成本账本与请求级对账**。管理后台新增「墨行对账」：支持录入 USD/RMB 充值和调账，分别维护官网价、上游成本折扣和客户销售折扣。每次墨行请求在客户扣费同一事务中写入供应商成本流水并自动扣减墨行余额，保存上游实际模型和所有商务快照；提供余额守恒、日/模型/请求级销售额、成本、毛利、实付贡献和异常请求核查。普通用户接口仅公开销售定价，不暴露供应商折扣与成本。历史请求不回填虚构成本，从 migration `0020` 部署时点开始精确追踪。
- **2026-07-22** · 价格、用户账单和管理用量看板取消国内/海外模型币种区分，改为 USD/RMB 显示币种切换；选择后所有模型、日账单、调用明细和趋势图统一换算，选择会在浏览器本地保留。代码示范区同步增加安全语法高亮，复制内容仍为可直接运行的纯代码。
- **2026-07-22** · 管理后台用量看板新增近 7/30/90 天筛选、每日消费趋势图和按日账单；趋势、日账单及各维度汇总全部遵循统一的 USD/RMB 显示币种选择。
- **2026-07-22** · 「使用说明」新增可直接运行的代码示范：支持 Python、Java、Node.js、Go、PHP，统一调用 OpenAI 兼容 `/v1/chat/completions`，仅使用各语言标准库；支持切换模型、复制占位示例或一键填入真实 Key，存在多个可用 Key 时复用弹窗选择流程。
- **2026-07-22** · USD/CNY 从固定 `7.2` 改为 Frankfurter 最新综合参考汇率：服务端缓存 1 小时，账单、价格表、钱包与虎皮椒人民币收款统一使用；接口同时返回汇率日期、来源及实时/兜底状态。外部服务失败或数据越界时回退 `XUNHUPAY_RMB_PER_USD`，不阻断页面和支付。
- **2026-07-22** · 「我的 Key」新增「密钥管理 / 使用说明」子标签：新建与管理 Key 保留在密钥管理，Cursor 和 Claude Code 接入教程集中移动到使用说明；子标签写入 URL，刷新及前进后退保持当前位置。
- **2026-07-22** · **用户账单与钱包**。用户控制台将原「用量明细 / 充值」升级为「我的账单 / 我的钱包」：新增 7/30/90 天账单总览、每日汇总、按模型费用分布与分页调用明细；钱包集中展示有效余额、充值余额、赠送余额、线上支付及人工充值。账务仍统一按 micro-USD 精确结算，展示层按当前充值汇率将 GLM、Kimi、Qwen、DeepSeek 等中国模型换算成人民币，Claude/GPT 等海外模型保留美元；价格表同步采用相同币种规则。
- **2026-07-22** · 首页价格表隐藏 `claude-sonnet-4-6` 与 `claude-haiku-4-5`；仅调整营销页展示，不下架模型、不修改控制台选择或接口计费。
- **2026-07-22** · **GLM/Kimi 接入、定价与用户权限分层**。新增墨行 `glm-5.2` / `kimi-k3` 直连，兼容 `/v1/messages` 与 `/v1/chat/completions`，且保留原 subscription → 墨行 → Gemini 兜底链。新增 `full_model_access` 用户标签：免费及赠送余额用户无论请求何模型都强制走 GLM 5.2；人工充值审核或线上支付成功后自动开放全部模型，管理端可查看和调整。migration `0017` 将 Claude 改为官网价 8 折，`0018` 增加并回填全模型权限，`0019` 增加 GLM/Kimi 定价。首页同步展示 GLM 5.2 官网价 8 折与 Kimi K3 原价/资源短缺说明，控制台补齐模型选择与价格。
- **2026-06-27** · **远端上线 + 端到端联调通过**。在 `8.216.44.14` 用 `devops/deploy` 的 docker-compose 起 db+backend+web 三容器（backend 以 root 挂 docker.sock + 同路径挂 `/var/lib/substantia/claude`，故能 exec 兄弟容器 `claude-slot-sub-a`）。真实跑通 `/v1/messages`→docker exec→真 Opus 4.8→计费扣款。**计费修复**：`runner` 改为从 `claude --output-format json` 的 `modelUsage` 解析**实际命中模型**（`claude-opus-4-8`）作为计价依据（订阅档实际模型 ≠ 请求别名）；新增 `_cli_model` 把规范名/别名归一成 CLI 认的 `opus/sonnet/haiku`；migration `0002` 补真实模型名定价。验证：一次 pong（in 21122 / out 4 token）精确扣 317130 micro=$0.317。**遗留**：edge-nginx 域名反代、cache_read token 单独低价、限流执行、OpenAI 兼容（P2）。
- **2026-06-27** · **实现完成（后端+前端）**。发现容器团队已落地 `services/claude/*` 引擎 + `controller/claude.py`，遂重定边界：删掉自建 `ak_credentials/ak_containers`，slot 即上游凭据，本系统消费引擎。落地：migration（5 表）、`security/api_key_auth`、`services/apikey/{__init__,pricing,users,keys,topups,usage,runner}`、`controller/{auth,portal,admin_apikey,gateway}`、前端（api 客户端 + Login + User/Admin 仪表盘 + ui.css）。`runner.py` 跑 `claude --output-format json` 拿 token 计费；sub→apikey = slot 池故障转移；统一 `_safe_uid` 路由一致。测试 32 绿（10 单测 + 3 e2e + 容器团队 19），真实 uvicorn cookie 鉴权冒烟通过。**遗留**：真实 slot 镜像联调、限流执行、usage 批量写、OpenAI 兼容（均 A6/P2）。
- **2026-06-27** · 用户拍板剩余 4 项：计费=token 且**逐模型定价**（新增 `ak_model_prices`，按命中模型计价，降级后按实际模型价）；**自助注册**；**注册自动送 $20 余额**（改用户 `balance` 模型，原"申请单"改为 `ak_topup_requests` 充值申请走 admin 审核）；**按 user_id 路由**。同步 §3 表结构、§4 计费步、§5 增 `/auth/*` 与 `/admin/model-prices`、§6/§7/§10。设计确认(A0)完成，待容器团队对齐 exec 细节后进 A1。
- **2026-06-27** · 用户拍板三项：投递 = `docker exec`（HTTPS→后端 exec→返回）；sub→apikey 降级在网关/后端层（exec 重试注入 `ANTHROPIC_*` env）；下游只做 Anthropic 兼容（OpenAI 延后）。同步 §4/§5.3/§8/§10/A5。待确认收敛到 4 项（计费单位/用户来源/审核策略/sticky 维度）。
- **2026-06-27** · 初版草案：确立"上游凭据(slot)/容器/下游 sk-key/申请/用量"五件套数据模型；明确与容器团队边界（只管理不实现容器）；定义网关链路与 sub→apikey 降级=容器凭据链；列出 A0–A6 里程碑与 6 项待确认。**未动代码，等用户确认。**
</content>
</invoke>
