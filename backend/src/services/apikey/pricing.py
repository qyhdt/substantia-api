# -*- coding: utf-8 -*-
"""
逐模型定价 + 成本计算。

价格单位：微美元 / 1k token（输入、输出分开）。
成本 = ceil(prompt/1000 * in_price) + ceil(completion/1000 * out_price)，取整到微美元。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from utils import db as db_util
from utils.pm_logger import get_app_logger

log = get_app_logger()


async def list_prices(*, include_supplier_terms: bool = False) -> List[Dict[str, Any]]:
    private_fields = ", t.supplier, t.supplier_multiplier" if include_supplier_terms else ""
    rows = await db_util.fetch(
        "SELECT p.*, (t.supplier IS NOT NULL) AS supplier_managed, t.sale_multiplier, "
        "t.official_input_micro_usd_per_1k, t.official_output_micro_usd_per_1k, "
        "t.official_cache_read_micro_usd_per_1k, t.official_cache_write_micro_usd_per_1k, "
        "t.official_input_micro_cny_per_million, t.official_output_micro_cny_per_million, "
        "t.official_cache_read_micro_cny_per_million, t.official_cache_write_micro_cny_per_million, "
        f"t.pricing_fx_rate{private_fields} "
        "FROM ak_model_prices p LEFT JOIN ak_supplier_model_terms t ON t.model=p.model "
        "AND t.supplier='moxing' ORDER BY p.model"
    )
    return [dict(r) for r in rows]


async def get_price(model: str) -> Optional[Dict[str, Any]]:
    if not model:
        return None
    row = await db_util.fetchrow("SELECT * FROM ak_model_prices WHERE model = $1", model)
    return dict(row) if row else None


async def upsert_price(
    model: str,
    *,
    display_name: Optional[str] = None,
    input_micro_usd_per_1k: int = 0,
    output_micro_usd_per_1k: int = 0,
    cache_read_micro_usd_per_1k: Optional[int] = None,
    cache_write_micro_usd_per_1k: Optional[int] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    # 缓存价未显式给出时，按官方比例从输入价派生（read 10% / write 125%）。
    in_price = int(input_micro_usd_per_1k)
    cr = int(cache_read_micro_usd_per_1k) if cache_read_micro_usd_per_1k is not None else round(in_price * 0.10)
    cw = int(cache_write_micro_usd_per_1k) if cache_write_micro_usd_per_1k is not None else round(in_price * 1.25)
    row = await db_util.fetchrow(
        """
        INSERT INTO ak_model_prices
            (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k,
             cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k, enabled, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, now())
        ON CONFLICT (model) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            input_micro_usd_per_1k = EXCLUDED.input_micro_usd_per_1k,
            output_micro_usd_per_1k = EXCLUDED.output_micro_usd_per_1k,
            cache_read_micro_usd_per_1k = EXCLUDED.cache_read_micro_usd_per_1k,
            cache_write_micro_usd_per_1k = EXCLUDED.cache_write_micro_usd_per_1k,
            enabled = EXCLUDED.enabled,
            updated_at = now()
        RETURNING *
        """,
        model,
        display_name,
        in_price,
        int(output_micro_usd_per_1k),
        cr,
        cw,
        bool(enabled),
    )
    return dict(row)


def _cache_read_price(price: Dict[str, Any]) -> int:
    """缓存读取价：表里有非 0 值就用，否则按输入价 10% 派生（兼容旧库未回填的行）。"""
    v = price.get("cache_read_micro_usd_per_1k") or 0
    return int(v) if v else round(int(price.get("input_micro_usd_per_1k") or 0) * 0.10)


def _cache_write_price(price: Dict[str, Any]) -> int:
    """缓存写入价：表里有非 0 值就用，否则按输入价 125% 派生。"""
    v = price.get("cache_write_micro_usd_per_1k") or 0
    return int(v) if v else round(int(price.get("input_micro_usd_per_1k") or 0) * 1.25)


async def compute_cost_micro_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> int:
    """按命中模型的价算成本（微美元），缓存 token 单独按官方折扣计价。

    - prompt_tokens     : 真正新输入（全价）
    - cache_read_tokens : 缓存命中读取（官方 ≈ 10% 输入价）
    - cache_write_tokens: 缓存创建写入（官方 ≈ 125% 输入价）
    - completion_tokens : 输出（输出价）
    模型无定价 → 记 0 并告警（不阻断用户）。
    """
    from services.apikey import moxing_accounting
    managed = await moxing_accounting.sale_cost_micro_usd(
        model, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens,
    )
    if managed is not None:
        return managed
    price = await get_price(model)
    if not price:
        log.warning("ak_pricing: no price for model=%s, charging 0", model)
        return 0
    in_cost = math.ceil((prompt_tokens or 0) / 1000 * price["input_micro_usd_per_1k"])
    out_cost = math.ceil((completion_tokens or 0) / 1000 * price["output_micro_usd_per_1k"])
    cr_cost = math.ceil((cache_read_tokens or 0) / 1000 * _cache_read_price(price))
    cw_cost = math.ceil((cache_write_tokens or 0) / 1000 * _cache_write_price(price))
    return int(in_cost + out_cost + cr_cost + cw_cost)
