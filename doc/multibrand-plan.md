# 多品牌统一方案（方案 A：一套代码 + 分开部署 + 数据隔离）

目标：**只保留 `substantia-ai`（前端）/ `substantia-api`（后端）一套代码**，
按域名/部署显示不同品牌。**退役 `prod-ai` / `prod-api` 两个重复仓库**（不再各改一遍）。
数据分开：每个品牌一套独立数据库 + 独立容器栈，互不干扰。

## 核心思路

一套代码，部署 N 次，每次用不同环境变量决定品牌 + 域名 + 数据库：

```
substantia-ai / substantia-api （唯一代码源）
        │
        ├── 部署实例①  BRAND=substantia  域名 substantia.ai   DB=substantia_db
        └── 部署实例②  BRAND=yaya        域名 yayaok.com        DB=yaya_db
```

品牌之间只差「配置」，不差「代码」。

## 品牌差异点盘查（就这些需要按品牌区分）

**后端 `substantia-api`（大部分已是环境变量，改动小）**
- `SMTP_FROM_NAME`（发件人显示名）、`SMTP_FROM`
- `AUTH_COOKIE`（cookie 名，避免同浏览器跨品牌串登录）
- `PAYMENT_RETURN_URL` / `RECHARGE_URL`（支付回跳）
- `APP_NAME`（`config/version.py`，目前硬编码 → 改成读 env）
- 邮件模板里的品牌名/署名（若有硬编码 → 改成读 brand 配置）

**前端 `substantia-ai`**
- 站点名 / logo / favicon / 主题色 / 首页文案 / 页脚版权 / SEO 标题
- 客服/联系方式、条款链接

## 实施步骤

### 1. 建「品牌配置层」（单一事实源）
- 后端：`config/brands.py` — `BRANDS = {"substantia": {...}, "yaya": {...}}`，
  按 `settings.BRAND` 取当前品牌的 name/邮件署名/条款链接等；`settings.BRAND` 从 env 读（默认 substantia）。
  已是 env 的字段（SMTP_FROM_NAME 等）可继续用 env，或统一收进 brands.py 由 BRAND 派生（推荐统一，少配几个 env）。
- 前端：`src/brand/brands.ts` — 同样一张表（name/logo/主题色/文案/URL）。
  品牌选择二选一：
  - **构建期**：`VITE_BRAND=yaya npm run build`（每品牌各 build 一份镜像，最简单，静态资源可预置）
  - **运行期**：读 `window.location.hostname` → 查表（一份构建多域名共用，但 logo/favicon 等静态资源要都打进包）
  方案 A 是分开部署，**推荐构建期 `VITE_BRAND`**：干净、每实例只含自己品牌资源。

### 2. 把硬编码品牌串替换成读配置
- 后端：`APP_NAME`、邮件模板等硬编码 "Substantia" → 读 `brands.py`。
- 前端：全站 "Substantia"/logo/文案 → 读 `brands.ts`（用当前 BRAND）。
  （参考境核AI 那次 `实境AI→境核AI` 全站替换的做法，但这次是抽成配置而非直接替换。）

### 3. 部署改造
- `substantia-*` 的部署脚本/compose 支持传 `BRAND` + 品牌专属 `.env`（域名、DB、支付 key、SMTP）。
- 每品牌一套：独立 DB 容器 + 独立后端/前端容器 + edge-nginx 加该域名的 server 块指向该实例。

### 4. 从 prod-* 迁移过来（数据保留）
- yaya(prod-api)现有数据库**原样保留**，新实例用 substantia 代码 + `BRAND=yaya` + 指向 yaya 的 DB。
- 确认 yaya DB 的 schema 与 substantia 当前 migrations 兼容（若 prod 曾 diverge，先跑一遍 migration 对齐；差异大则单列一步核对）。
- 域名解析/edge-nginx 从旧 yaya 实例切到新实例，验证后下线旧实例。
- **归档 `prod-ai` / `prod-api` 仓库**（GitHub archive，不删，留痕）。

### 5. 验证
- 两个域名各自打开：品牌名/logo/配色/邮件署名/支付回跳都对得上各自品牌。
- 跨品牌不串登录（AUTH_COOKIE 不同）。
- 各自数据库隔离：A 品牌看不到 B 的用户/订单。

## 关键决策 / 风险
- **cookie 名必须按品牌区分**，否则同浏览器登录会互相覆盖。
- **yaya DB 与 substantia 代码的 schema 兼容性**是最大不确定项 —— 迁移前必须核对 migrations，
  prod-api(yaya) 若已 diverge（它有自己的提交），要先把差异 merge 进 substantia 或写迁移脚本。
- 支付/SMTP 等密钥按品牌各配一份，`.env` 不入库（沿用现有纪律）。
- 静态资源（logo/favicon）：构建期方案下，每品牌资源放 `src/brand/<brand>/`，按 VITE_BRAND 引入。

## 工作量粗估
- 品牌配置层 + 替换硬编码：后端 0.5 天、前端 1~1.5 天（取决于前端品牌串多少）。
- 部署改造 + prod 数据迁移核对：1 天（schema 兼容是变量）。
- 合计约 2.5~3.5 天，分阶段：①配置层 ②前端接入 ③部署+迁移 ④验证下线旧仓库。

## 下一步
先做①品牌配置层（后端 brands.py + 前端 brands.ts，把两个品牌的差异值列全），
这步不影响线上、可独立验证。确认后再逐步接入、迁移。

---

## 附：yaya(prod-*) vs substantia 分歧实测结论（已核对，de-risk）

**最大风险已解除**：`db/migrations/0001_apikey_core.sql` 的差异**仅一行注释**
（`sk-yaya-…` vs `sk-substantia-…`），**schema 完全一致** → yaya 数据库可直接跑 substantia 代码，无需迁移对齐。

**真实源码分歧很小，且几乎都是品牌值**（正好是要抽进配置层的东西）：
- **key 前缀**：`sk-yaya-*`（（yaya）vs `sk-substantia-*` —— `security/api_key_auth.py` + 前端展示。→ 抽成 `BRAND.key_prefix`
- **品牌名/文案**：`config/version.py`(APP_NAME)、`email_service.py`(邮件署名)、
  前端 `Landing.tsx`/`Login.tsx`/`UserDashboard.tsx`/`i18n.tsx`/`index.html`(站名/标题/文案)
- **settings.py**：SMTP_FROM_NAME / AUTH_COOKIE / 回跳 URL（已是 env，按品牌配）
- **支付**：`services/apikey/payments.py`/`xunhupay.py`/`webhooks.py` —— 需确认是"品牌差异(key/回跳)"还是"功能差异"，逐个核对（多半是 key，走 env）
- **claude 相关**（`docker_manager.py`/`health.py`/`registry.py`）差异 = substantia 刚加的共享账号池(PR #7)，**prod-api(yaya) 只是还没有** → 统一后自动获得，不是真分歧

**结论**：分歧小、无 schema 障碍，方案 A 高度可行。要抽进品牌配置层的具体值已列全（见上）。

## 修订后的下一步（更明确）
1. 前端 `src/brand/brands.ts` + 后端 `config/brands.py`：把上面列的品牌值（key_prefix / 名称 / 文案 / SMTP署名 / 回跳）填成两份 brand 配置，`BRAND` env 选择。
2. 替换硬编码（sk-yaya、Substantia、站名文案）为读配置。
3. 部署：substantia 代码 + `BRAND=yaya` + 指向 yaya 现有 DB + yayaok.com → 验证 → 归档 prod-* 仓库。
   （无需数据迁移，DB 原样复用。）

---

## ⚠️ 决定变更（最新，覆盖上面方案 A 的"数据分开"）

用户最终确认：**一套代码 + 一个数据库(不分库) + 按域名运行时切品牌**。
即 yayaok.com 和 substantia.ai 由**同一套部署、同一个库**服务，仅品牌皮肤按请求域名切换。
（这是原方案 B：runtime brand-by-domain + 数据共享。）

### 实现要点
- **前端**（substantia-ai）：读 `window.location.hostname` → 查 `brands.ts` → 渲染对应品牌
  (name/logo/favicon/主题色/文案/SEO)。一份构建，两品牌资源都打进包，按域名选。
- **后端**（substantia-api）：按请求 `Host` 头决定品牌相关输出
  (邮件署名、API key 前缀 sk-yaya/sk-substantia、支付回跳)。用中间件解析 Host → 注入 brand 到 request 上下文。
- **部署**：substantia 一套栈同时服务两域名；edge-nginx 把 yayaok.com + substantia.ai 都指向同一 substantia 容器。
- **退役**：prod-api/prod-ai(yaya) 仓库 + yaya-* 容器停用归档。

### ‼️ 必须先拍板的一个点：现有两个库怎么并成一个
现在 `substantia-api-db` 和 `yaya-api-db` 是两个独立库，各有各的用户/订单/API key。
"一个库"意味着二者要合一，需定：
- **(a) 保留其一、丢弃另一**：若某品牌数据可弃(如刚上线没什么真实用户)，直接用另一个库，最简单。
- **(b) 合并两库**：都要保留 → 需数据迁移(处理用户邮箱/ID 冲突、API key 唯一性)，工作量与风险大。
- key 前缀不再区分品牌？共享库里 sk-yaya 和 sk-substantia 混在一张表 → 需保证前缀+随机段全局唯一(通常没问题)。

**未定这点无法安全实现**——否则可能覆盖/丢失某个品牌的现有数据。

### 实现顺序（拍板后）
1. 前端 brands.ts + 按 hostname 切换 + 替换硬编码 → 本地可验证(改 host 试两品牌)
2. 后端 Host→brand 中间件 + 邮件/key前缀/回跳按 brand
3. 数据库按 (a)/(b) 处理
4. edge-nginx 两域名指向同栈 + 部署 + 验证 + 停用 yaya 旧栈

---

## ✅ 最终锁定决定（用户确认）

**保留 substantia 的代码 + substantia 的数据库；yaya 纯品牌皮肤，别的零区别。**
- 一套代码：`substantia-api` / `substantia-ai`
- 一个数据库：**substantia 现有库**（`yaya-api-db` 直接弃用，不迁移）
- 按域名切品牌：`substantia.ai` → substantia 皮肤；`yayaok.com` → yaya 皮肤
- 只有品牌层不同：站名/logo/favicon/配色/文案/SEO、邮件署名、API key 前缀(sk-substantia/sk-yaya)、支付回跳
- 退役：`prod-api`/`prod-ai` 仓库、`yaya-*` 容器、`yaya-api-db` 全部停用归档

数据库问题已无（用 substantia 一个库），所以**无数据迁移、无阻塞点**，可直接实现。

### 执行清单（新会话照此做）
1. **前端 substantia-ai**：
   - 新建 `src/brand/brands.ts`：`{ substantia: {...}, yaya: {...} }`（name/logo/favicon/主题色/文案/SEO/条款/客服）
   - `src/brand/current.ts`：按 `window.location.hostname`（含 yayaok → yaya，其余 → substantia）返回当前 brand
   - 全站硬编码 "Substantia"/logo/文案/`sk-substantia` 展示 → 改读 current brand
   - yaya 的 logo/favicon 资源放 `src/brand/yaya/`
   - 本地验证：改 hosts 或用 `?brand=yaya` 覆盖，两品牌都对
2. **后端 substantia-api**：
   - `config/brands.py`：同一张表（key_prefix / 邮件署名 / 支付回跳 / APP_NAME）
   - Host 中间件：解析请求 `Host` → 存 request.state.brand（yayaok→yaya，其余→substantia）
   - `security/api_key_auth.py` 生成 key 用 brand.key_prefix；`email_service.py`/version.py/回跳 按 brand
3. **部署**：
   - edge-nginx 把 `yayaok.com` + `www.yayaok.com` + `api.yayaok.com` 的 server_name 指到 substantia 的容器
   - 部署 substantia 栈；验证两域名各自品牌
   - 停用 `yaya-*` 容器 + `yaya-api-db`；归档 prod-ai/prod-api 仓库
4. **验证**：两域名品牌正确、API key 前缀按域名、邮件署名按域名、同库数据一致。

---

## 进度状态（接力用）

**已开工（substantia-ai 分支 `feat/multibrand-by-domain`，未合并 main，未 build 验证）：**
- `frontend/lib/site.ts`：`BRANDS{substantia,yaya}` + `getBrand(host)`（yayaok→yaya）；`SITE` 保留=substantia 向后兼容
- `frontend/lib/site-server.ts`：`getBrandFromRequest()`（读 Host，Next15 async headers）
- `frontend/app/(site)/[lang]/layout.tsx`：metadata（title/SEO/OG）已按域名切，转 `force-dynamic`

**新会话第一步**：`cd substantia-ai/frontend && npm i && npx next build` 验证上面无误 → 合并分支。

**待续（按此顺序）：**
1. 前端可见品牌名：`components/site/Nav.tsx`、`Footer.tsx` 的 logo/名称 → 用 `getBrandFromRequest()`（服务端组件）或从 layout 传 brand prop；`organizationJsonLd` 传入 brand
2. i18n 文案里的品牌串（`lib/dictionaries/zh.ts`/`en.ts` 若含"境核/Substantia"）→ brand 化
3. 后端 substantia-api：`config/brands.py` + Host 中间件（`request.state.brand`）→ `security/api_key_auth.py` key 前缀、`email_service.py` 署名、支付回跳按 brand
4. 部署：edge-nginx 把 yayaok.com/www/api 的 server_name 指到 substantia 容器；部署验证两域名品牌
5. 停用 yaya-* 容器 + yaya-api-db；归档 prod-ai/prod-api

**注意**：force-dynamic 使 [lang] 路由改为按请求渲染（品牌按域名必需，牺牲静态缓存，符合"一套部署双域名"）。

---

## ✅✅ 已完成上线（全部验证通过）

一套代码（substantia-ai/substantia-api）按域名切品牌，已上线并运行时验证：

- **前端**（substantia-ai #29）：`BRANDS`+`getBrand(host)`+`site-server`；metadata/Nav/Footer/JSON-LD/版权按域名。next build 绿，已部署。
- **后端**（substantia-api #8）：`config/brands.py`+Host 中间件；`generate_key` 用品牌前缀（yayaok→sk-yaya-，校验走哈希兼容两种）；邮件主题/署名按品牌。已部署。
- **nginx 切流**：yaya server 块的 `set $yaya_* <容器>` 改指 substantia 容器（`substantia-web`/`substantia-backend`/`substantia-api-backend`/`substantia-api-web`）。**注意：改后需 `docker restart edge-nginx` 才生效，`nginx -s reload` 不重新解析变量**。备份在 `nginx.conf.bak.multibrand`。
- **决定性验证**：停掉全部 `yaya-*` 容器后，yayaok.com 仍 200 且出「丫丫/Yaya AI」，api.yayaok.com 仍 `{"status":"ok"}` → 确认真正跑在 substantia 栈上。substantia.ai 仍出「境核智能」无回归。
- **清理**：`yaya-*` 容器 + `yaya-api-db` 已 `docker stop`（未删，可回滚）；`prod-ai`/`prod-api` 仓库已 GitHub archive。

### ⚠️ 数据后果（按用户决定）
用户选「保留 substantia 库、yaya-api-db 弃用」。故 **yaya 原有的用户/API key（在 yaya-api-db）不再生效**——api.yayaok.com 现在校验 substantia 库。若 yaya 曾有真实付费 key 用户，需重新签发。

### 回滚（如需）
1. `docker start yaya-web yaya-backend yaya-api-web yaya-api-backend yaya-postgres yaya-redis yaya-api-db yaya-api-redis`
2. `sudo cp /home/work/new-api/devops/edge-proxy/nginx.conf.bak.multibrand /home/work/new-api/devops/edge-proxy/nginx.conf`
3. `docker restart edge-nginx`

### 遗留（非阻塞，可后续）
- 子页 SEO metaDesc 里仍含默认品牌名「境核智能」（`lib/dictionaries/*.ts`）——首页/logo/标题/版权已品牌化，子页 SEO 描述为次要项，后续可加 brandize 批处理。
