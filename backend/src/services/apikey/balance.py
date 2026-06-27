# -*- coding: utf-8 -*-
"""
余额分桶助手：试用桶（trial，有限期）+ 实付桶（balance，永久）。

有效余额 = 实付 + (试用有效时的试用余额)。
试用有效 = trial_micro_usd>0 且（trial_permanent 或 未到 trial_expires_at）。
消费时先扣试用桶，再扣实付（见 usage.record_and_charge）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def trial_active(u: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    trial = int(u.get("trial_micro_usd") or 0)
    if trial <= 0:
        return False
    if u.get("trial_permanent"):
        return True
    exp = u.get("trial_expires_at")
    if exp is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now < exp


def effective_balance(u: Dict[str, Any], now: Optional[datetime] = None) -> int:
    """可用余额：实付 + 有效试用。"""
    paid = int(u.get("balance_micro_usd") or 0)
    trial = int(u.get("trial_micro_usd") or 0) if trial_active(u, now) else 0
    return paid + trial
