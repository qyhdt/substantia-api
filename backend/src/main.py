# -*- coding: utf-8 -*-
"""
应用入口：
    uvicorn main:app --host 0.0.0.0 --port 9999 --reload
或:
    ./startup-local.sh
"""
import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# 先加载 .env（已存在的环境变量不会被覆盖），再 import 任何用到 settings 的模块
load_dotenv(override=False)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from config.version import APP_NAME
from controller import admin_apikey, auth, claude, codex, example, gateway, health, portal, public, uploads, webhooks
from frame.base_api_route import BaseAPIRoute
from frame.error_handler import register_exception_handlers
from utils.fastapi_request_context import RequestContextMiddleware
from utils.pm_logger import get_app_logger, setup_root_logging

log = get_app_logger()
# 让业务 logger 也写进持久化文件（app.log/error.log），不再只进 stdout
setup_root_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启停钩子：在这里初始化外部连接（Redis/DB），退出时关闭。"""
    log.info("startup: app booting...")

    # 上传目录（转账凭证）：容器挂载卷，启动时确保存在
    try:
        import os
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        log.info("startup: upload dir ready: %s", settings.UPLOAD_DIR)
    except Exception as e:
        log.warning("startup: upload dir mkdir failed: %s", e)

    # Postgres：配置了 DATABASE_URL 才跑 migrations（db/migrations/*.sql）
    if settings.DATABASE_URL:
        try:
            from db.migrate import run_migrations
            await run_migrations()
            log.info("startup: migrations ok")
        except Exception as e:
            log.error("startup: migrations failed: %s", e)
            raise

    # Redis 预热（配置了再连，连不上不致命）
    try:
        from utils import redis as redis_util
        await redis_util.get("__warmup__")
        log.info("startup: redis ok")
    except Exception as e:
        log.warning("startup: redis warmup failed (will connect on first use): %s", e)

    # Claude slot 容器：docker 可达且有 slot 时，拉起所有 enabled 容器 + 起健康探针（保活）
    probe_task = None
    if settings.CLAUDE_PROBE_ENABLED:
        try:
            from services.claude import docker_manager as _dm
            from services.claude import health as _health
            from services.claude.registry import get_router as _get_router
            if _get_router().all_slots() and await asyncio.to_thread(_dm.is_docker_reachable):
                results = await asyncio.to_thread(_dm.ensure_all_enabled)
                log.info("startup: claude slots ensured: %s", results)
                probe_task = asyncio.create_task(_health.probe_loop(), name="claude.probe")
            else:
                log.info("startup: claude slots skipped (no slot configured or docker unreachable)")
        except Exception as e:
            log.warning("startup: claude slots init failed: %s", e)

    log.info("startup: ready")
    yield

    # 停健康探针
    if probe_task is not None:
        probe_task.cancel()
        try:
            await probe_task
        except Exception:
            pass

    # 关闭外部连接
    try:
        from utils import redis as redis_util
        await redis_util.close_async_pool()
        log.info("shutdown: redis closed")
    except Exception as e:
        log.warning("shutdown: redis close failed: %s", e)

    try:
        from utils import db as db_util
        await db_util.close_pool()
        log.info("shutdown: db closed")
    except Exception as e:
        log.info("shutdown: db close skipped (%s)", e)


app = FastAPI(lifespan=lifespan, title=APP_NAME)

# ✅ CORS 必须最先注册
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:6337",
    "http://127.0.0.1:6337",
]
CORS_ORIGINS += settings.cors_origins_list

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Download-Filename", "X-Trace-Id", "X-Request-Id"],
)

app.router.route_class = BaseAPIRoute
app.add_middleware(RequestContextMiddleware)
register_exception_handlers(app)


@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok"}


# 路由：兼容旧路径（不带 /api）+ 统一挂在 /api 下（前端 vite proxy / nginx 都按 /api 反代）
app.include_router(health.router)
app.include_router(example.router)

app.include_router(health.router, prefix="/api")
app.include_router(example.router, prefix="/api")
app.include_router(claude.router, prefix="/api")
app.include_router(claude.admin_router, prefix="/api")
app.include_router(codex.admin_router, prefix="/api")   # /api/admin/codex/*（ChatGPT 订阅登录 + 门控）

# APIKey 分发系统（下游令牌 / 门户 / 管理 / 网关）
app.include_router(auth.router, prefix="/api")
app.include_router(public.router, prefix="/api")
app.include_router(portal.router, prefix="/api")
app.include_router(admin_apikey.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")   # /api/uploads/{name} 回读凭证
app.include_router(gateway.router, prefix="/api")   # /api/v1/messages
app.include_router(gateway.router)                  # /v1/messages（裸路径，给 SDK base_url 用）
app.include_router(webhooks.router, prefix="/api")  # /api/webhooks/polar
