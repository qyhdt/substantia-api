# -*- coding: utf-8 -*-
"""
进程级 slot 池单例 + 配置加载入口。

slot 配置来源（优先级，后续 M5 admin 接管）：
1. 显式 configure(...)（测试 / admin 热更新）
2. 环境变量 CLAUDE_SLOTS_JSON（JSON 数组）
3. 空池（route() 会抛 NoRoutableSlotError，提示先配 slot）

健康态是运行时的，不随配置覆盖：reconfigure 时按 id 保留已有 slot 的 health/cooldown。
"""
from __future__ import annotations

import json
import threading
from typing import List, Optional

from services.claude import store
from services.claude.router import SlotRouter
from services.claude.slots import Slot

_lock = threading.Lock()
_router: Optional[SlotRouter] = None


def load_slots_from_json(raw: str) -> List[Slot]:
    """解析 JSON 数组为 Slot 列表。空/非法 → []（调用方决定是否报错）。"""
    raw = (raw or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("CLAUDE_SLOTS_JSON 必须是 JSON 数组")
    return [Slot.model_validate(item) for item in data]


def configure(slots: List[Slot]) -> SlotRouter:
    """用新 slot 列表重建池；按 id 保留已有 slot 的运行时健康态。"""
    global _router
    with _lock:
        old = {s.id: s for s in _router.all_slots()} if _router else {}
        for s in slots:
            prev = old.get(s.id)
            if prev is not None:
                s.health = prev.health
                s.cooldown_until = prev.cooldown_until
        _router = SlotRouter(slots)
        return _router


def get_router() -> SlotRouter:
    """拿进程单例；首次惰性从 CLAUDE_SLOTS_JSON 加载（无则空池）。"""
    global _router
    if _router is None:
        with _lock:
            if _router is None:
                _router = SlotRouter(store.load())
    return _router


def save_and_reconfigure(slots: List[Slot]) -> SlotRouter:
    """admin 用：持久化 slot 列表到 store + 重建路由池（保留运行时健康态）。"""
    store.save(slots)
    return configure(slots)


def reset_for_test() -> None:
    """仅测试用：清掉单例。"""
    global _router
    with _lock:
        _router = None
