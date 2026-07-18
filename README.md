# substantia-api

Substantia 项目的 API 服务与前端。后端采用分层的 **FastAPI** 框架，前端使用 **React (Vite + TypeScript)**。

## 目录结构

```
substantia-api/
├── backend/            # 后端服务（FastAPI）
│   └── src/
│       ├── config/         # settings / version / logging 配置
│       ├── controller/     # 路由层（health、example…）
│       ├── service(s)/     # 业务逻辑层
│       ├── security/       # JWT 鉴权、密码、依赖、admin 校验
│       ├── frame/          # 框架层：统一路由、异常处理、SSE
│       ├── utils/          # 日志、请求上下文、db、redis、性能监控等
│       ├── db/             # 轻量 SQL migration（db/migrations/*.sql）
│       ├── main.py         # 应用入口
│       ├── requirements.txt
│       ├── Dockerfile / docker-compose.yml
│       └── .env.example
├── frontend/           # 前端（React + Vite + TS），/api 反代到后端
├── devops/             # 部署与运维
└── doc/                # 项目文档
```

## 后端开发

```bash
cd backend/src
cp .env.example .env          # 按需修改（JWT_SECRET、DATABASE_URL 等）
./setup-venv.sh               # 创建 .venv 并装依赖
./startup-local.sh            # 启动（uvicorn 热重载，默认 :9999）
```

验证：

```bash
curl localhost:9999/api/health     # {"status":"ok"}
curl localhost:9999/api/version    # {"name":"substantia-api","version":"0.1.0"}
```

API 文档：http://localhost:9999/docs

容器方式：

```bash
cd backend/src && docker compose up -d --build   # app + redis + postgres
```

### 框架约定

- **统一响应/异常**：错误经 `frame/error_handler.py` 返回 `{code, message, detail, trace_id}`。
- **鉴权**：`Depends(require_access_token)` 校验 JWT（cookie 或 `Authorization: Bearer`）；
  `AUTH_DISABLED=true` 时本地跳过。
- **请求上下文**：每个请求带 `trace_id`/`request_id`，日志自动串联。
- **新增接口**：在 `controller/` 加路由 → `services/` 写业务 → 在 `main.py` 注册 router。

## 前端开发

```bash
cd frontend
npm install
npm run dev                   # http://localhost:6337，/api 自动反代到 :9999
npm run build                 # 产物在 dist/
```

## 部署

- API：`api.substantia.ai`
- IDE：`ide.substantia.ai`

### Claude 上游故障转移

生产环境使用固定的三级顺序，不会在兜底档之间随机选路：

| 优先级 | 上游 | 配置 |
|---:|---|---|
| `0` | Claude subscription | admin 管理的健康订阅 slot；同优先级内按 RR/weighted-HRW 配置路由 |
| `100` | moxing | Anthropic 兼容网关；`CLAUDE_FALLBACK_MOXING_*` 三项齐全才启用 |
| `200` | Gemini | 同机已有的 LiteLLM Anthropic bridge；`CLAUDE_FALLBACK_GEMINI_*` 三项齐全才启用 |

请求先走健康的 subscription；订阅出现鉴权失败、额度耗尽或其它可重试故障后转 moxing，moxing 仍不可用才转 Gemini。即使命中兜底档，计费仍以客户端原请求的 Claude 模型为准，不按实际 fallback 模型改价。

兜底档表在 `backend/src/services/claude/registry.py` 的 `FALLBACK_TIERS`：新增一档只需登记 id、priority、settings 前缀，三件套（`BASE_URL`/`AUTH_TOKEN`/`MODEL`）配齐即启用，未配齐的档自动跳过。

Fallback provider 仅属于内部连续性实现：API 响应的 `model` 与助手自我身份使用客户端选择的 Claude 型号，Messages `id` 由本地重新生成，不回显内部 provider、deployment 或路由信息；正常讨论各上游模型的业务内容不会被字符串替换。

部署变量见 `devops/deploy/.env.example`。Gemini 的 `AUTH_TOKEN` 填现有 LiteLLM 的 master token，而不是其 aicenter/Gemini 上游 key；默认 bridge 地址是宿主 `172.17.0.1:4000`。

所有真实 token 只能写入不入库的 `devops/deploy/.env`。不要把 token 写进前端、README、slot JSON 或管理端 CRUD；管理接口和 UI 只应显示优先级、配置状态或 env 变量名，绝不返回密钥值。修改 `.env` 后重建 backend 使配置生效：

```bash
cd devops/deploy
docker compose up -d --force-recreate backend
```

## License

TBD
