# -*- coding: utf-8 -*-
"""外汇参考汇率：当前只提供 USD/CNY，供账单展示与人民币充值换算。

主数据源为 Frankfurter 的最新综合参考汇率（无需 API key）；进程内缓存避免每次请求
访问外网。上游不可用或数据异常时回退到 XUNHUPAY_RMB_PER_USD，保证支付与页面可用。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

import httpx

from config.settings import settings

log = logging.getLogger("ak.fx")

_cache: Dict[str, Any] = {}


def fallback_usd_cny_rate() -> float:
    value = float(settings.XUNHUPAY_RMB_PER_USD or 0)
    return value if value > 0 else 7.2


def _parse_rate(data: Any) -> tuple[float, str | None]:
    if not isinstance(data, dict):
        raise ValueError("invalid exchange-rate response")
    rate = float(data.get("rate") or 0)
    if not 4.0 <= rate <= 12.0:
        raise ValueError("USD/CNY rate outside safety bounds")
    date = str(data.get("date") or "").strip() or None
    return rate, date


def clear_cache() -> None:
    _cache.clear()


async def current_usd_cny() -> Dict[str, Any]:
    now = time.monotonic()
    if _cache and now < float(_cache.get("expires_at") or 0):
        return {k: v for k, v in _cache.items() if k != "expires_at"}

    url = settings.FX_USD_CNY_URL.strip()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            response.raise_for_status()
        rate, rate_date = _parse_rate(response.json())
        result = {
            "rate": round(rate, 6),
            "date": rate_date,
            "source": "Frankfurter",
            "live": True,
        }
        ttl = max(300, int(settings.FX_RATE_CACHE_SECONDS or 3600))
    except Exception as exc:  # noqa: BLE001
        log.warning("USD/CNY rate fetch failed, using configured fallback: %s", type(exc).__name__)
        result = {
            "rate": fallback_usd_cny_rate(),
            "date": None,
            "source": "configured fallback",
            "live": False,
        }
        ttl = 300

    _cache.update(result, expires_at=now + ttl)
    return dict(result)
