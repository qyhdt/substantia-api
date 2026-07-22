# -*- coding: utf-8 -*-
"""墨行供应商资金、商务价与请求级成本对账。"""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

from services.apikey import fx
from utils import db as db_util

SUPPLIER = "moxing"


def canonical_model(model: str | None) -> str:
    name = (model or "").strip().lower()
    if name.startswith("glm-5.2"):
        return "glm-5.2"
    if name.startswith("kimi-k3"):
        return "kimi-k3"
    return name


def is_moxing_slot(slot_id: str | None) -> bool:
    return "moxing" in (slot_id or "").strip().lower()


async def is_managed_model(model: str | None) -> bool:
    row = await db_util.fetchval(
        "SELECT 1 FROM ak_supplier_model_terms WHERE supplier=$1 AND model=$2",
        SUPPLIER, canonical_model(model),
    )
    return bool(row)


def _scaled(value: int, multiplier: Decimal | float | str) -> int:
    return int((Decimal(value) * Decimal(str(multiplier))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _component_cost(tokens: int, price_micro_usd_per_1k: int) -> int:
    return int(math.ceil((tokens or 0) / 1000 * int(price_micro_usd_per_1k or 0)))


def usage_cost(term: Dict[str, Any], prompt_tokens: int, completion_tokens: int,
               cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> tuple[int, int]:
    """返回 (官网成本, 商务折后成本)，均为 micro-USD。"""
    official = (
        _component_cost(prompt_tokens, term["official_input_micro_usd_per_1k"])
        + _component_cost(completion_tokens, term["official_output_micro_usd_per_1k"])
        + _component_cost(cache_read_tokens, term["official_cache_read_micro_usd_per_1k"])
        + _component_cost(cache_write_tokens, term["official_cache_write_micro_usd_per_1k"])
    )
    return official, _scaled(official, term["supplier_multiplier"])


async def record_usage(
    conn,
    *,
    usage_log_id: int,
    slot_id: str | None,
    public_model: str,
    upstream_model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    request_id: str | None,
    user_multiplier: float,
    request_status: str,
) -> Optional[Dict[str, Any]]:
    """在客户扣费同一事务内记墨行成本并扣供应商余额；非墨行 slot 直接返回。"""
    if not is_moxing_slot(slot_id):
        return None
    total_accounted_tokens = sum((prompt_tokens or 0, completion_tokens or 0,
                                  cache_read_tokens or 0, cache_write_tokens or 0))
    if request_status != "ok" and total_accounted_tokens == 0:
        return None

    actual_model = canonical_model(upstream_model or public_model)
    term_row = await conn.fetchrow(
        "SELECT * FROM ak_supplier_model_terms WHERE supplier = $1 AND model = $2",
        SUPPLIER, actual_model,
    )
    term = dict(term_row) if term_row else None
    official_cost = supplier_cost = 0
    status = "unpriced"
    supplier_multiplier = None
    if term:
        official_cost, supplier_cost = usage_cost(
            term, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens,
        )
        supplier_multiplier = term["supplier_multiplier"]
        status = "posted" if total_accounted_tokens > 0 else "missing_usage"

    sale_row = await conn.fetchrow(
        "SELECT sale_multiplier FROM ak_supplier_model_terms WHERE supplier = $1 AND model = $2",
        SUPPLIER, canonical_model(public_model),
    )
    sale_multiplier = sale_row["sale_multiplier"] if sale_row else None

    await conn.execute(
        "UPDATE ak_usage_logs SET supplier = $1, upstream_model = $2, "
        "official_cost_micro_usd = $3, supplier_cost_micro_usd = $4, "
        "supplier_multiplier = $5, sale_multiplier = $6, user_multiplier = $7, "
        "supplier_accounting_status = $8 WHERE id = $9",
        SUPPLIER, actual_model, official_cost, supplier_cost,
        supplier_multiplier, sale_multiplier, Decimal(str(user_multiplier)), status, usage_log_id,
    )
    await conn.execute(
        "INSERT INTO ak_supplier_accounts (supplier) VALUES ($1) ON CONFLICT (supplier) DO NOTHING",
        SUPPLIER,
    )
    account = await conn.fetchrow(
        "SELECT balance_micro_usd FROM ak_supplier_accounts WHERE supplier = $1 FOR UPDATE",
        SUPPLIER,
    )
    balance_after = int(account["balance_micro_usd"] or 0) - supplier_cost
    await conn.execute(
        "UPDATE ak_supplier_accounts SET balance_micro_usd = $1, updated_at = now() WHERE supplier = $2",
        balance_after, SUPPLIER,
    )
    await conn.execute(
        "INSERT INTO ak_supplier_ledger "
        "(supplier, entry_type, amount_micro_usd, balance_after_micro_usd, usage_log_id, "
        " model, request_id, note) VALUES ($1, 'usage', $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (usage_log_id) DO NOTHING",
        SUPPLIER, -supplier_cost, balance_after, usage_log_id, actual_model, request_id,
        None if term else "unpriced upstream model; cost requires manual term correction",
    )
    return {
        "supplier": SUPPLIER,
        "upstream_model": actual_model,
        "official_cost_micro_usd": official_cost,
        "supplier_cost_micro_usd": supplier_cost,
        "supplier_balance_after_micro_usd": balance_after,
        "supplier_accounting_status": status,
    }


async def _money_to_micro(amount: Decimal, currency: str) -> tuple[int, Decimal]:
    code = currency.strip().upper()
    if code not in {"USD", "RMB"}:
        raise ValueError("currency must be USD or RMB")
    if code == "USD":
        rate = Decimal("1")
        usd = amount
    else:
        exchange = await fx.current_usd_cny()
        rate = Decimal(str(exchange["rate"]))
        usd = amount / rate
    micro = int((usd * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return micro, rate


async def add_funds(*, amount: Decimal, currency: str, entry_type: str,
                    admin_id: int, reference: str | None = None, note: str | None = None) -> Dict[str, Any]:
    if entry_type not in {"topup", "adjustment"}:
        raise ValueError("bad entry type")
    if entry_type == "topup" and amount <= 0:
        raise ValueError("topup amount must be positive")
    if amount == 0:
        raise ValueError("amount must not be zero")
    micro, rate = await _money_to_micro(amount, currency)
    async with db_util.transaction() as conn:
        await conn.execute(
            "INSERT INTO ak_supplier_accounts (supplier) VALUES ($1) ON CONFLICT (supplier) DO NOTHING",
            SUPPLIER,
        )
        account = await conn.fetchrow(
            "SELECT balance_micro_usd FROM ak_supplier_accounts WHERE supplier = $1 FOR UPDATE",
            SUPPLIER,
        )
        balance_after = int(account["balance_micro_usd"] or 0) + micro
        await conn.execute(
            "UPDATE ak_supplier_accounts SET balance_micro_usd = $1, updated_at = now() WHERE supplier = $2",
            balance_after, SUPPLIER,
        )
        row = await conn.fetchrow(
            "INSERT INTO ak_supplier_ledger "
            "(supplier, entry_type, amount_micro_usd, balance_after_micro_usd, original_amount, "
            " original_currency, fx_rate, reference, note, created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *",
            SUPPLIER, entry_type, micro, balance_after, amount, currency.upper(), rate,
            reference, note, admin_id,
        )
    return dict(row)


async def add_balance_snapshot(*, amount: Decimal, currency: str, admin_id: int,
                               as_of: datetime | None = None, note: str | None = None) -> Dict[str, Any]:
    if amount < 0:
        raise ValueError("reported balance must not be negative")
    micro, rate = await _money_to_micro(amount, currency)
    row = await db_util.fetchrow(
        "INSERT INTO ak_supplier_balance_snapshots "
        "(supplier, reported_balance_micro_usd, original_amount, original_currency, fx_rate, "
        " as_of, note, created_by) VALUES ($1,$2,$3,$4,$5,coalesce($6, now()),$7,$8) RETURNING *",
        SUPPLIER, micro, amount, currency.upper(), rate, as_of, note, admin_id,
    )
    return dict(row)


def _per_million_to_micro_per_1k(value: Decimal) -> int:
    if value < 0:
        raise ValueError("official price must not be negative")
    return int((value * Decimal(1000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def update_terms(*, model: str, display_name: str | None,
                       official_input: Decimal, official_output: Decimal,
                       official_cache_read: Decimal, official_cache_write: Decimal,
                       supplier_multiplier: Decimal, sale_multiplier: Decimal,
                       admin_id: int) -> Dict[str, Any]:
    name = canonical_model(model)
    if name not in {"glm-5.2", "kimi-k3"}:
        raise ValueError("unsupported moxing model")
    if not (Decimal("0") <= supplier_multiplier <= Decimal("100")):
        raise ValueError("supplier multiplier out of range")
    if not (Decimal("0") <= sale_multiplier <= Decimal("100")):
        raise ValueError("sale multiplier out of range")
    prices = [
        _per_million_to_micro_per_1k(official_input),
        _per_million_to_micro_per_1k(official_output),
        _per_million_to_micro_per_1k(official_cache_read),
        _per_million_to_micro_per_1k(official_cache_write),
    ]
    sale_prices = [_scaled(value, sale_multiplier) for value in prices]
    async with db_util.transaction() as conn:
        row = await conn.fetchrow(
            "INSERT INTO ak_supplier_model_terms "
            "(supplier, model, display_name, official_input_micro_usd_per_1k, "
            " official_output_micro_usd_per_1k, official_cache_read_micro_usd_per_1k, "
            " official_cache_write_micro_usd_per_1k, supplier_multiplier, sale_multiplier, updated_by, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,now()) "
            "ON CONFLICT (supplier, model) DO UPDATE SET display_name=EXCLUDED.display_name, "
            " official_input_micro_usd_per_1k=EXCLUDED.official_input_micro_usd_per_1k, "
            " official_output_micro_usd_per_1k=EXCLUDED.official_output_micro_usd_per_1k, "
            " official_cache_read_micro_usd_per_1k=EXCLUDED.official_cache_read_micro_usd_per_1k, "
            " official_cache_write_micro_usd_per_1k=EXCLUDED.official_cache_write_micro_usd_per_1k, "
            " supplier_multiplier=EXCLUDED.supplier_multiplier, sale_multiplier=EXCLUDED.sale_multiplier, "
            " updated_by=EXCLUDED.updated_by, updated_at=now() RETURNING *",
            SUPPLIER, name, display_name, *prices, supplier_multiplier, sale_multiplier, admin_id,
        )
        await conn.execute(
            "INSERT INTO ak_model_prices "
            "(model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k, "
            " cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k, enabled, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,true,now()) ON CONFLICT (model) DO UPDATE SET "
            " display_name=EXCLUDED.display_name, input_micro_usd_per_1k=EXCLUDED.input_micro_usd_per_1k, "
            " output_micro_usd_per_1k=EXCLUDED.output_micro_usd_per_1k, "
            " cache_read_micro_usd_per_1k=EXCLUDED.cache_read_micro_usd_per_1k, "
            " cache_write_micro_usd_per_1k=EXCLUDED.cache_write_micro_usd_per_1k, updated_at=now()",
            name, display_name, *sale_prices,
        )
    return dict(row)


async def accounting_summary(days: int = 30, limit: int = 100) -> Dict[str, Any]:
    days = max(1, min(int(days), 3650))
    limit = max(1, min(int(limit), 500))
    account = await db_util.fetchrow(
        "SELECT * FROM ak_supplier_accounts WHERE supplier = $1", SUPPLIER,
    )
    terms = await db_util.fetch(
        "SELECT * FROM ak_supplier_model_terms WHERE supplier = $1 ORDER BY model", SUPPLIER,
    )
    period = await db_util.fetchrow(
        "SELECT count(*) AS calls, coalesce(sum(total_tokens),0) AS tokens, "
        "coalesce(sum(cost_micro_usd),0) AS sales, "
        "coalesce(sum(supplier_cost_micro_usd),0) AS supplier_cost, "
        "coalesce(sum(charged_paid_micro_usd),0) AS paid_sales, "
        "coalesce(sum(charged_trial_micro_usd),0) AS trial_sales, "
        "count(*) FILTER (WHERE supplier_accounting_status IS DISTINCT FROM 'posted') AS accounting_issue_calls "
        "FROM ak_usage_logs WHERE supplier = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day')",
        SUPPLIER, days,
    )
    ledger_totals = await db_util.fetchrow(
        "SELECT coalesce(sum(amount_micro_usd) FILTER (WHERE entry_type='topup'),0) AS topups, "
        "coalesce(sum(amount_micro_usd) FILTER (WHERE entry_type='adjustment'),0) AS adjustments, "
        "coalesce(sum(-amount_micro_usd) FILTER (WHERE entry_type='usage'),0) AS usage_cost, "
        "coalesce(sum(amount_micro_usd),0) AS ledger_balance "
        "FROM ak_supplier_ledger WHERE supplier = $1",
        SUPPLIER,
    )
    daily = await db_util.fetch(
        "SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS calls, "
        "coalesce(sum(total_tokens),0) AS tokens, coalesce(sum(cost_micro_usd),0) AS sales, "
        "coalesce(sum(supplier_cost_micro_usd),0) AS supplier_cost, "
        "coalesce(sum(charged_paid_micro_usd),0) AS paid_sales, "
        "coalesce(sum(charged_trial_micro_usd),0) AS trial_sales "
        "FROM ak_usage_logs WHERE supplier = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY day ORDER BY day DESC",
        SUPPLIER, days,
    )
    by_model = await db_util.fetch(
        "SELECT model, upstream_model, count(*) AS calls, coalesce(sum(total_tokens),0) AS tokens, "
        "coalesce(sum(cost_micro_usd),0) AS sales, "
        "coalesce(sum(supplier_cost_micro_usd),0) AS supplier_cost "
        "FROM ak_usage_logs WHERE supplier = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY model, upstream_model ORDER BY supplier_cost DESC",
        SUPPLIER, days,
    )
    recent_usage = await db_util.fetch(
        "SELECT l.id, l.created_at, u.email, l.request_id, l.model, l.upstream_model, "
        "l.prompt_tokens, l.completion_tokens, l.total_tokens, l.cost_micro_usd AS sales, "
        "l.supplier_cost_micro_usd AS supplier_cost, l.supplier_multiplier, l.sale_multiplier, "
        "l.user_multiplier, l.charged_paid_micro_usd, l.charged_trial_micro_usd, "
        "l.supplier_accounting_status "
        "FROM ak_usage_logs l LEFT JOIN ak_users u ON u.id=l.user_id "
        "WHERE l.supplier=$1 ORDER BY l.created_at DESC LIMIT $2",
        SUPPLIER, limit,
    )
    ledger = await db_util.fetch(
        "SELECT * FROM ak_supplier_ledger WHERE supplier=$1 ORDER BY created_at DESC LIMIT $2",
        SUPPLIER, limit,
    )
    snapshot = await db_util.fetchrow(
        "SELECT * FROM ak_supplier_balance_snapshots WHERE supplier=$1 ORDER BY as_of DESC, id DESC LIMIT 1",
        SUPPLIER,
    )
    account_dict = dict(account) if account else {"supplier": SUPPLIER, "balance_micro_usd": 0}
    snapshot_dict = dict(snapshot) if snapshot else None
    if snapshot_dict:
        snapshot_dict["variance_micro_usd"] = (
            int(account_dict.get("balance_micro_usd") or 0)
            - int(snapshot_dict.get("reported_balance_micro_usd") or 0)
        )
    sales = int(period["sales"] or 0)
    supplier_cost = int(period["supplier_cost"] or 0)
    paid_sales = int(period["paid_sales"] or 0)
    totals = dict(ledger_totals)
    totals["internal_variance_micro_usd"] = (
        int(account_dict.get("balance_micro_usd") or 0) - int(totals.get("ledger_balance") or 0)
    )
    return {
        "days": days,
        "account": account_dict,
        "terms": [dict(row) for row in terms],
        "period": {
            **dict(period),
            "gross_profit_micro_usd": sales - supplier_cost,
            "cash_contribution_micro_usd": paid_sales - supplier_cost,
        },
        "ledger_totals": totals,
        "latest_snapshot": snapshot_dict,
        "daily": [dict(row) for row in daily],
        "by_model": [dict(row) for row in by_model],
        "recent_usage": [dict(row) for row in recent_usage],
        "ledger": [dict(row) for row in ledger],
    }
