# -*- coding: utf-8 -*-
"""
PostgreSQL 连接池（asyncpg）。

设计要点：
- 按事件循环隔离 pool，避免跨 loop 复用（asyncpg 不允许）
- 通过 settings.DATABASE_URL 取连接串
- 暴露 init / close / fetch / fetchrow / execute / transaction 6 个常用入口

如果项目不需要 PostgreSQL，删除本文件并从 requirements.txt 移除 `asyncpg` 即可。
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import asyncpg

from config.settings import settings

_pools: Dict[asyncio.AbstractEventLoop, asyncpg.Pool] = {}


def _get_database_url() -> str:
    url = (settings.DATABASE_URL or "").strip()
    if not url:
        raise ValueError("DATABASE_URL is not set")
    return url


async def get_pool() -> asyncpg.Pool:
    """按当前事件循环获取连接池（懒创建）。"""
    loop = asyncio.get_running_loop()
    if loop not in _pools:
        _pools[loop] = await asyncpg.create_pool(
            _get_database_url(),
            min_size=settings.DB_POOL_MIN_SIZE,
            max_size=settings.DB_POOL_MAX_SIZE,
            command_timeout=settings.DB_COMMAND_TIMEOUT,
        )
    return _pools[loop]


async def close_pool() -> None:
    """关闭当前事件循环对应的连接池（FastAPI lifespan 退出时调用）。"""
    loop = asyncio.get_running_loop()
    pool = _pools.pop(loop, None)
    if pool is not None:
        await pool.close()


async def fetch(query: str, *args: Any) -> List[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> Optional[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


@asynccontextmanager
async def transaction():
    """
    用法：
        async with transaction() as conn:
            await conn.execute("...")
            await conn.execute("...")
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
