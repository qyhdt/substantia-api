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
import logging
import threading
from pathlib import Path
from typing import List, Optional

from config.settings import settings
from services.claude import store
from services.claude.router import SlotRouter
from services.claude.slots import Slot, SlotType

log = logging.getLogger("claude.registry")

_lock = threading.Lock()
_router: Optional[SlotRouter] = None


def slots_from_shared_accounts_dir() -> List[Slot]:
    """从共享账号目录（<dir>/<acc>/.credentials.json）动态生成订阅 slot。
    与境核AI（小智）账号池共用同一批账号目录：小智后台 add-account.sh 新增的账号，
    这里自动纳入轮询。凭据文件是同一份（同机 bind），续期同步、不互相作废。
    未配 CLAUDE_SHARED_ACCOUNTS_DIR → 返回 []（回退 slots.json）。"""
    d = (settings.CLAUDE_SHARED_ACCOUNTS_DIR or "").strip()
    if not d:
        return []
    img = (settings.CLAUDE_SHARED_ACCOUNTS_IMAGE or "").strip() or "qyhdt/private:claude-loggedin"
    out: List[Slot] = []
    try:
        for sub in sorted(p for p in Path(d).iterdir() if p.is_dir()):
            if (sub / ".credentials.json").exists():
                out.append(Slot(
                    id=sub.name, type=SlotType.SUBSCRIPTION, enabled=True,
                    weight=1.0, creds_dir=str(sub), image=img,
                ))
    except FileNotFoundError:
        log.warning("shared accounts dir 不存在: %s", d)
    except Exception as e:  # noqa: BLE001
        log.warning("scan shared accounts dir failed: %s", e)
    return out


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
    """拿进程单例；首次惰性加载。配了共享账号目录 → 用它（与小智账号池同源）；否则回退 slots.json。"""
    global _router
    if _router is None:
        with _lock:
            if _router is None:
                shared = slots_from_shared_accounts_dir()
                _router = SlotRouter(shared if shared else store.load())
    return _router


def refresh_shared_slots() -> Optional[SlotRouter]:
    """周期重扫共享账号目录：账号集有增删则热更新（保留已有 slot 健康态）。
    未配置共享目录（返回空）则不动现有池。probe_loop 每轮调一次，让新账号免重启即生效。"""
    shared = slots_from_shared_accounts_dir()
    if not shared:
        return None
    cur_ids = {s.id for s in _router.all_slots()} if _router else set()
    if {s.id for s in shared} != cur_ids:
        log.info("shared accounts 变化 → 重配 slot 池: %s", sorted(s.id for s in shared))
        return configure(shared)
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
