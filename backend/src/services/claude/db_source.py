# -*- coding: utf-8 -*-
"""
slot 配置的 DB 来源（CLAUDE_SLOTS_SOURCE=db）。对应目录扫描来源（registry.slots_from_shared_accounts_dir）。

- slots_from_db：查 claude_slots WHERE server_ip=CLAUDE_NODE_IP AND enabled，
  并把每行 creds_json 落成本机 <accounts>/<slot_id>/.credentials.json 供容器挂载。
- sync_creds_to_db：探针后把容器刷新过的 .credentials.json 写回 DB（subscription 命脉）。
- admin CRUD：db_list_slots / db_upsert_slot / db_set_slot_enabled / db_reassign_slot / db_delete_slot。

DB 访问用 utils.db（asyncpg，async-only）。本模块对外暴露**同步**接口（与 registry/docker_manager/
health 的同步风格一致，控制器经 asyncio.to_thread 调用），内部经一个独立后台事件循环把
协程跑成同步（asyncpg 的 pool 按事件循环隔离，故用专属 loop 复用一个 pool）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from services.claude.docker_manager import DockerManagerError, assert_safe_id
from services.claude.slots import Slot, SlotType

log = logging.getLogger("claude.db_source")

_NODE_UID = 1000
_NODE_GID = 1000


# ============================================================================
# async → sync 桥：专属后台事件循环（asyncpg pool 按 loop 隔离，复用一个 pool）
# ============================================================================
_bridge_loop: Optional[asyncio.AbstractEventLoop] = None
_bridge_lock = threading.Lock()


def _get_bridge_loop() -> asyncio.AbstractEventLoop:
    global _bridge_loop
    if _bridge_loop is None:
        with _bridge_lock:
            if _bridge_loop is None:
                loop = asyncio.new_event_loop()
                t = threading.Thread(target=loop.run_forever, daemon=True,
                                     name="claude-db-bridge")
                t.start()
                _bridge_loop = loop
    return _bridge_loop


def _run_sync(coro):
    """在专属后台 loop 上把协程跑成同步。仅从非 bridge 线程调用（控制器经 to_thread）。"""
    loop = _get_bridge_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def _affected(status: str) -> int:
    """从 asyncpg execute 的状态串取影响行数，如 'UPDATE 3' / 'INSERT 0 1' / 'DELETE 1'。"""
    try:
        return int((status or "").split()[-1])
    except (ValueError, IndexError):
        return 0


# ============================================================================
# 身份 / 目录 / 镜像
# ============================================================================
def slots_source_is_db() -> bool:
    """当前是否用 DB 作为 slot 来源。"""
    return (settings.CLAUDE_SLOTS_SOURCE or "").strip() == "db"


def node_ip() -> str:
    """本节点身份（出口 IP）。"""
    return (settings.CLAUDE_NODE_IP or "").strip()


def _accounts_dir() -> str:
    """本机账号目录：优先 CLAUDE_ACCOUNTS_DIR，回退 CLAUDE_SHARED_ACCOUNTS_DIR。"""
    d = (settings.CLAUDE_ACCOUNTS_DIR or "").strip()
    if d:
        return d
    return (settings.CLAUDE_SHARED_ACCOUNTS_DIR or "").strip()


def _creds_base_dir() -> Optional[Path]:
    """凭据落地根目录（= 账号目录），容器按此挂载。未配则 None。"""
    d = _accounts_dir()
    return Path(d).expanduser() if d else None


def default_subscription_image() -> str:
    """subscription slot 默认镜像：优先共享账号镜像，回落 base，再回落 claude-runner。"""
    img = (settings.CLAUDE_SHARED_ACCOUNTS_IMAGE or "").strip()
    if img:
        return img
    img = (settings.CLAUDE_BASE_IMAGE or "").strip()
    if img:
        return img
    return "claude-runner"


def _chown(p: Path) -> None:
    try:
        os.chown(p, _NODE_UID, _NODE_GID)
    except Exception:  # noqa: BLE001
        pass


def _materialize_creds(dir_: Path, creds_json: str) -> None:
    """幂等地把 creds_json 写成 <dir>/.credentials.json（0600 + chown node）。内容未变不动 mtime。"""
    dir_.mkdir(parents=True, exist_ok=True)
    _chown(dir_)
    p = dir_ / ".credentials.json"
    try:
        if p.exists() and p.read_text(encoding="utf-8") == creds_json:
            return
    except Exception:  # noqa: BLE001
        pass
    p.write_text(creds_json, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except Exception:  # noqa: BLE001
        pass
    _chown(p)


def remove_slot_creds_dir(slot_id: str) -> None:
    """删除本机该 slot 的凭据目录（<accounts>/<slot>）。删除/移走 slot 后必须调用，
    否则重启 seed 会把残留凭据重新导回、账号"复活"。"""
    slot_id = (slot_id or "").strip()
    if not slot_id:
        return
    base = _creds_base_dir()
    if base is None:
        return
    dir_ = base / slot_id
    if dir_ == base:  # 安全：绝不删账号目录根
        return
    try:
        import shutil
        shutil.rmtree(dir_, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        log.warning("删除 slot 凭据目录失败 slot=%s: %s", slot_id, e)


# ============================================================================
# env JSONB 解析（asyncpg 里 jsonb 默认返回 str）
# ============================================================================
def _parse_env(v: Any) -> Dict[str, str]:
    if v is None:
        return {}
    if isinstance(v, dict):
        return {str(k): str(x) for k, x in v.items()}
    try:
        m = json.loads(v)
        if isinstance(m, dict):
            return {str(k): str(x) for k, x in m.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


# ============================================================================
# 加载：claude_slots → Slot 列表（本节点 enabled）
# ============================================================================
def slots_from_db() -> Optional[List[Slot]]:
    """从 claude_slots 加载归属本节点的 enabled slot。
    未配 CLAUDE_NODE_IP 或查询失败 → 返回 None（调用方据此保留现有池，不误清空）；
    查询成功但 0 行 → 返回 []。"""
    nip = node_ip()
    if not nip:
        log.warning("CLAUDE_SLOTS_SOURCE=db 但未配 CLAUDE_NODE_IP，slot 池为空")
        return None
    try:
        return _run_sync(_slots_from_db(nip))
    except Exception as e:  # noqa: BLE001
        log.warning("查 claude_slots 失败：%s", e)
        return None


async def _slots_from_db(nip: str) -> List[Slot]:
    from utils import db
    rows = await db.fetch(
        """
        SELECT slot_id, type, weight, image, creds_json, env
        FROM claude_slots WHERE server_ip=$1 AND enabled=true ORDER BY slot_id
        """,
        nip,
    )
    base = _creds_base_dir()
    out: List[Slot] = []
    for r in rows:
        sid = (r["slot_id"] or "").strip()
        if not sid:
            continue
        creds_dir: Optional[str] = None
        if base is not None:
            cdir = base / sid
            cj = r["creds_json"] or ""
            if cj:
                try:
                    _materialize_creds(cdir, cj)
                except Exception as e:  # noqa: BLE001
                    log.warning("落地 creds 失败 slot=%s: %s", sid, e)
                    continue
            creds_dir = str(cdir)
        out.append(Slot(
            id=sid,
            type=SlotType((r["type"] or "").strip() or SlotType.SUBSCRIPTION.value),
            enabled=True,
            weight=float(r["weight"]) if r["weight"] is not None else 1.0,
            creds_dir=creds_dir,
            image=(r["image"] or "") or None,
            env=_parse_env(r["env"]),
        ))
    return out


# ============================================================================
# 探针后把刷新过的凭据写回 DB（仅 db 源、内容有变时）
# ============================================================================
def sync_creds_to_db(slot: Slot) -> None:
    if not slots_source_is_db() or slot is None:
        return
    nip = node_ip()
    if not nip:
        return
    from services.claude.docker_manager import slot_creds_dir
    try:
        body = (slot_creds_dir(slot) / ".credentials.json").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return
    if not body:
        return
    try:
        _run_sync(_sync_creds_to_db(nip, slot.id, body))
    except Exception as e:  # noqa: BLE001
        log.warning("凭据刷新写回 DB 失败 slot=%s: %s", slot.id, e)


async def _sync_creds_to_db(nip: str, slot_id: str, body: str) -> None:
    from utils import db
    status = await db.execute(
        """
        UPDATE claude_slots SET creds_json=$1, creds_synced_at=now(), updated_at=now()
        WHERE server_ip=$2 AND slot_id=$3 AND creds_json IS DISTINCT FROM $1
        """,
        body, nip, slot_id,
    )
    if _affected(status) > 0:
        log.info("凭据刷新写回 DB slot=%s", slot_id)


# ============================================================================
# admin CRUD（db 源）
# ============================================================================
def db_list_slots() -> List[dict]:
    """列出所有节点的 slot（admin 用），叠加本节点运行时健康态。

    注意：本节点运行时健康态在**同步包装层**先取（此处在 to_thread 工作线程上，
    get_router() 惰性加载会经 _run_sync 到 bridge loop——是顶层调用，不嵌套）。
    绝不能在 bridge 协程 _db_list_slots_rows 里再调 get_router()，否则会在 bridge loop
    上嵌套 _run_sync 造成自锁（协程占着 bridge loop 等一个只能由该 loop 执行的协程）。"""
    node = node_ip()
    from services.claude.registry import get_router
    health = {s.id: s for s in get_router().all_slots()}
    rows = _run_sync(_db_list_slots_rows())
    for v in rows:
        if v["server_ip"] == node:
            s = health.get(v["id"])
            if s is not None:
                v["health"] = s.health.value
                v["routable"] = s.is_routable()
    return rows


async def _db_list_slots_rows() -> List[dict]:
    from utils import db
    rows = await db.fetch(
        """
        SELECT server_ip, slot_id, type, enabled, weight, image, account_email, note,
               (creds_json <> '') AS has_creds, creds_synced_at, updated_at
        FROM claude_slots ORDER BY server_ip, slot_id
        """
    )
    node = node_ip()
    out: List[dict] = []
    for r in rows:
        sip = r["server_ip"] or ""
        sid = r["slot_id"] or ""
        v = {
            "server_ip": sip,
            "id": sid,
            "type": r["type"] or "",
            "enabled": r["enabled"],
            "weight": float(r["weight"]) if r["weight"] is not None else 1.0,
            "image": r["image"] or "",
            "account_email": r["account_email"] or "",
            "note": r["note"] or "",
            "has_creds": r["has_creds"],
            "is_local": sip == node,
            "health": "unknown",  # 本节点健康态由 db_list_slots 同步包装层叠加
            "routable": False,
        }
        out.append(v)
    return out


def db_upsert_slot(server_ip: str, slot_id: str, typ: str, weight: float,
                   image: str, creds_json: str, enabled: bool) -> None:
    """新增/更新一行（按 server_ip+slot_id）。creds_json 为空则不覆盖已有凭据。"""
    _run_sync(_db_upsert_slot(server_ip, slot_id, typ, weight, image, creds_json, enabled))


async def _db_upsert_slot(server_ip: str, slot_id: str, typ: str, weight: float,
                          image: str, creds_json: str, enabled: bool) -> None:
    from utils import db
    if not creds_json:
        await db.execute(
            """
            INSERT INTO claude_slots (server_ip, slot_id, type, enabled, weight, image)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (server_ip, slot_id) DO UPDATE
              SET type=$3, enabled=$4, weight=$5, image=$6, updated_at=now()
            """,
            server_ip, slot_id, typ, enabled, weight, image,
        )
        return
    await db.execute(
        """
        INSERT INTO claude_slots (server_ip, slot_id, type, enabled, weight, image, creds_json, creds_synced_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,now())
        ON CONFLICT (server_ip, slot_id) DO UPDATE
          SET type=$3, enabled=$4, weight=$5, image=$6, creds_json=$7, creds_synced_at=now(), updated_at=now()
        """,
        server_ip, slot_id, typ, enabled, weight, image, creds_json,
    )


def db_set_slot_enabled(server_ip: str, slot_id: str, enabled: bool) -> bool:
    return _run_sync(_db_set_slot_enabled(server_ip, slot_id, enabled))


async def _db_set_slot_enabled(server_ip: str, slot_id: str, enabled: bool) -> bool:
    from utils import db
    status = await db.execute(
        "UPDATE claude_slots SET enabled=$1, updated_at=now() WHERE server_ip=$2 AND slot_id=$3",
        enabled, server_ip, slot_id,
    )
    return _affected(status) > 0


def db_reassign_slot(old_ip: str, slot_id: str, new_ip: str) -> bool:
    """把账号从 old_ip 改分配到 new_ip（改 server_ip）。目标已存在同名则抛错。"""
    return _run_sync(_db_reassign_slot(old_ip, slot_id, new_ip))


async def _db_reassign_slot(old_ip: str, slot_id: str, new_ip: str) -> bool:
    from utils import db
    exists = await db.fetchval(
        "SELECT 1 FROM claude_slots WHERE server_ip=$1 AND slot_id=$2", new_ip, slot_id)
    if exists is not None:
        raise DockerManagerError(f"目标服务器 {new_ip} 已存在账号 {slot_id}")
    status = await db.execute(
        "UPDATE claude_slots SET server_ip=$1, updated_at=now() WHERE server_ip=$2 AND slot_id=$3",
        new_ip, old_ip, slot_id,
    )
    return _affected(status) > 0


def db_delete_slot(server_ip: str, slot_id: str) -> bool:
    return _run_sync(_db_delete_slot(server_ip, slot_id))


async def _db_delete_slot(server_ip: str, slot_id: str) -> bool:
    from utils import db
    status = await db.execute(
        "DELETE FROM claude_slots WHERE server_ip=$1 AND slot_id=$2", server_ip, slot_id)
    return _affected(status) > 0


def db_upsert_login_slot(slot_id: str, creds_dir: str) -> None:
    """网页登录成功后，把本节点该账号插入 claude_slots（creds 从磁盘读）。"""
    node = node_ip()
    if not node:
        raise DockerManagerError("未配 CLAUDE_NODE_IP，无法写入 claude_slots")
    try:
        body = (Path(creds_dir).expanduser() / ".credentials.json").read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise DockerManagerError(f"读凭据失败：{e}") from e
    if not body:
        raise DockerManagerError("读凭据失败：空文件")
    # subscription 必须有镜像才能建容器，用默认镜像（凭据靠挂载，不依赖镜像内预登录）。
    db_upsert_slot(node, slot_id, SlotType.SUBSCRIPTION.value, 1.0,
                   default_subscription_image(), body, True)
