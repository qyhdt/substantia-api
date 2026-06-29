# -*- coding: utf-8 -*-
"""
计费与用量落库：请求前置校验（余额/key 封顶/模型白名单），请求后扣费 + 记日志。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from config.settings import settings
from services.apikey import pricing
from utils import db as db_util
from utils.pm_logger import get_app_logger

log = get_app_logger()


def precheck(key: Dict[str, Any], user: Dict[str, Any], model: str) -> None:
    """网关请求前置校验：有效余额>0、key 未超封顶、模型在白名单。失败抛 402/403。
    user 为含 balance/trial 字段的 dict；有效余额 = 实付 + 有效试用。"""
    from services.apikey.balance import effective_balance
    if settings.AK_ENFORCE_BALANCE and effective_balance(user) <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient balance. Top up at {settings.RECHARGE_URL}",
        )

    cap = key.get("quota_cap_micro_usd")
    if cap is not None and (key.get("spent_micro_usd") or 0) >= cap:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="key quota exhausted")

    allowed = key.get("allowed_models")
    if isinstance(allowed, str):
        try:
            allowed = json.loads(allowed)
        except Exception:
            allowed = None
    if allowed:  # 非空才限制
        if model not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"model {model} not allowed")


async def record_and_charge(
    *,
    api_key_id: int,
    user_id: int,
    slot_id: Optional[str],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    attempts: int = 1,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    status_str: str = "ok",
    error_code: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """算成本 → 扣用户余额 + 累加 key 花费 + 写 usage 日志（一个事务）。返回 {cost_micro_usd, ...}。

    缓存 token 单独按官方折扣计价（read 10% / write 125%）。total_tokens 计入全部
    （新输入 + 缓存读 + 缓存写 + 输出），便于用量展示；计价按各自单价。"""
    cache_read_tokens = cache_read_tokens or 0
    cache_write_tokens = cache_write_tokens or 0
    total_tokens = (prompt_tokens or 0) + (completion_tokens or 0) + cache_read_tokens + cache_write_tokens
    cost = await pricing.compute_cost_micro_usd(
        model, prompt_tokens, completion_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
    )

    async with db_util.transaction() as conn:
        if cost > 0:
            # 先扣试用桶（有效时），再扣实付桶。行级锁防并发扣费。
            from services.apikey.balance import trial_active
            urow = await conn.fetchrow(
                "SELECT balance_micro_usd, trial_micro_usd, trial_expires_at, trial_permanent "
                "FROM ak_users WHERE id = $1 FOR UPDATE",
                user_id,
            )
            avail_trial = int(urow["trial_micro_usd"]) if (urow and trial_active(dict(urow))) else 0
            from_trial = min(cost, avail_trial)
            from_paid = cost - from_trial
            await conn.execute(
                "UPDATE ak_users SET trial_micro_usd = trial_micro_usd - $1, "
                "balance_micro_usd = balance_micro_usd - $2 WHERE id = $3",
                from_trial, from_paid, user_id,
            )
            await conn.execute(
                "UPDATE ak_api_keys SET spent_micro_usd = spent_micro_usd + $1, last_used_at = now() "
                "WHERE id = $2",
                cost, api_key_id,
            )
        else:
            await conn.execute(
                "UPDATE ak_api_keys SET last_used_at = now() WHERE id = $1", api_key_id
            )
        await conn.execute(
            """
            INSERT INTO ak_usage_logs
                (api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens,
                 total_tokens, cost_micro_usd, latency_ms, attempts, status, error_code, request_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """,
            api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens,
            total_tokens, cost, latency_ms, attempts, status_str, error_code, request_id,
        )
    return {
        "cost_micro_usd": cost,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


async def usage_for_key(api_key_id: int, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    rows = await db_util.fetch(
        "SELECT * FROM ak_usage_logs WHERE api_key_id = $1 AND user_id = $2 "
        "ORDER BY created_at DESC LIMIT $3",
        api_key_id, user_id, limit,
    )
    return [dict(r) for r in rows]


async def usage_for_user(user_id: int, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """分页：返回 {items, total}。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total = await db_util.fetchval("SELECT count(*) FROM ak_usage_logs WHERE user_id = $1", user_id)
    rows = await db_util.fetch(
        "SELECT * FROM ak_usage_logs WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        user_id, limit, offset,
    )
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


async def user_spend_summary(user_id: int) -> Dict[str, Any]:
    """单用户消费聚合：总花费/调用数/总 token + 按模型明细。供 admin 用户详情用。"""
    total = await db_util.fetchrow(
        "SELECT count(*) AS calls, "
        "coalesce(sum(total_tokens), 0) AS tokens, "
        "coalesce(sum(cost_micro_usd), 0) AS cost "
        "FROM ak_usage_logs WHERE user_id = $1",
        user_id,
    )
    by_model = await db_util.fetch(
        "SELECT model, count(*) AS calls, "
        "coalesce(sum(prompt_tokens), 0) AS prompt_tokens, "
        "coalesce(sum(completion_tokens), 0) AS completion_tokens, "
        "coalesce(sum(total_tokens), 0) AS tokens, "
        "coalesce(sum(cost_micro_usd), 0) AS cost "
        "FROM ak_usage_logs WHERE user_id = $1 "
        "GROUP BY model ORDER BY cost DESC NULLS LAST",
        user_id,
    )
    return {
        "total_calls": int(total["calls"] or 0),
        "total_tokens": int(total["tokens"] or 0),
        "total_cost_micro_usd": int(total["cost"] or 0),
        "by_model": [dict(r) for r in by_model],
    }


async def admin_summary(limit: int = 500) -> Dict[str, Any]:
    """看板聚合：按 user / model / slot 汇总 token + 花费。"""
    by_model = await db_util.fetch(
        "SELECT model, count(*) AS calls, sum(total_tokens) AS tokens, sum(cost_micro_usd) AS cost "
        "FROM ak_usage_logs GROUP BY model ORDER BY cost DESC NULLS LAST"
    )
    by_user = await db_util.fetch(
        "SELECT u.email, count(*) AS calls, sum(l.total_tokens) AS tokens, sum(l.cost_micro_usd) AS cost "
        "FROM ak_usage_logs l JOIN ak_users u ON u.id = l.user_id "
        "GROUP BY u.email ORDER BY cost DESC NULLS LAST LIMIT $1",
        limit,
    )
    by_slot = await db_util.fetch(
        "SELECT slot_id, count(*) AS calls, sum(total_tokens) AS tokens, sum(cost_micro_usd) AS cost "
        "FROM ak_usage_logs GROUP BY slot_id ORDER BY cost DESC NULLS LAST"
    )
    return {
        "by_model": [dict(r) for r in by_model],
        "by_user": [dict(r) for r in by_user],
        "by_slot": [dict(r) for r in by_slot],
    }
