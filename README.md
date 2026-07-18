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

生产环境使用固定的三级顺序，不会在 Gemini 与 GLM 之间随机选路：

| 优先级 | 上游 | 配置 |
|---:|---|---|
| `0` | Claude subscription | admin 管理的健康订阅 slot；同优先级内按 RR/weighted-HRW 配置路由 |
| `100` | Gemini | 同机已有的 LiteLLM Anthropic bridge；`CLAUDE_FALLBACK_GEMINI_*` 三项齐全才启用 |
| `200` | GLM-5.2 | 智谱官方 Anthropic 兼容端点；`CLAUDE_FALLBACK_GLM_AUTH_TOKEN` 非空才启用 |

请求先走健康的 subscription；订阅出现鉴权失败、额度耗尽或其它可重试故障后转 Gemini，Gemini 仍不可用才转 GLM-5.2。即使命中 Gemini 或 GLM，计费仍以客户端原请求的 Claude 模型为准，不按实际 fallback 模型改价。

部署变量见 `devops/deploy/.env.example`。Gemini 的 `AUTH_TOKEN` 填现有 LiteLLM 的 master token，而不是其 aicenter/Gemini 上游 key；默认 bridge 地址是宿主 `172.17.0.1:4000`。GLM 默认直连 `https://open.bigmodel.cn/api/anthropic`，模型使用 `glm-5.2[1m]`（参见[智谱 Claude Code 接入文档](https://docs.bigmodel.cn/cn/guide/develop/claude)）。

所有真实 token 只能写入不入库的 `devops/deploy/.env`。不要把 token 写进前端、README、slot JSON 或管理端 CRUD；管理接口和 UI 只应显示优先级、配置状态或 env 变量名，绝不返回密钥值。修改 `.env` 后重建 backend 使配置生效：

```bash
cd devops/deploy
docker compose up -d --force-recreate backend
```

## License

TBD
