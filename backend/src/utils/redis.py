# -*- coding: utf-8 -*-
"""
Redis 工具：异步连接池 + 常用 KV/List/Hash 封装。
通过 settings.REDIS_HOST/PORT/DB 等读取配置。

如果项目不需要 Redis，删除本文件并从 requirements.txt 移除 `redis` 即可。
"""
import asyncio
import logging
from typing import Any

try:
    from redis.asyncio import ConnectionPool, Redis
except ImportError:
    ConnectionPool = None  # type: ignore
    Redis = None  # type: ignore

from config.settings import settings

logger = logging.getLogger(__name__)

_pool: "ConnectionPool | None" = None
_client_async: "Redis | None" = None
_bg_write_sem: asyncio.Semaphore | None = None


def _get_redis_url() -> str:
    return f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"


def get_async_pool() -> "ConnectionPool":
    """获取异步连接池（懒创建，进程内单例）。"""
    global _pool
    if ConnectionPool is None or Redis is None:
        raise RuntimeError("请先安装 redis>=5.0: pip install redis")
    if _pool is None:
        _pool = ConnectionPool.from_url(
            _get_redis_url(),
            decode_responses=True,
            max_connections=settings.REDIS_POOL_MAX_CONNECTIONS,
        )
    return _pool


def get_async_client() -> "Redis":
    """获取异步 Redis 客户端（复用连接池，协程安全）。"""
    global _client_async
    if _client_async is None:
        _client_async = Redis(connection_pool=get_async_pool())
    return _client_async


def get_bg_write_sem() -> asyncio.Semaphore:
    """后台写信号量：限制并发，避免连接池被打穿。"""
    global _bg_write_sem
    if _bg_write_sem is None:
        _bg_write_sem = asyncio.Semaphore(settings.REDIS_BG_WRITE_CONCURRENCY)
    return _bg_write_sem


async def run_bg_write(coro, *, label: str = "bg_write") -> None:
    """fire-and-forget 写任务包装：捕获异常，不上抛。"""
    sem = get_bg_write_sem()
    async with sem:
        try:
            await coro
        except Exception as exc:
            logger.warning("[%s] 后台写任务失败（已忽略）: %s: %s", label, type(exc).__name__, exc)


async def close_async_pool() -> None:
    """应用退出时调用（如 FastAPI lifespan）。"""
    global _client_async, _pool
    if _client_async is not None:
        await _client_async.aclose()
        _client_async = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# ---------- KV ----------


async def get(key: str, client: Any = None) -> str | None:
    c = client or get_async_client()
    return await c.get(key)


async def set_(key: str, value: str | bytes, ex: int | None = None, client: Any = None) -> None:
    c = client or get_async_client()
    await c.set(key, value, ex=ex)


async def delete(key: str, client: Any = None) -> int:
    c = client or get_async_client()
    return await c.delete(key)


async def exists(key: str, client: Any = None) -> bool:
    c = client or get_async_client()
    return bool(await c.exists(key))


async def setex(key: str, seconds: int, value: str | bytes, client: Any = None) -> None:
    c = client or get_async_client()
    await c.setex(key, seconds, value)


async def expire(key: str, seconds: int, client: Any = None) -> bool:
    c = client or get_async_client()
    return await c.expire(key, seconds)


# ---------- List ----------


async def lpush(key: str, *values: Any, client: Any = None) -> int:
    c = client or get_async_client()
    return await c.lpush(key, *values)


async def rpush(key: str, *values: Any, client: Any = None) -> int:
    c = client or get_async_client()
    return await c.rpush(key, *values)


async def lrange(key: str, start: int, end: int = -1, client: Any = None) -> list:
    c = client or get_async_client()
    out = await c.lrange(key, start, end)
    return list(out) if out else []


async def llen(key: str, client: Any = None) -> int:
    c = client or get_async_client()
    return await c.llen(key)


# ---------- Hash ----------


async def hset(key: str, field: str, value: str | bytes, client: Any = None) -> int:
    c = client or get_async_client()
    return await c.hset(key, field, value)


async def hget(key: str, field: str, client: Any = None) -> str | None:
    c = client or get_async_client()
    return await c.hget(key, field)


async def hgetall(key: str, client: Any = None) -> dict:
    c = client or get_async_client()
    out = await c.hgetall(key)
    return dict(out) if out else {}


async def hdel(key: str, *fields: str, client: Any = None) -> int:
    c = client or get_async_client()
    return await c.hdel(key, *fields)
