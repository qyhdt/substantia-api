# -*- coding: utf-8 -*-
"""
下游 sk-key 的签发 / 查询 / 状态管理。明文只在签发时返回一次（库里只存 hash）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg

from security.api_key_auth import generate_key
from utils import db as db_util


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏：剔除 key_hash，金额字段保留（前端自己换算）。"""
    d = dict(row)
    d.pop("key_hash", None)
    am = d.get("allowed_models")
    if isinstance(am, str):
        try:
            d["allowed_models"] = json.loads(am)
        except Exception:
            d["allowed_models"] = None
    return d


async def issue_key(
    user_id: int,
    *,
    name: str = "default",
    allowed_models: Optional[List[str]] = None,
    quota_cap_micro_usd: Optional[int] = None,
    rate_limit_rpm: Optional[int] = None,
    expires_at: Optional[datetime] = None,
    conn: Optional[asyncpg.Connection] = None,
) -> Dict[str, Any]:
    """签发一把新 key。返回 {plain, key}（plain 仅此一次）。可传 conn 复用事务。"""
    plain, prefix, key_hash = generate_key()
    am = json.dumps(allowed_models) if allowed_models else None
    sql = """
        INSERT INTO ak_api_keys
            (user_id, name, key_prefix, key_hash, key_plain, allowed_models,
             quota_cap_micro_usd, rate_limit_rpm, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9)
        RETURNING *
    """
    args = (user_id, name, prefix, key_hash, plain, am, quota_cap_micro_usd, rate_limit_rpm, expires_at)
    row = await (conn.fetchrow(sql, *args) if conn else db_util.fetchrow(sql, *args))
    return {"plain": plain, "key": _public(dict(row))}


async def list_keys(user_id: int) -> List[Dict[str, Any]]:
    rows = await db_util.fetch(
        "SELECT * FROM ak_api_keys WHERE user_id = $1 ORDER BY created_at DESC", user_id
    )
    return [_public(dict(r)) for r in rows]


async def get_key(key_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if user_id is not None:
        row = await db_util.fetchrow(
            "SELECT * FROM ak_api_keys WHERE id = $1 AND user_id = $2", key_id, user_id
        )
    else:
        row = await db_util.fetchrow("SELECT * FROM ak_api_keys WHERE id = $1", key_id)
    return _public(dict(row)) if row else None


async def delete_key(key_id: int, user_id: Optional[int] = None) -> bool:
    """彻底删除一把 key（本人）。usage 日志保留（按 api_key_id 留痕，无 FK）。"""
    if user_id is not None:
        res = await db_util.execute(
            "DELETE FROM ak_api_keys WHERE id = $1 AND user_id = $2", key_id, user_id
        )
    else:
        res = await db_util.execute("DELETE FROM ak_api_keys WHERE id = $1", key_id)
    return res.endswith("1")


async def set_status(key_id: int, status: str, user_id: Optional[int] = None) -> bool:
    """active | disabled | revoked。user_id 给定时限定本人（用户自助禁用）。"""
    if user_id is not None:
        res = await db_util.execute(
            "UPDATE ak_api_keys SET status = $1 WHERE id = $2 AND user_id = $3",
            status, key_id, user_id,
        )
    else:
        res = await db_util.execute(
            "UPDATE ak_api_keys SET status = $1 WHERE id = $2", status, key_id
        )
    return res.endswith("1")


async def update_key(
    key_id: int,
    *,
    name: Optional[str] = None,
    allowed_models: Optional[List[str]] = None,
    quota_cap_micro_usd: Optional[int] = None,
    rate_limit_rpm: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """admin 改 key 配置。仅更新给定字段。"""
    sets, args, i = [], [], 1
    if name is not None:
        sets.append(f"name = ${i}"); args.append(name); i += 1
    if allowed_models is not None:
        sets.append(f"allowed_models = ${i}::jsonb"); args.append(json.dumps(allowed_models)); i += 1
    if quota_cap_micro_usd is not None:
        sets.append(f"quota_cap_micro_usd = ${i}"); args.append(quota_cap_micro_usd); i += 1
    if rate_limit_rpm is not None:
        sets.append(f"rate_limit_rpm = ${i}"); args.append(rate_limit_rpm); i += 1
    if not sets:
        return await get_key(key_id)
    args.append(key_id)
    row = await db_util.fetchrow(
        f"UPDATE ak_api_keys SET {', '.join(sets)} WHERE id = ${i} RETURNING *", *args
    )
    return _public(dict(row)) if row else None
