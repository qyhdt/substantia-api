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

from collections import defaultdict
import hashlib
import itertools
import math
import re
import threading
from typing import Collection, Dict, Iterable, List, Optional

from config.settings import settings
from services.claude.slots import Slot


class NoRoutableSlotError(RuntimeError):
    """池里没有任何可路由的 slot（全空 / 全禁用 / 全不健康 / 本次均已排除）。"""


def _hash01(key: str) -> float:
    """把 key 映射到开区间 (0, 1)，供 HRW 打分。取 sha256 前 8 字节。"""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")
    # +1 / +2 保证落在 (0,1) 开区间，避开 ln(0)
    return (n + 1.0) / (2 ** 64 + 1.0)


def _score(user_id: str, slot: Slot) -> float:
    h = _hash01(f"{user_id}:{slot.id}")
    return -slot.weight / math.log(h)


_NAT_RE = re.compile(r"(\d+)")


def _natural_key(s: str):
    """ID 自然序键：数字段按数值比（acc2 < acc10），其余按字面。
    保证 ${prefix}-acc1、${prefix}-acc2…${prefix}-acc10 按人的直觉排队。"""
    return [int(t) if t.isdigit() else t for t in _NAT_RE.split(s)]


class SlotRouter:
    """slot 池 + 严格 priority + RR/HRW 路由。

    priority 数值越小越优先；只有前一档没有候选（或被本次请求排除）时才进入
    下一档。同 priority 内仍保留既有 round-robin / weighted-HRW 语义。
    线程安全（健康态会被探针并发改）。
    """

    def __init__(self, slots: Optional[Iterable[Slot]] = None) -> None:
        self._lock = threading.RLock()
        self._slots: Dict[str, Slot] = {}
        for s in slots or []:
            self._slots[s.id] = s
        # round_robin 发号器：每请求取号定起点（线程安全，itertools.count 的 next 是原子的）
        self._rr = itertools.count()

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

    def route_candidates(
        self,
        user_id: str,
        now: Optional[float] = None,
        exclude_slot_ids: Optional[Collection[str]] = None,
    ) -> List[Slot]:
        """返回一次请求可依次尝试的 slot 快照。

        结果严格按 priority 从小到大排列且不重复；同 priority 内按当前策略排序：
        HRW 为分数降序，RR 为本次发号器起点的轮转顺序。调用方可顺序遍历实现
        subscription → Gemini → GLM 等透明故障转移。

        ``exclude_slot_ids`` 用于重试时排除本请求已尝试的 slot。没有候选返回 []，
        由 ``route`` 统一转换成 ``NoRoutableSlotError``。
        """
        excluded = set(exclude_slot_ids or ())
        policy = (settings.CLAUDE_ROUTE_POLICY or "").strip().lower()
        with self._lock:
            candidates = [
                slot for slot in self._slots.values()
                if slot.id not in excluded and slot.is_routable(now)
            ]
            if not candidates:
                return []

            by_priority: Dict[int, List[Slot]] = defaultdict(list)
            for slot in candidates:
                by_priority[slot.priority].append(slot)

            ordered: List[Slot] = []
            if policy in ("hash", "hrw"):
                for priority in sorted(by_priority):
                    # score 几乎不会相等；自然序 tie-break 让结果与插入顺序无关。
                    group = sorted(
                        by_priority[priority],
                        key=lambda slot: (-_score(user_id, slot), _natural_key(slot.id)),
                    )
                    ordered.extend(group)
                return ordered

            ticket = next(self._rr)
            for priority in sorted(by_priority):
                group = sorted(by_priority[priority], key=lambda slot: _natural_key(slot.id))
                start = ticket % len(group)
                ordered.extend(group[start:] + group[:start])
            return ordered

    def route(
        self,
        user_id: str,
        now: Optional[float] = None,
        exclude_slot_ids: Optional[Collection[str]] = None,
    ) -> Slot:
        """按 CLAUDE_ROUTE_POLICY 选 slot；无可路由 slot 抛 NoRoutableSlotError。
        - hash/hrw：加权 HRW，同一用户固定命中同一 slot（会话粘性）。
        - round_robin（默认）：同档 slot 按 ID 自然序轮询，user_id 不参与选择。
        两种策略都先严格选择最低可用 priority；可通过 exclude_slot_ids 排除已试 slot。
        """
        candidates = self.route_candidates(user_id, now, exclude_slot_ids)
        if not candidates:
            raise NoRoutableSlotError("no routable slot (empty / all disabled / all unhealthy / excluded)")
        return candidates[0]

    def _route_hash(self, user_id: str, now: Optional[float] = None) -> Slot:
        # 保留给既有内部/测试调用方；临时固定 HRW，不受当前 settings policy 影响。
        with self._lock:
            cands = [slot for slot in self._slots.values() if slot.is_routable(now)]
        if not cands:
            raise NoRoutableSlotError("no routable slot (empty / all disabled / all unhealthy)")
        first_priority = min(slot.priority for slot in cands)
        tier = [slot for slot in cands if slot.priority == first_priority]
        return max(tier, key=lambda slot: _score(user_id, slot))

    def _route_round_robin(self, now: Optional[float] = None) -> Slot:
        # 保留给既有内部/测试调用方；逻辑与 route_candidates 的 RR 首项一致。
        with self._lock:
            cands = [slot for slot in self._slots.values() if slot.is_routable(now)]
            if not cands:
                raise NoRoutableSlotError("no routable slot (empty / all disabled / all unhealthy)")
            first_priority = min(slot.priority for slot in cands)
            tier = sorted(
                (slot for slot in cands if slot.priority == first_priority),
                key=lambda slot: _natural_key(slot.id),
            )
            return tier[next(self._rr) % len(tier)]
