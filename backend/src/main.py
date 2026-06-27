# -*- coding: utf-8 -*-
"""
应用入口：
    uvicorn main:app --host 0.0.0.0 --port 7999 --reload
或:
    ./startup-local.sh
"""
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# 先加载 .env（已存在的环境变量不会被覆盖），再 import 任何用到 settings 的模块
load_dotenv(override=False)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from config.version import APP_NAME
from controller import example, health
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

    log.info("startup: ready")
    yield

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
    "http://localhost:5173",
    "http://127.0.0.1:5173",
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
