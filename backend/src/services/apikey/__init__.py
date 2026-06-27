# -*- coding: utf-8 -*-
"""APIKey 分发系统：下游用户/令牌/计费/网关。

上游算力（slot 池 / 路由 / 容器 / exec）由 services.claude 提供，本包只消费它。
设计见 doc/apikey-distribution-plan.md。
"""

# $1 = 1_000_000 微美元
MICRO_PER_USD = 1_000_000


def usd(micro: int) -> float:
    """微美元 → 美元（展示用）。"""
    return round((micro or 0) / MICRO_PER_USD, 6)


def to_micro(dollars: float) -> int:
    return int(round(float(dollars) * MICRO_PER_USD))
