# APIKey 分发与网关 — Plan（活文档）

> 本文是 substantia-api 的**「类 oneapi」密钥分发 + 网关 + 用户/管理员门户**的设计与实施计划。
> 约定：**以后每次改动都要回来更新「实施里程碑」勾选状态和「变更记录」。**
> 姊妹文档：容器/订阅运行层见 [`claude-docker-plan.md`](./claude-docker-plan.md)（由容器团队负责，本计划只**消费与管理**，不实现容器本身）。

最后更新：2026-06-27 · 状态：**草案待确认（未动代码）**

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
- [ ] 与容器团队对齐 §8 docker exec 细节（命名/workspace/输出格式/流式）

### A1 · 数据与账户底座
- [ ] migrations：`ak_users(含 balance) / ak_api_keys / ak_model_prices / ak_topup_requests`
- [ ] 注册/登录（复用 `security/jwt_handler`、`password`）：**注册自动充 $20 + 签发首把 key**
- [ ] `security/api_key_auth.py`：sk-key 生成（前缀+hash）、校验依赖

### A2 · 用户端：Key + 余额 + 充值申请
- [ ] `controller/portal.py` + `services/apikey/*`：me/余额、key 列表+新建、用量、topup 申请
- [ ] 前端用户端页面

### A3 · 管理端：审核 + 定价 + 用户
- [ ] `controller/admin_apikey.py`：topup 审核、调余额、key 管理、模型定价 CRUD、用户管理
- [ ] 前端管理端审核台 + Key/用户 + 模型定价

### A4 · 上游与容器配置
- [ ] migrations：`ak_credentials / ak_containers`
- [ ] credentials/containers 的 admin CRUD + sub 健康看板 + relogin 入口
- [ ] 前端「上游凭据」「容器编排」页

### A5 · 网关打通
- [ ] `controller/gateway.py`：`/v1/messages`（Anthropic 兼容，含 SSE 流式）
- [ ] 路由（选容器）+ 凭据链选择（sub→apikey 降级 = exec 重试注入 `ANTHROPIC_*`）
- [ ] `services/apikey/exec.py`：`docker exec claude -p`、解析输出/usage、流式
- [ ] `ak_usage_logs` 落库 + quota 原子扣减

### A6 · 计费/看板/加固
- [ ] 用量看板聚合接口 + 前端图表
- [ ] 限流（rate_limit_rpm）、并发闸、注册/申请防刷
- [ ] OpenAI 兼容端点（P2，LiteLLM）

---

## 8. 与容器团队的约定接口（已定 = docker exec）

后端 gateway service **直接 `docker exec`**（复用容器团队的 `docker_manager` / 容器命名约定）：

- 入参：`user_id`、`prompt/messages`、选中的 `container`、选中的 `credential`、是否 stream。
- subscription 凭据：`docker exec -e HOME=/workspace/<user_id> <container> claude -p ...`（不注入 `ANTHROPIC_*`，用容器烘好的 sub）。
- apikey 凭据（降级）：同上但加 `-e ANTHROPIC_BASE_URL=... -e ANTHROPIC_AUTH_TOKEN=... -e ANTHROPIC_MODEL=...`。
- 我们对容器团队的依赖：**容器存在且 sub 凭据可用**（命名/卷/保活由他们保证）；本层只读其约定即可 exec。

**需与容器团队对齐的细节**：容器命名规则、`/workspace/<user_id>` 工作区隔离约定、`claude -p` 的输出格式（用于解析 usage tokens）、流式输出方式。

---

## 9. 安全

- sk-key **只存 hash**（如 sha256），库里不留明文；签发时只回一次明文给用户。
- 上游 apikey / sub token **绝不入库明文**：`ak_credentials.auth_ref` 指向 `.env` 或密钥管理，沿用 `.gitignore` 已含 `.env` 的约定。
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
- **2026-06-27** · 用户拍板剩余 4 项：计费=token 且**逐模型定价**（新增 `ak_model_prices`，按命中模型计价，降级后按实际模型价）；**自助注册**；**注册自动送 $20 余额**（改用户 `balance` 模型，原"申请单"改为 `ak_topup_requests` 充值申请走 admin 审核）；**按 user_id 路由**。同步 §3 表结构、§4 计费步、§5 增 `/auth/*` 与 `/admin/model-prices`、§6/§7/§10。设计确认(A0)完成，待容器团队对齐 exec 细节后进 A1。
- **2026-06-27** · 用户拍板三项：投递 = `docker exec`（HTTPS→后端 exec→返回）；sub→apikey 降级在网关/后端层（exec 重试注入 `ANTHROPIC_*` env）；下游只做 Anthropic 兼容（OpenAI 延后）。同步 §4/§5.3/§8/§10/A5。待确认收敛到 4 项（计费单位/用户来源/审核策略/sticky 维度）。
- **2026-06-27** · 初版草案：确立"上游凭据(slot)/容器/下游 sk-key/申请/用量"五件套数据模型；明确与容器团队边界（只管理不实现容器）；定义网关链路与 sub→apikey 降级=容器凭据链；列出 A0–A6 里程碑与 6 项待确认。**未动代码，等用户确认。**
</content>
</invoke>
