# -*- coding: utf-8 -*-
"""
计费与用量落库：请求前置校验（余额/key 封顶/模型白名单），请求后扣费 + 记日志。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from config.settings import settings
from services.apikey import pricing
from utils import db as db_util
from utils.pm_logger import get_app_logger

log = get_app_logger()

_CHINA_MODEL_PREFIXES = (
    "glm", "kimi", "qwen", "deepseek", "doubao", "ernie", "baichuan",
)


def is_china_model(model: str | None) -> bool:
    """账单展示币种归类；实际扣费仍统一使用 micro-USD。"""
    name = (model or "").strip().lower()
    return any(name.startswith(prefix) for prefix in _CHINA_MODEL_PREFIXES)


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
    upstream_model: Optional[str] = None,
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
    from services.apikey import moxing_accounting
    billing_fx_rate = None
    if moxing_accounting.is_moxing_slot(slot_id):
        from services.apikey import fx
        billing_fx_rate = (await fx.current_usd_cny())["rate"]
    # 应用用户价格系数：实扣 = 模型价 × 系数（1.0=原价 / 0.5=五折 / 1.3=上浮）。
    # round() 为 round-half-to-even，与 Go pyRound 一致。
    mult = 1.0
    if cost > 0:
        from services.apikey.users import user_price_multiplier
        mult = await user_price_multiplier(user_id)
        if mult != 1.0:
            cost = round(cost * mult)
            if cost < 0:
                cost = 0

    from_trial = from_paid = 0
    supplier_entry = None
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
        usage_row = await conn.fetchrow(
            """
            INSERT INTO ak_usage_logs
                (api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens,
                 cache_read_tokens, cache_write_tokens, total_tokens, cost_micro_usd, latency_ms,
                 attempts, status, error_code, request_id, user_multiplier,
                 charged_paid_micro_usd, charged_trial_micro_usd)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
            RETURNING id
            """,
            api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens,
            cache_read_tokens, cache_write_tokens, total_tokens, cost, latency_ms,
            attempts, status_str, error_code, request_id,
            mult, from_paid, from_trial,
        )
        supplier_entry = await moxing_accounting.record_usage(
            conn,
            usage_log_id=int(usage_row["id"]),
            slot_id=slot_id,
            public_model=model,
            upstream_model=upstream_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            request_id=request_id,
            user_multiplier=mult,
            request_status=status_str,
            customer_cost_micro_usd=cost,
            charged_paid_micro_usd=from_paid,
            charged_trial_micro_usd=from_trial,
            billing_fx_rate=billing_fx_rate or 6.7648,
        )
    return {
        "cost_micro_usd": cost,
        "total_tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "charged_paid_micro_usd": from_paid,
        "charged_trial_micro_usd": from_trial,
        **(supplier_entry or {}),
    }


async def usage_for_key(api_key_id: int, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    rows = await db_util.fetch(
        "SELECT id, api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens, "
        "cache_read_tokens, cache_write_tokens, "
        "total_tokens, cost_micro_usd, latency_ms, attempts, status, error_code, request_id, created_at "
        "FROM ak_usage_logs WHERE api_key_id = $1 AND user_id = $2 "
        "ORDER BY created_at DESC LIMIT $3",
        api_key_id, user_id, limit,
    )
    return [dict(r) for r in rows]


async def usage_for_user(
    user_id: int, limit: int = 50, offset: int = 0, days: int | None = None,
) -> Dict[str, Any]:
    """分页：返回 {items, total}。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    if days is not None:
        days = max(1, min(int(days), 365))
        total = await db_util.fetchval(
            "SELECT count(*) FROM ak_usage_logs WHERE user_id = $1 "
            "AND created_at >= now() - ($2::int * interval '1 day')",
            user_id, days,
        )
        rows = await db_util.fetch(
            "SELECT id, api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens, "
            "cache_read_tokens, cache_write_tokens, "
            "total_tokens, cost_micro_usd, latency_ms, attempts, status, error_code, request_id, created_at "
            "FROM ak_usage_logs WHERE user_id = $1 "
            "AND created_at >= now() - ($2::int * interval '1 day') "
            "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
            user_id, days, limit, offset,
        )
    else:
        total = await db_util.fetchval("SELECT count(*) FROM ak_usage_logs WHERE user_id = $1", user_id)
        rows = await db_util.fetch(
            "SELECT id, api_key_id, user_id, slot_id, model, prompt_tokens, completion_tokens, "
            "cache_read_tokens, cache_write_tokens, "
            "total_tokens, cost_micro_usd, latency_ms, attempts, status, error_code, request_id, created_at "
            "FROM ak_usage_logs WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            user_id, limit, offset,
        )
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


async def billing_summary(user_id: int, days: int = 7) -> Dict[str, Any]:
    """用户账单聚合：总览、按日、按模型与按渠道。"""
    days = max(1, min(int(days), 365))
    china = "lower(model) ~ '^(glm|kimi|qwen|deepseek|doubao|ernie|baichuan)'"
    total = await db_util.fetchrow(
        "SELECT count(*) AS calls, coalesce(sum(total_tokens), 0) AS tokens, "
        f"coalesce(sum(cost_micro_usd) FILTER (WHERE {china}), 0) AS china_cost, "
        f"coalesce(sum(cost_micro_usd) FILTER (WHERE NOT ({china})), 0) AS overseas_cost "
        "FROM ak_usage_logs WHERE user_id = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day')",
        user_id, days,
    )
    daily = await db_util.fetch(
        "SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS calls, "
        "coalesce(sum(total_tokens), 0) AS tokens, "
        f"coalesce(sum(cost_micro_usd) FILTER (WHERE {china}), 0) AS china_cost, "
        f"coalesce(sum(cost_micro_usd) FILTER (WHERE NOT ({china})), 0) AS overseas_cost "
        "FROM ak_usage_logs WHERE user_id = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY day ORDER BY day DESC",
        user_id, days,
    )
    by_model = await db_util.fetch(
        "SELECT model, count(*) AS calls, coalesce(sum(prompt_tokens), 0) AS prompt_tokens, "
        "coalesce(sum(completion_tokens), 0) AS completion_tokens, "
        "coalesce(sum(total_tokens), 0) AS tokens, coalesce(sum(cost_micro_usd), 0) AS cost "
        "FROM ak_usage_logs WHERE user_id = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY model ORDER BY cost DESC NULLS LAST",
        user_id, days,
    )
    by_slot = await db_util.fetch(
        "SELECT slot_id, count(*) AS calls, coalesce(sum(prompt_tokens), 0) AS prompt_tokens, "
        "coalesce(sum(completion_tokens), 0) AS completion_tokens, "
        "coalesce(sum(total_tokens), 0) AS tokens, coalesce(sum(cost_micro_usd), 0) AS cost "
        "FROM ak_usage_logs WHERE user_id = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY slot_id ORDER BY cost DESC NULLS LAST",
        user_id, days,
    )
    return {
        "days": days,
        "total_calls": int(total["calls"] or 0),
        "total_tokens": int(total["tokens"] or 0),
        "china_cost_micro_usd": int(total["china_cost"] or 0),
        "overseas_cost_micro_usd": int(total["overseas_cost"] or 0),
        "daily": [dict(row) for row in daily],
        "by_model": [
            {**dict(row), "currency": "cny" if is_china_model(row["model"]) else "usd"}
            for row in by_model
        ],
        "by_slot": [dict(row) for row in by_slot],
    }


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


async def admin_usage_details(*, email: str | None = None, start_date=None, end_date=None,
                              limit: int = 50, offset: int = 0, export: bool = False) -> Dict[str, Any]:
    """全站调用明细；邮箱模糊搜索，日期按北京时间闭区间筛选。"""
    limit = max(1, min(int(limit), 100_000 if export else 200))
    offset = max(0, int(offset))
    conditions: list[str] = []
    args: list[Any] = []

    def bind(value: Any) -> str:
        args.append(value)
        return f"${len(args)}"

    if email and email.strip():
        conditions.append(f"u.email ILIKE {bind('%' + email.strip() + '%')}")
    if start_date is not None:
        conditions.append(f"l.created_at >= ({bind(start_date)}::date::timestamp AT TIME ZONE 'Asia/Shanghai')")
    if end_date is not None:
        conditions.append(
            f"l.created_at < (({bind(end_date)}::date + interval '1 day') AT TIME ZONE 'Asia/Shanghai')"
        )
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    total = await db_util.fetchval(
        f"SELECT count(*) FROM ak_usage_logs l JOIN ak_users u ON u.id=l.user_id {where}", *args,
    )
    limit_ref = bind(limit)
    offset_ref = bind(offset)
    rows = await db_util.fetch(
        "SELECT l.id, l.created_at, u.email, l.request_id, l.model, l.slot_id, "
        "l.prompt_tokens, l.completion_tokens, l.cache_read_tokens, l.cache_write_tokens, "
        "l.total_tokens, l.cost_micro_usd, l.status, l.error_code "
        f"FROM ak_usage_logs l JOIN ak_users u ON u.id=l.user_id {where} "
        f"ORDER BY l.created_at DESC LIMIT {limit_ref} OFFSET {offset_ref}",
        *args,
    )
    return {"items": [dict(row) for row in rows], "total": int(total or 0)}


async def admin_summary(limit: int = 500, days: int = 7,
                        start_date=None, end_date=None) -> Dict[str, Any]:
    """管理看板：北京时间日期区间、上期对比、趋势及各维度汇总。"""
    days = max(1, min(int(days), 3650))
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    end_date = end_date or today
    start_date = start_date or (end_date - timedelta(days=days - 1))
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    period_days = (end_date - start_date).days + 1
    if period_days > 3650:
        raise ValueError("date range must not exceed 3650 days")
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    where = (
        "created_at >= ($1::date::timestamp AT TIME ZONE 'Asia/Shanghai') AND "
        "created_at < (($2::date + interval '1 day') AT TIME ZONE 'Asia/Shanghai')"
    )
    l_where = (
        "l.created_at >= ($1::date::timestamp AT TIME ZONE 'Asia/Shanghai') AND "
        "l.created_at < (($2::date + interval '1 day') AT TIME ZONE 'Asia/Shanghai')"
    )
    china = "lower(model) ~ '^(glm|kimi|qwen|deepseek|doubao|ernie|baichuan)'"
    total = await db_util.fetchrow(
        f"SELECT count(*) AS calls, coalesce(sum(total_tokens),0) AS tokens, "
        f"coalesce(sum(cost_micro_usd),0) AS cost FROM ak_usage_logs WHERE {where}",
        start_date, end_date,
    )
    previous_total = await db_util.fetchrow(
        f"SELECT count(*) AS calls, coalesce(sum(total_tokens),0) AS tokens, "
        f"coalesce(sum(cost_micro_usd),0) AS cost FROM ak_usage_logs WHERE {where}",
        previous_start, previous_end,
    )
    by_model = await db_util.fetch(
        "SELECT model, count(*) AS calls, sum(total_tokens) AS tokens, sum(cost_micro_usd) AS cost "
        f"FROM ak_usage_logs WHERE {where} "
        "GROUP BY model ORDER BY cost DESC NULLS LAST",
        start_date, end_date,
    )
    by_user = await db_util.fetch(
        "SELECT u.email, count(*) AS calls, sum(l.total_tokens) AS tokens, sum(l.cost_micro_usd) AS cost "
        "FROM ak_usage_logs l JOIN ak_users u ON u.id = l.user_id "
        f"WHERE {l_where} GROUP BY u.email ORDER BY cost DESC NULLS LAST LIMIT $3",
        start_date, end_date, limit,
    )
    by_slot = await db_util.fetch(
        "SELECT slot_id, count(*) AS calls, sum(total_tokens) AS tokens, sum(cost_micro_usd) AS cost "
        f"FROM ak_usage_logs WHERE {where} "
        "GROUP BY slot_id ORDER BY cost DESC NULLS LAST",
        start_date, end_date,
    )
    daily = await db_util.fetch(
        "SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS calls, "
        "coalesce(sum(total_tokens), 0) AS tokens, "
        f"coalesce(sum(cost_micro_usd) FILTER (WHERE {china}), 0) AS china_cost, "
        f"coalesce(sum(cost_micro_usd) FILTER (WHERE NOT ({china})), 0) AS overseas_cost "
        f"FROM ak_usage_logs WHERE {where} "
        "GROUP BY day ORDER BY day DESC",
        start_date, end_date,
    )
    daily_map = {row["day"]: dict(row) for row in daily}
    daily_filled = []
    previous_filled = []
    previous_daily = await db_util.fetch(
        "SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS calls, "
        "coalesce(sum(total_tokens),0) AS tokens, coalesce(sum(cost_micro_usd),0) AS cost "
        f"FROM ak_usage_logs WHERE {where} GROUP BY day ORDER BY day",
        previous_start, previous_end,
    )
    previous_map = {row["day"]: dict(row) for row in previous_daily}
    for offset in range(period_days):
        day = start_date + timedelta(days=offset)
        daily_filled.append(daily_map.get(day, {
            "day": day, "calls": 0, "tokens": 0, "china_cost": 0, "overseas_cost": 0,
        }))
        previous_day = previous_start + timedelta(days=offset)
        previous_filled.append(previous_map.get(previous_day, {
            "day": previous_day, "calls": 0, "tokens": 0, "cost": 0,
        }))
    return {
        "days": period_days,
        "start_date": start_date,
        "end_date": end_date,
        "previous_start_date": previous_start,
        "previous_end_date": previous_end,
        "total": dict(total),
        "previous_total": dict(previous_total),
        "daily": daily_filled,
        "previous_daily": previous_filled,
        "by_model": [dict(r) for r in by_model],
        "by_user": [dict(r) for r in by_user],
        "by_slot": [dict(r) for r in by_slot],
    }
