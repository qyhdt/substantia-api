# -*- coding: utf-8 -*-
"""
Claude 容器对外 + admin 接口。

- 用户：POST /claude/chat  —— 路由到该用户固定的 slot 容器跑 claude，返回结果。
- admin：slot 池 CRUD + 健康看板 + 容器拉起/状态/探针。

业务编排见 services/claude/*，设计见 doc/claude-docker-plan.md。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from config.settings import settings
from security.admin import require_admin
from security.dependencies import current_user, require_access_token
from services.claude import db_source
from services.claude import docker_manager as dm
from services.claude import health as health_mod
from services.claude import login as login_mod
from services.claude import registry
from services.claude.router import NoRoutableSlotError
from services.claude.slots import Slot, SlotType

router = APIRouter(prefix="/claude", tags=["claude"])
admin_router = APIRouter(prefix="/admin/claude", tags=["admin-claude"])


def _safe_uid(user: dict) -> str:
    """把认证用户映射成稳定的安全 id（用于 HRW 路由 + 容器内工作目录名）。"""
    raw = str(user.get("id") or user.get("email") or "anon")
    return "u-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# ============================== 用户接口 ==============================
class ChatIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)


@router.post("/chat", dependencies=[Depends(require_access_token)], summary="跑一次 Claude")
async def chat(payload: ChatIn, user: dict = Depends(current_user)):
    uid = _safe_uid(user)
    try:
        res = await asyncio.to_thread(dm.exec_claude, uid, payload.prompt)
    except NoRoutableSlotError as e:
        raise HTTPException(status_code=503, detail=f"no available claude slot: {e}")
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not res.ok:
        # 所有尝试都失败（含鉴权失败）→ 502，附最后输出便于排查
        raise HTTPException(status_code=502, detail={
            "message": "claude exec failed",
            "slot_id": res.slot_id,
            "exit_code": res.exit_code,
            "auth_failed": res.auth_failed,
            "attempts": res.attempts,
            "output": res.output[-2000:],
        })
    return {"slot_id": res.slot_id, "attempts": res.attempts, "output": res.output}


# ============================== admin：slot 池 ==============================
class SlotIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: SlotType = SlotType.SUBSCRIPTION
    enabled: bool = True
    weight: float = Field(default=1.0, gt=0)
    priority: int = Field(default=0, ge=0)
    creds_dir: Optional[str] = None
    image: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)


def _slot_view(s: Slot) -> dict:
    return {
        "id": s.id, "type": s.type.value, "enabled": s.enabled, "weight": s.weight,
        "priority": s.priority, "managed": registry.is_managed_fallback_slot(s.id),
        "creds_dir": s.creds_dir, "image": s.image, "env_keys": sorted(s.env.keys()),
        "health": s.health.value, "cooldown_until": s.cooldown_until,
        "routable": s.is_routable(),
    }


def _reject_managed_fallback(slot_id: str) -> None:
    """环境托管 fallback 只能通过部署配置修改，slot CRUD 不得覆盖。"""
    if registry.is_managed_fallback_slot(slot_id):
        raise HTTPException(
            status_code=400,
            detail=f"{slot_id} 由环境变量托管，请修改 CLAUDE_FALLBACK_* 配置",
        )


@admin_router.get("/slots", dependencies=[Depends(require_admin)], summary="列出 slot + 健康态")
async def list_slots():
    if db_source.slots_source_is_db():
        rows = await asyncio.to_thread(db_source.db_list_slots)
        # DB schema 暂无 priority；已有 DB 业务 slot 均属于最高优先级 0。保留 id
        # 的陈旧 DB 行必须隐藏，由当前进程 settings 合成项取代，且只暴露 env keys。
        rows = [row for row in rows if not registry.is_managed_fallback_slot(row.get("id", ""))]
        for row in rows:
            row["priority"] = 0
            row["managed"] = False
            row.setdefault("env_keys", [])
        node = db_source.node_ip()
        for slot in registry.get_router().all_slots():
            if not registry.is_managed_fallback_slot(slot.id):
                continue
            view = _slot_view(slot)
            view.update({"server_ip": node, "is_local": True})
            rows.append(view)
        return {"slots": rows, "source": "db", "node_ip": db_source.node_ip()}
    return {"slots": [_slot_view(s) for s in registry.get_router().all_slots()], "source": "dir"}


class CreateSlotIn(BaseModel):
    server_ip: str = ""
    slot_id: str = Field(min_length=1, max_length=64)
    type: str = ""
    weight: float = 0.0
    image: str = ""
    creds_json: str = ""
    enabled: Optional[bool] = None


def _refresh_and_ensure(slot_id: str) -> None:
    """db 源改动落到本节点时：热更新 slot 池 + 拉起该 slot 容器（同步，供 to_thread）。"""
    registry.refresh_shared_slots()
    s = registry.get_router().get(slot_id)
    if s is not None:
        try:
            dm.ensure_slot_container(s)
        except dm.DockerManagerError:
            pass


@admin_router.post("/slots", dependencies=[Depends(require_admin)], summary="db 源：新增/更新 slot（可粘贴 creds_json）")
async def create_slot(payload: CreateSlotIn):
    _reject_managed_fallback(payload.slot_id)
    if not db_source.slots_source_is_db():
        raise HTTPException(status_code=400, detail="仅 CLAUDE_SLOTS_SOURCE=db 支持")
    server_ip = payload.server_ip.strip() or db_source.node_ip()
    if not server_ip:
        raise HTTPException(status_code=400, detail="server_ip 必填")
    try:
        dm.assert_safe_id(payload.slot_id, "slot_id")
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    typ = payload.type or SlotType.SUBSCRIPTION.value
    creds_json = payload.creds_json.strip()
    if typ == SlotType.SUBSCRIPTION.value and not creds_json:
        raise HTTPException(status_code=400, detail="subscription 需粘贴 creds_json（.credentials.json 内容）")
    image = payload.image.strip()
    if typ == SlotType.SUBSCRIPTION.value and not image:
        # subscription 空镜像 → 补默认（否则建不了容器）
        image = db_source.default_subscription_image()
    weight = payload.weight if payload.weight > 0 else 1.0
    enabled = True if payload.enabled is None else payload.enabled
    try:
        await asyncio.to_thread(db_source.db_upsert_slot, server_ip, payload.slot_id,
                                typ, weight, image, creds_json, enabled)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    # 归属本节点则热更新 + 拉容器
    if server_ip == db_source.node_ip():
        await asyncio.to_thread(_refresh_and_ensure, payload.slot_id)
    return {"ok": True, "server_ip": server_ip, "slot_id": payload.slot_id}


@admin_router.post("/slots/{slot_id}/enabled", dependencies=[Depends(require_admin)], summary="db 源：启用/禁用 slot")
async def set_slot_enabled(slot_id: str, server_ip: str = "", value: str = "false"):
    _reject_managed_fallback(slot_id)
    if not db_source.slots_source_is_db():
        raise HTTPException(status_code=400, detail="仅 CLAUDE_SLOTS_SOURCE=db 支持")
    enabled = value == "true"
    try:
        ok = await asyncio.to_thread(db_source.db_set_slot_enabled, server_ip.strip(), slot_id, enabled)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="slot not found")
    if server_ip.strip() == db_source.node_ip():
        await asyncio.to_thread(registry.refresh_shared_slots)
    return {"ok": True}


@admin_router.post("/slots/{slot_id}/server", dependencies=[Depends(require_admin)], summary="db 源：把 slot 分配到别的服务器 IP")
async def reassign_slot(slot_id: str, from_: str = Query("", alias="from"), to: str = ""):
    _reject_managed_fallback(slot_id)
    if not db_source.slots_source_is_db():
        raise HTTPException(status_code=400, detail="仅 CLAUDE_SLOTS_SOURCE=db 支持")
    src = from_.strip() or db_source.node_ip()
    dst = to.strip()
    if not dst:
        raise HTTPException(status_code=400, detail="to（目标 server_ip）必填")
    if dst == src:
        return {"ok": True, "unchanged": True}
    try:
        ok = await asyncio.to_thread(db_source.db_reassign_slot, src, slot_id, dst)
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="slot not found")
    node = db_source.node_ip()
    if src == node:  # 从本机移走：摘出池 + 删本机孤儿容器 + 删磁盘凭据
        await asyncio.to_thread(registry.refresh_shared_slots)
        await asyncio.to_thread(dm.stop_slot_container, slot_id, remove=True)
        await asyncio.to_thread(db_source.remove_slot_creds_dir, slot_id)
    if dst == node:  # 分配到本机：接管 + 拉容器
        await asyncio.to_thread(_refresh_and_ensure, slot_id)
    return {"ok": True, "from": src, "to": dst}


@admin_router.put("/slots/{slot_id}", dependencies=[Depends(require_admin)], summary="新增/更新 slot")
async def upsert_slot(slot_id: str, payload: SlotIn):
    if payload.id != slot_id:
        raise HTTPException(status_code=400, detail="path slot_id 与 body.id 不一致")
    _reject_managed_fallback(slot_id)
    r = registry.get_router()
    slots = {s.id: s for s in r.all_slots()}
    slots[payload.id] = Slot(**payload.model_dump())
    registry.save_and_reconfigure(list(slots.values()))
    return {"ok": True, "slot": _slot_view(registry.get_router().get(slot_id))}


@admin_router.delete("/slots/{slot_id}", dependencies=[Depends(require_admin)], summary="删除 slot")
async def delete_slot(slot_id: str, server_ip: str = ""):
    _reject_managed_fallback(slot_id)
    if db_source.slots_source_is_db():
        sip = server_ip.strip() or db_source.node_ip()
        try:
            ok = await asyncio.to_thread(db_source.db_delete_slot, sip, slot_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        if not ok:
            raise HTTPException(status_code=404, detail="slot not found")
        if sip == db_source.node_ip():
            await asyncio.to_thread(registry.refresh_shared_slots)
            await asyncio.to_thread(dm.stop_slot_container, slot_id, remove=True)
            await asyncio.to_thread(db_source.remove_slot_creds_dir, slot_id)  # 删磁盘凭据，防重启 seed 复活
        return {"ok": True, "removed": slot_id, "server_ip": sip}
    r = registry.get_router()
    remaining = [s for s in r.all_slots() if s.id != slot_id]
    if len(remaining) == len(r.all_slots()):
        raise HTTPException(status_code=404, detail="slot not found")
    registry.save_and_reconfigure(remaining)
    return {"ok": True, "removed": slot_id}


# ============================== admin：容器 ==============================
@admin_router.get("/containers", dependencies=[Depends(require_admin)], summary="列出 slot 容器")
async def list_containers():
    if not await asyncio.to_thread(dm.is_docker_reachable):
        raise HTTPException(status_code=503, detail="docker daemon 不可达")
    return {"containers": await asyncio.to_thread(dm.list_slot_containers)}


@admin_router.post("/containers/ensure", dependencies=[Depends(require_admin)], summary="拉起所有 enabled slot 容器")
async def ensure_containers():
    return {"results": await asyncio.to_thread(dm.ensure_all_enabled)}


@admin_router.post("/slots/{slot_id}/probe", dependencies=[Depends(require_admin)], summary="对某 slot 跑一次健康探针")
async def probe_slot(slot_id: str):
    slot = registry.get_router().get(slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="slot not found")
    res = await asyncio.to_thread(health_mod.probe_and_update, slot)
    return {"slot_id": res.slot_id, "healthy": res.healthy, "detail": res.detail}


# ============================== admin：交互式登录（网页终端 xterm ↔ PTY，轮询传输） ==============================
class LoginStartIn(BaseModel):
    account_id: str


class LoginWriteIn(BaseModel):
    session_id: str
    data: str  # 前端 xterm 的原始按键


class LoginSessIn(BaseModel):
    session_id: str


@admin_router.post("/login/start", dependencies=[Depends(require_admin)], summary="起一个交互式登录会话（新增订阅账号）")
async def login_start(payload: LoginStartIn):
    _reject_managed_fallback(payload.account_id)
    try:
        sid = await asyncio.to_thread(login_mod.start_login, payload.account_id)
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"session_id": sid, "account_id": payload.account_id}


@admin_router.get("/login/read", dependencies=[Depends(require_admin)], summary="轮询：从 offset 起返回新输出（base64）")
async def login_read(session_id: str, offset: int = 0):
    try:
        data, new_offset, exited, creds_ready = await asyncio.to_thread(
            login_mod.read_login, session_id, offset)
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "offset": new_offset,
        "exited": exited,
        "creds_ready": creds_ready,
    }


@admin_router.post("/login/write", dependencies=[Depends(require_admin)], summary="把前端按键写进登录终端")
async def login_write(payload: LoginWriteIn):
    try:
        await asyncio.to_thread(login_mod.write_login, payload.session_id,
                                payload.data.encode("utf-8"))
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@admin_router.post("/login/finish", dependencies=[Depends(require_admin)], summary="收尾：凭据已写出则收编成 slot")
async def login_finish(payload: LoginSessIn):
    try:
        out = await asyncio.to_thread(login_mod.finish_login, payload.session_id)
    except dm.DockerManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


@admin_router.post("/login/cancel", dependencies=[Depends(require_admin)], summary="放弃一个登录会话")
async def login_cancel(payload: LoginSessIn):
    await asyncio.to_thread(login_mod.cancel_login, payload.session_id)
    return {"ok": True}
