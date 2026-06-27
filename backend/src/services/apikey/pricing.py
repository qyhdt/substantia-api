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


async def list_prices() -> List[Dict[str, Any]]:
    rows = await db_util.fetch("SELECT * FROM ak_model_prices ORDER BY model")
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
    enabled: bool = True,
) -> Dict[str, Any]:
    row = await db_util.fetchrow(
        """
        INSERT INTO ak_model_prices
            (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k, enabled, updated_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (model) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            input_micro_usd_per_1k = EXCLUDED.input_micro_usd_per_1k,
            output_micro_usd_per_1k = EXCLUDED.output_micro_usd_per_1k,
            enabled = EXCLUDED.enabled,
            updated_at = now()
        RETURNING *
        """,
        model,
        display_name,
        int(input_micro_usd_per_1k),
        int(output_micro_usd_per_1k),
        bool(enabled),
    )
    return dict(row)


async def compute_cost_micro_usd(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """按命中模型的价算成本（微美元）。模型无定价 → 记 0 并告警（不阻断用户）。"""
    price = await get_price(model)
    if not price:
        log.warning("ak_pricing: no price for model=%s, charging 0", model)
        return 0
    in_cost = math.ceil((prompt_tokens or 0) / 1000 * price["input_micro_usd_per_1k"])
    out_cost = math.ceil((completion_tokens or 0) / 1000 * price["output_micro_usd_per_1k"])
    return int(in_cost + out_cost)
