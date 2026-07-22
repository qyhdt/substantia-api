# -*- coding: utf-8 -*-
"""墨行供应商资金、商务价与请求级成本对账。"""
from __future__ import annotations

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


def _component_cost(tokens: int, price_micro_cny_per_million: int) -> int:
    """按墨行原生人民币/百万 token 价格，返回 micro-CNY。"""
    numerator = int(tokens or 0) * int(price_micro_cny_per_million or 0)
    return (numerator + 999_999) // 1_000_000


def usage_cost(term: Dict[str, Any], prompt_tokens: int, completion_tokens: int,
               cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> tuple[int, int]:
    """返回 (官网成本, 商务折后成本)，均为 micro-CNY。"""
    official = (
        _component_cost(prompt_tokens, term["official_input_micro_cny_per_million"])
        + _component_cost(completion_tokens, term["official_output_micro_cny_per_million"])
        + _component_cost(cache_read_tokens, term["official_cache_read_micro_cny_per_million"])
        + _component_cost(cache_write_tokens, term["official_cache_write_micro_cny_per_million"])
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
    customer_cost_micro_usd: int = 0,
    charged_paid_micro_usd: int = 0,
    charged_trial_micro_usd: int = 0,
    billing_fx_rate: Decimal | float | str = Decimal("6.7648"),
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

    rate = Decimal(str(billing_fx_rate))
    if rate <= 0:
        rate = Decimal("6.7648")
    supplier_cost_usd = _scaled(supplier_cost, Decimal("1") / rate)
    sales_cny = _scaled(customer_cost_micro_usd, rate)
    paid_cny = _scaled(charged_paid_micro_usd, rate)
    trial_cny = _scaled(charged_trial_micro_usd, rate)

    await conn.execute(
        "UPDATE ak_usage_logs SET supplier = $1, upstream_model = $2, "
        "official_cost_micro_usd = $3, supplier_cost_micro_usd = $4, "
        "official_cost_micro_cny = $5, supplier_cost_micro_cny = $6, sales_micro_cny = $7, "
        "charged_paid_micro_cny = $8, charged_trial_micro_cny = $9, billing_fx_rate = $10, "
        "supplier_multiplier = $11, sale_multiplier = $12, user_multiplier = $13, "
        "supplier_accounting_status = $14 WHERE id = $15",
        SUPPLIER, actual_model, _scaled(official_cost, Decimal("1") / rate), supplier_cost_usd,
        official_cost, supplier_cost, sales_cny, paid_cny, trial_cny, rate,
        supplier_multiplier, sale_multiplier, Decimal(str(user_multiplier)), status, usage_log_id,
    )
    await conn.execute(
        "INSERT INTO ak_supplier_accounts (supplier) VALUES ($1) ON CONFLICT (supplier) DO NOTHING",
        SUPPLIER,
    )
    account = await conn.fetchrow(
        "SELECT balance_micro_usd, balance_micro_cny FROM ak_supplier_accounts WHERE supplier = $1 FOR UPDATE",
        SUPPLIER,
    )
    balance_after = int(account["balance_micro_cny"] or 0) - supplier_cost
    balance_after_usd = int(account["balance_micro_usd"] or 0) - supplier_cost_usd
    await conn.execute(
        "UPDATE ak_supplier_accounts SET balance_micro_usd = $1, balance_micro_cny = $2, updated_at = now() WHERE supplier = $3",
        balance_after_usd, balance_after, SUPPLIER,
    )
    await conn.execute(
        "INSERT INTO ak_supplier_ledger "
        "(supplier, entry_type, amount_micro_usd, balance_after_micro_usd, amount_micro_cny, "
        " balance_after_micro_cny, usage_log_id, model, request_id, note) "
        "VALUES ($1, 'usage', $2, $3, $4, $5, $6, $7, $8, $9) "
        "ON CONFLICT (usage_log_id) DO NOTHING",
        SUPPLIER, -supplier_cost_usd, balance_after_usd, -supplier_cost, balance_after,
        usage_log_id, actual_model, request_id,
        None if term else "unpriced upstream model; cost requires manual term correction",
    )
    return {
        "supplier": SUPPLIER,
        "upstream_model": actual_model,
        "official_cost_micro_cny": official_cost,
        "supplier_cost_micro_cny": supplier_cost,
        "supplier_balance_after_micro_cny": balance_after,
        "supplier_accounting_status": status,
    }


async def _money_to_micro(amount: Decimal, currency: str) -> tuple[int, int, Decimal]:
    code = currency.strip().upper()
    if code not in {"USD", "RMB"}:
        raise ValueError("currency must be USD or RMB")
    exchange = await fx.current_usd_cny()
    rate = Decimal(str(exchange["rate"]))
    usd = amount if code == "USD" else amount / rate
    cny = amount * rate if code == "USD" else amount
    micro_usd = _scaled(1_000_000, usd)
    micro_cny = _scaled(1_000_000, cny)
    return micro_usd, micro_cny, rate


async def add_funds(*, amount: Decimal, currency: str, entry_type: str,
                    admin_id: int, reference: str | None = None, note: str | None = None) -> Dict[str, Any]:
    if entry_type not in {"topup", "adjustment"}:
        raise ValueError("bad entry type")
    if entry_type == "topup" and amount <= 0:
        raise ValueError("topup amount must be positive")
    if amount == 0:
        raise ValueError("amount must not be zero")
    micro_usd, micro_cny, rate = await _money_to_micro(amount, currency)
    async with db_util.transaction() as conn:
        await conn.execute(
            "INSERT INTO ak_supplier_accounts (supplier) VALUES ($1) ON CONFLICT (supplier) DO NOTHING",
            SUPPLIER,
        )
        account = await conn.fetchrow(
            "SELECT balance_micro_usd, balance_micro_cny FROM ak_supplier_accounts WHERE supplier = $1 FOR UPDATE",
            SUPPLIER,
        )
        balance_after_usd = int(account["balance_micro_usd"] or 0) + micro_usd
        balance_after_cny = int(account["balance_micro_cny"] or 0) + micro_cny
        await conn.execute(
            "UPDATE ak_supplier_accounts SET balance_micro_usd = $1, balance_micro_cny = $2, updated_at = now() WHERE supplier = $3",
            balance_after_usd, balance_after_cny, SUPPLIER,
        )
        row = await conn.fetchrow(
            "INSERT INTO ak_supplier_ledger "
            "(supplier, entry_type, amount_micro_usd, balance_after_micro_usd, amount_micro_cny, "
            " balance_after_micro_cny, original_amount, original_currency, fx_rate, reference, note, created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *",
            SUPPLIER, entry_type, micro_usd, balance_after_usd, micro_cny, balance_after_cny,
            amount, currency.upper(), rate,
            reference, note, admin_id,
        )
    return dict(row)


def _per_million_to_micro_cny(value: Decimal) -> int:
    if value < 0:
        raise ValueError("official price must not be negative")
    return int((value * Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
        _per_million_to_micro_cny(official_input),
        _per_million_to_micro_cny(official_output),
        _per_million_to_micro_cny(official_cache_read),
        _per_million_to_micro_cny(official_cache_write),
    ]
    exchange = await fx.current_usd_cny()
    rate = Decimal(str(exchange["rate"]))
    legacy_prices = [_scaled(value, Decimal("1") / rate / Decimal(1000)) for value in prices]
    sale_prices = [_scaled(value, sale_multiplier) for value in legacy_prices]
    async with db_util.transaction() as conn:
        row = await conn.fetchrow(
            "INSERT INTO ak_supplier_model_terms "
            "(supplier, model, display_name, official_input_micro_usd_per_1k, "
            " official_output_micro_usd_per_1k, official_cache_read_micro_usd_per_1k, "
            " official_cache_write_micro_usd_per_1k, official_input_micro_cny_per_million, "
            " official_output_micro_cny_per_million, official_cache_read_micro_cny_per_million, "
            " official_cache_write_micro_cny_per_million, pricing_fx_rate, supplier_multiplier, sale_multiplier, updated_by, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,now()) "
            "ON CONFLICT (supplier, model) DO UPDATE SET display_name=EXCLUDED.display_name, "
            " official_input_micro_usd_per_1k=EXCLUDED.official_input_micro_usd_per_1k, "
            " official_output_micro_usd_per_1k=EXCLUDED.official_output_micro_usd_per_1k, "
            " official_cache_read_micro_usd_per_1k=EXCLUDED.official_cache_read_micro_usd_per_1k, "
            " official_cache_write_micro_usd_per_1k=EXCLUDED.official_cache_write_micro_usd_per_1k, "
            " official_input_micro_cny_per_million=EXCLUDED.official_input_micro_cny_per_million, "
            " official_output_micro_cny_per_million=EXCLUDED.official_output_micro_cny_per_million, "
            " official_cache_read_micro_cny_per_million=EXCLUDED.official_cache_read_micro_cny_per_million, "
            " official_cache_write_micro_cny_per_million=EXCLUDED.official_cache_write_micro_cny_per_million, "
            " pricing_fx_rate=EXCLUDED.pricing_fx_rate, "
            " supplier_multiplier=EXCLUDED.supplier_multiplier, sale_multiplier=EXCLUDED.sale_multiplier, "
            " updated_by=EXCLUDED.updated_by, updated_at=now() RETURNING *",
            SUPPLIER, name, display_name, *legacy_prices, *prices, rate,
            supplier_multiplier, sale_multiplier, admin_id,
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


async def sale_cost_micro_usd(model: str, prompt_tokens: int, completion_tokens: int,
                              cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> Optional[int]:
    """墨行客户价固定为人民币；按请求时汇率折成 micro-USD 进入统一用户余额账本。"""
    row = await db_util.fetchrow(
        "SELECT * FROM ak_supplier_model_terms WHERE supplier=$1 AND model=$2",
        SUPPLIER, canonical_model(model),
    )
    if not row:
        return None
    term = dict(row)
    official_cny, _ = usage_cost(term, prompt_tokens, completion_tokens,
                                 cache_read_tokens, cache_write_tokens)
    sale_cny = _scaled(official_cny, term["sale_multiplier"])
    exchange = await fx.current_usd_cny()
    return _scaled(sale_cny, Decimal("1") / Decimal(str(exchange["rate"])))


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
        "coalesce(sum(sales_micro_cny),0) AS sales, "
        "coalesce(sum(supplier_cost_micro_cny),0) AS supplier_cost, "
        "coalesce(sum(charged_paid_micro_cny),0) AS paid_sales, "
        "coalesce(sum(charged_trial_micro_cny),0) AS trial_sales, "
        "count(*) FILTER (WHERE supplier_accounting_status IS DISTINCT FROM 'posted') AS accounting_issue_calls "
        "FROM ak_usage_logs WHERE supplier = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day')",
        SUPPLIER, days,
    )
    ledger_totals = await db_util.fetchrow(
        "SELECT coalesce(sum(amount_micro_cny) FILTER (WHERE entry_type='topup'),0) AS topups, "
        "coalesce(sum(amount_micro_cny) FILTER (WHERE entry_type='adjustment'),0) AS adjustments, "
        "coalesce(sum(-amount_micro_cny) FILTER (WHERE entry_type='usage'),0) AS usage_cost, "
        "coalesce(sum(amount_micro_cny),0) AS ledger_balance "
        "FROM ak_supplier_ledger WHERE supplier = $1",
        SUPPLIER,
    )
    daily = await db_util.fetch(
        "SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) AS calls, "
        "coalesce(sum(total_tokens),0) AS tokens, coalesce(sum(sales_micro_cny),0) AS sales, "
        "coalesce(sum(supplier_cost_micro_cny),0) AS supplier_cost, "
        "coalesce(sum(charged_paid_micro_cny),0) AS paid_sales, "
        "coalesce(sum(charged_trial_micro_cny),0) AS trial_sales "
        "FROM ak_usage_logs WHERE supplier = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY day ORDER BY day DESC",
        SUPPLIER, days,
    )
    by_model = await db_util.fetch(
        "SELECT model, upstream_model, count(*) AS calls, coalesce(sum(total_tokens),0) AS tokens, "
        "coalesce(sum(sales_micro_cny),0) AS sales, "
        "coalesce(sum(supplier_cost_micro_cny),0) AS supplier_cost "
        "FROM ak_usage_logs WHERE supplier = $1 "
        "AND created_at >= now() - ($2::int * interval '1 day') "
        "GROUP BY model, upstream_model ORDER BY supplier_cost DESC",
        SUPPLIER, days,
    )
    recent_usage = await db_util.fetch(
        "SELECT l.id, l.created_at, u.email, l.request_id, l.model, l.upstream_model, "
        "l.prompt_tokens, l.completion_tokens, l.cache_read_tokens, l.cache_write_tokens, l.total_tokens, "
        "l.sales_micro_cny AS sales, l.supplier_cost_micro_cny AS supplier_cost, "
        "l.supplier_multiplier, l.sale_multiplier, l.user_multiplier, "
        "l.charged_paid_micro_cny, l.charged_trial_micro_cny, "
        "l.supplier_accounting_status "
        "FROM ak_usage_logs l LEFT JOIN ak_users u ON u.id=l.user_id "
        "WHERE l.supplier=$1 ORDER BY l.created_at DESC LIMIT $2",
        SUPPLIER, limit,
    )
    ledger = await db_util.fetch(
        "SELECT * FROM ak_supplier_ledger WHERE supplier=$1 ORDER BY created_at DESC LIMIT $2",
        SUPPLIER, limit,
    )
    account_dict = dict(account) if account else {"supplier": SUPPLIER, "balance_micro_cny": 0}
    sales = int(period["sales"] or 0)
    supplier_cost = int(period["supplier_cost"] or 0)
    paid_sales = int(period["paid_sales"] or 0)
    totals = dict(ledger_totals)
    totals["internal_variance_micro_cny"] = (
        int(account_dict.get("balance_micro_cny") or 0) - int(totals.get("ledger_balance") or 0)
    )
    return {
        "days": days,
        "account": account_dict,
        "terms": [dict(row) for row in terms],
        "period": {
            **dict(period),
            "gross_profit_micro_cny": sales - supplier_cost,
            "cash_contribution_micro_cny": paid_sales - supplier_cost,
        },
        "ledger_totals": totals,
        "daily": [dict(row) for row in daily],
        "by_model": [dict(row) for row in by_model],
        "recent_usage": [dict(row) for row in recent_usage],
        "ledger": [dict(row) for row in ledger],
    }
