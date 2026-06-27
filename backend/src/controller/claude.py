# -*- coding: utf-8 -*-
"""
Claude 容器对外 + admin 接口。

- 用户：POST /claude/chat  —— 路由到该用户固定的 slot 容器跑 claude，返回结果。
- admin：slot 池 CRUD + 健康看板 + 容器拉起/状态/探针。

业务编排见 services/claude/*，设计见 doc/claude-docker-plan.md。
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from security.admin import require_admin
from security.dependencies import current_user, require_access_token
from services.claude import docker_manager as dm
from services.claude import health as health_mod
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
    creds_dir: Optional[str] = None
    image: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)


def _slot_view(s: Slot) -> dict:
    return {
        "id": s.id, "type": s.type.value, "enabled": s.enabled, "weight": s.weight,
        "creds_dir": s.creds_dir, "image": s.image, "env_keys": sorted(s.env.keys()),
        "health": s.health.value, "cooldown_until": s.cooldown_until,
        "routable": s.is_routable(),
    }


@admin_router.get("/slots", dependencies=[Depends(require_admin)], summary="列出 slot + 健康态")
async def list_slots():
    return {"slots": [_slot_view(s) for s in registry.get_router().all_slots()]}


@admin_router.put("/slots/{slot_id}", dependencies=[Depends(require_admin)], summary="新增/更新 slot")
async def upsert_slot(slot_id: str, payload: SlotIn):
    if payload.id != slot_id:
        raise HTTPException(status_code=400, detail="path slot_id 与 body.id 不一致")
    r = registry.get_router()
    slots = {s.id: s for s in r.all_slots()}
    slots[payload.id] = Slot(**payload.model_dump())
    registry.save_and_reconfigure(list(slots.values()))
    return {"ok": True, "slot": _slot_view(registry.get_router().get(slot_id))}


@admin_router.delete("/slots/{slot_id}", dependencies=[Depends(require_admin)], summary="删除 slot")
async def delete_slot(slot_id: str):
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
