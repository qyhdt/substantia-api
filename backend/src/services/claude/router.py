# -*- coding: utf-8 -*-
"""
用户 → slot 路由：加权 Rendezvous（HRW，最高随机权重）哈希。

为什么用 HRW 而不是一致性哈希环：
- 无状态、纯函数：slot = argmax_i  score(user_id, slot_i)，不用维护 ring。
- sticky：同一 user_id 永远落同一 slot（会话缓存连续 + 单 sub 负载是稳定用户子集）。
- 增删 slot 只搬 ~1/N 用户（HRW 天然性质，见 test_claude_router）。
- 天然支持权重与「剔除不健康 slot」。

加权公式（Cassandra/weighted-HRW）：score = -weight / ln(h)，h∈(0,1) 由 hash 派生。
argmax 即按 weight 比例分布；weight 相等时退化为均匀分布。
"""
from __future__ import annotations

import hashlib
import math
import threading
from typing import Dict, Iterable, List, Optional

from services.claude.slots import Slot


class NoRoutableSlotError(RuntimeError):
    """池里没有任何可路由的 slot（全空 / 全禁用 / 全在冷却）。"""


def _hash01(key: str) -> float:
    """把 key 映射到开区间 (0, 1)，供 HRW 打分。取 sha256 前 8 字节。"""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")
    # +1 / +2 保证落在 (0,1) 开区间，避开 ln(0)
    return (n + 1.0) / (2 ** 64 + 1.0)


def _score(user_id: str, slot: Slot) -> float:
    h = _hash01(f"{user_id}:{slot.id}")
    return -slot.weight / math.log(h)


class SlotRouter:
    """slot 池 + HRW 路由。线程安全（健康态会被探针并发改）。"""

    def __init__(self, slots: Optional[Iterable[Slot]] = None) -> None:
        self._lock = threading.RLock()
        self._slots: Dict[str, Slot] = {}
        for s in slots or []:
            self._slots[s.id] = s

    # ---------- 池管理 ----------
    def upsert(self, slot: Slot) -> None:
        with self._lock:
            self._slots[slot.id] = slot

    def remove(self, slot_id: str) -> Optional[Slot]:
        with self._lock:
            return self._slots.pop(slot_id, None)

    def get(self, slot_id: str) -> Optional[Slot]:
        with self._lock:
            return self._slots.get(slot_id)

    def all_slots(self) -> List[Slot]:
        with self._lock:
            return list(self._slots.values())

    # ---------- 健康态 ----------
    def mark_unhealthy(self, slot_id: str, cooldown_seconds: float = 600.0,
                       now: Optional[float] = None) -> None:
        with self._lock:
            s = self._slots.get(slot_id)
            if s:
                s.mark_unhealthy(cooldown_seconds, now=now)

    def mark_healthy(self, slot_id: str) -> None:
        with self._lock:
            s = self._slots.get(slot_id)
            if s:
                s.mark_healthy()

    # ---------- 路由 ----------
    def routable_slots(self, now: Optional[float] = None) -> List[Slot]:
        with self._lock:
            return [s for s in self._slots.values() if s.is_routable(now)]

    def route(self, user_id: str, now: Optional[float] = None) -> Slot:
        """返回该用户命中的 slot；无可路由 slot 抛 NoRoutableSlotError。"""
        cands = self.routable_slots(now)
        if not cands:
            raise NoRoutableSlotError("no routable slot (empty / all disabled / all in cooldown)")
        return max(cands, key=lambda s: _score(user_id, s))
