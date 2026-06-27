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

## License

TBD
