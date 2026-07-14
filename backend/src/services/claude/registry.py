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


def accounts_dir() -> str:
    """本机账号目录：优先 CLAUDE_ACCOUNTS_DIR（本栈自有、可独立部署），
    否则回退 CLAUDE_SHARED_ACCOUNTS_DIR（历史共享池）。都空则返回 ""。"""
    d = (settings.CLAUDE_ACCOUNTS_DIR or "").strip()
    if d:
        return d
    return (settings.CLAUDE_SHARED_ACCOUNTS_DIR or "").strip()


def slots_from_shared_accounts_dir() -> List[Slot]:
    """从账号目录（<dir>/<acc>/.credentials.json）动态生成订阅 slot。
    与境核AI（小智）账号池共用同一批账号目录：小智后台 add-account.sh 新增的账号，
    这里自动纳入轮询。凭据文件是同一份（同机 bind），续期同步、不互相作废。
    未配账号目录 → 返回 []（回退 slots.json）。"""
    d = accounts_dir()
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


def load_slots_by_source() -> Optional[List[Slot]]:
    """按 CLAUDE_SLOTS_SOURCE 选来源：
    - db → claude_slots 表按 CLAUDE_NODE_IP 分片（None=查询失败/未配 NODE_IP；[]=0 行）。
    - 否则账号目录 → slots.json（dir 源；未配目录时回落 store）。"""
    if (settings.CLAUDE_SLOTS_SOURCE or "").strip() == "db":
        from services.claude import db_source
        return db_source.slots_from_db()  # None=失败/未配；[]=0 行
    shared = slots_from_shared_accounts_dir()
    if shared:
        return shared
    return store.load()


def get_router() -> SlotRouter:
    """拿进程单例；首次惰性加载。来源见 load_slots_by_source。"""
    global _router
    if _router is None:
        with _lock:
            if _router is None:
                slots = load_slots_by_source()
                _router = SlotRouter(slots or [])
    return _router


def refresh_shared_slots() -> Optional[SlotRouter]:
    """周期热更新 slot 池：账号集有增删则重配（保留已有 slot 健康态）。
    - db 源：查库结果为准（含删除/禁用）；查询失败(None)则不动现有池。
    - dir 源：重扫目录，空则不动现有池（保持原行为）。
    probe_loop 每轮调一次，让账号增删免重启即生效。"""
    slots = load_slots_by_source()
    if slots is None:
        # db 查询失败 / dir 未配：保留现有池不动
        return _router
    is_db = (settings.CLAUDE_SLOTS_SOURCE or "").strip() == "db"
    if not is_db and not slots:
        # dir 源扫出空：沿用旧行为，不清空
        return _router
    cur_ids = {s.id for s in _router.all_slots()} if _router else set()
    if {s.id for s in slots} != cur_ids:
        log.info("slot 集变化 → 重配 slot 池 (source=%s): %s",
                 settings.CLAUDE_SLOTS_SOURCE, sorted(s.id for s in slots))
        return configure(slots)
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
