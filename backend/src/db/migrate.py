# -*- coding: utf-8 -*-
"""
轻量级 SQL migration：扫 db/migrations/*.sql，按文件名排序在事务里执行已应用过的不再跑。

约定：
- 文件名 `NNNN_xxx.sql`（4 位数字递增）
- 不支持 down 迁移；写错就再加一份 `NNNN_fix_xxx.sql`
- 不支持参数化；纯 SQL
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Tuple

import asyncpg

from utils import db as db_util

log = logging.getLogger("vibe.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
FILENAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _discover() -> List[Tuple[str, Path]]:
    files: List[Tuple[str, Path]] = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = FILENAME_RE.match(p.name)
        if not m:
            log.warning("skip migration with non-conforming name: %s", p.name)
            continue
        files.append((m.group(1), p))
    return files


async def _applied_versions(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {r["version"] for r in rows}


async def run_migrations() -> None:
    """启动时调用一次。无 DATABASE_URL 时报错向上抛。"""
    pool = await db_util.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_LEDGER_DDL)
        applied = await _applied_versions(conn)

        for version, path in _discover():
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            log.info("applying migration %s (%s)", version, path.name)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES ($1)", version
                )
            log.info("migration %s applied", version)


if __name__ == "__main__":
    # 也支持手动跑：python -m db.migrate
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")
    asyncio.run(run_migrations())
