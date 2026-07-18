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

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Iterable, List, Optional

from config.settings import settings
from services.claude import store
from services.claude.router import SlotRouter
from services.claude.slots import Slot, SlotType

log = logging.getLogger("claude.registry")

_lock = threading.Lock()
_router: Optional[SlotRouter] = None

_RUNTIME_FIELDS = {"health", "cooldown_until"}

# ---- env 托管的兜底档表 ----
# 固定优先级链：业务/订阅 slot（默认 0）→ 以下各档按 priority 升序依次兜底。
# 新增一档只需在此登记（id、priority、settings 前缀、额外注入 env），无需改其它代码。
# 每档的 <PREFIX>BASE_URL / <PREFIX>AUTH_TOKEN / <PREFIX>MODEL 三件套配齐才启用；
# 密钥只进入 Slot.env，日志和 fingerprint 都不会输出明文。
FALLBACK_TIERS: tuple = (
    {
        "id": "fallback-moxing",
        "priority": 100,
        "settings_prefix": "CLAUDE_FALLBACK_MOXING_",
        "extra_env": {},
    },
    {
        "id": "fallback-gemini",
        "priority": 200,
        "settings_prefix": "CLAUDE_FALLBACK_GEMINI_",
        "extra_env": {},
    },
)
_FALLBACK_SLOT_IDS = frozenset(tier["id"] for tier in FALLBACK_TIERS)


def is_managed_fallback_slot(slot_id: str) -> bool:
    """该 id 是否由 env 独占管理，不能通过 slot CRUD 覆盖。"""
    return slot_id in _FALLBACK_SLOT_IDS


def _tier_slot(tier: dict) -> Optional[Slot]:
    """按档表从 settings 合成一个兜底 slot；三件套不齐返回 None（该档停用）。"""
    prefix = tier["settings_prefix"]
    base = (getattr(settings, f"{prefix}BASE_URL", "") or "").strip()
    token = (getattr(settings, f"{prefix}AUTH_TOKEN", "") or "").strip()
    model = (getattr(settings, f"{prefix}MODEL", "") or "").strip()
    if not (base and token and model):
        return None
    return Slot(
        id=tier["id"],
        type=SlotType.API_KEY,
        priority=tier["priority"],
        env={
            "ANTHROPIC_BASE_URL": base,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_MODEL": model,
            **tier["extra_env"],
        },
    )


def fallback_slots_from_settings() -> List[Slot]:
    """把配置齐全的 env 兜底档合成为只存在于运行时的 api_key slots（按档表顺序）。"""
    out: List[Slot] = []
    for tier in FALLBACK_TIERS:
        slot = _tier_slot(tier)
        if slot is not None:
            out.append(slot)
    return out


def merge_fallback_slots(slots: Iterable[Slot]) -> List[Slot]:
    """把 env 管理的兜底 slots 与任意来源的业务 slots 合并。

    保留业务输入顺序，但无条件剔除业务来源里的两个保留 id，再追加当前 env 合成项。
    因此撤掉 env 配置也会真正停用 fallback，不会从 store/DB 复活旧密钥或旧 priority。
    """
    merged = {slot.id: slot for slot in slots if slot.id not in _FALLBACK_SLOT_IDS}
    for slot in fallback_slots_from_settings():
        merged[slot.id] = slot
    return list(merged.values())


def slots_fingerprint(slots: Iterable[Slot]) -> str:
    """返回 slot 业务配置的稳定指纹（排除运行时 health/cooldown）。"""
    # 与 SlotRouter 一致，重复 id 采用最后一项；按 id 排序避免来源遍历顺序造成误刷新。
    by_id = {slot.id: slot for slot in slots}
    payload = [
        by_id[slot_id].model_dump(mode="json", exclude=_RUNTIME_FIELDS)
        for slot_id in sorted(by_id)
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        slots = db_source.slots_from_db()  # None=失败/未配；[]=0 行
        return None if slots is None else merge_fallback_slots(slots)
    # 只要显式配置了账号目录，它就是订阅 slot 的事实来源；即使当前为空，也不能
    # 悄悄回落 slots.json 复活另一批旧账号。fallback 始终与扫描结果合并。
    if accounts_dir():
        return merge_fallback_slots(slots_from_shared_accounts_dir())
    return merge_fallback_slots(store.load())


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
    """周期热更新 slot 池：账号或业务配置变化则重配（保留已有 slot 健康态）。
    - db 源：查库结果为准（含删除/禁用）；查询失败(None)则不动现有池。
    - dir 源：显式账号目录/slots.json 为准，增删至空也会刷新，避免旧凭据残留。
    - fallback：与任一来源合并；env 的端点/token/model 改变即使 id 不变也会刷新。
    probe_loop 每轮调一次，让账号增删免重启即生效。"""
    slots = load_slots_by_source()
    if slots is None:
        # db 查询失败 / dir 未配：保留现有池不动
        return _router
    current = _router.all_slots() if _router else []
    if slots_fingerprint(slots) != slots_fingerprint(current):
        log.info("slot 配置变化 → 重配 slot 池 (source=%s): %s",
                 settings.CLAUDE_SLOTS_SOURCE, sorted(s.id for s in slots))
        return configure(slots)
    return _router


def save_and_reconfigure(slots: List[Slot]) -> SlotRouter:
    """admin 用：仅持久化业务 slot，再合成 env fallback 并重建路由池。"""
    persistent = [slot for slot in slots if slot.id not in _FALLBACK_SLOT_IDS]
    store.save(persistent)
    return configure(merge_fallback_slots(persistent))


def reset_for_test() -> None:
    """仅测试用：清掉单例。"""
    global _router
    with _lock:
        _router = None
