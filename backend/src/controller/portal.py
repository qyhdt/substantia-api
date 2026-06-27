# -*- coding: utf-8 -*-
"""
用户端门户 /api/portal/*（需 JWT 鉴权）：余额、key 自助管理、用量、充值申请。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from security.dependencies import current_user, require_access_token
from services.apikey import to_micro, usd
from services.apikey import keys as keys_svc
from services.apikey import payments as payments_svc
from services.apikey import topups as topups_svc
from services.apikey import usage as usage_svc
from services.apikey import users as users_svc

router = APIRouter(prefix="/portal", tags=["portal"], dependencies=[Depends(require_access_token)])


def _uid(user: dict) -> int:
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="bad user context")


class CreateKeyIn(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=64)
    allowed_models: Optional[List[str]] = None


class TopupIn(BaseModel):
    amount_usd: float = Field(gt=0, le=100000)
    reason: Optional[str] = Field(default=None, max_length=500)


class RechargeIn(BaseModel):
    amount_usd: float = Field(ge=1, le=10000)


@router.get("/me", summary="账户 + 余额概览")
async def me(user: dict = Depends(current_user)):
    u = await users_svc.get_user(_uid(user))
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    return {**u, "balance_usd": usd(u["balance_micro_usd"])}


@router.get("/keys", summary="我的 key 列表（脱敏）")
async def my_keys(user: dict = Depends(current_user)):
    return await keys_svc.list_keys(_uid(user))


@router.post("/keys", summary="自助新建 key（明文仅返回一次）")
async def new_key(payload: CreateKeyIn, user: dict = Depends(current_user)):
    issued = await keys_svc.issue_key(
        _uid(user), name=payload.name, allowed_models=payload.allowed_models
    )
    return {"api_key": issued["plain"], "info": issued["key"]}


@router.post("/keys/{key_id}/disable", summary="禁用我的 key")
async def disable_key(key_id: int, user: dict = Depends(current_user)):
    ok = await keys_svc.set_status(key_id, "disabled", user_id=_uid(user))
    if not ok:
        raise HTTPException(status_code=404, detail="key not found")
    return {"ok": True}


@router.delete("/keys/{key_id}", summary="删除我的 key")
async def delete_key(key_id: int, user: dict = Depends(current_user)):
    ok = await keys_svc.delete_key(key_id, user_id=_uid(user))
    if not ok:
        raise HTTPException(status_code=404, detail="key not found")
    return {"ok": True}


@router.get("/keys/{key_id}/usage", summary="某 key 的用量明细")
async def key_usage(key_id: int, user: dict = Depends(current_user)):
    return await usage_svc.usage_for_key(key_id, _uid(user))


@router.get("/usage", summary="我的用量明细（分页）")
async def my_usage(limit: int = 50, offset: int = 0, user: dict = Depends(current_user)):
    return await usage_svc.usage_for_user(_uid(user), limit, offset)


@router.get("/topups", summary="我的充值申请列表")
async def my_topups(user: dict = Depends(current_user)):
    return await topups_svc.list_for_user(_uid(user))


@router.post("/topups", summary="提交加额度/充值申请（admin 审核，备用）")
async def submit_topup(payload: TopupIn, user: dict = Depends(current_user)):
    return await topups_svc.submit(_uid(user), to_micro(payload.amount_usd), payload.reason)


# ---------- 自助充值（Polar）----------
@router.get("/recharge/enabled", summary="充值渠道是否可用")
async def recharge_enabled():
    return {"enabled": payments_svc.configured(), "provider": "polar"}


@router.post("/recharge", summary="自助充值：创建 Polar 结账，返回跳转 URL")
async def recharge(payload: RechargeIn, user: dict = Depends(current_user)):
    u = await users_svc.get_user(_uid(user))
    email = (u or {}).get("email")
    return await payments_svc.create_checkout(_uid(user), email, payload.amount_usd)


@router.get("/payments", summary="我的充值订单（分页）")
async def my_payments(limit: int = 50, offset: int = 0, user: dict = Depends(current_user)):
    return await payments_svc.list_for_user(_uid(user), limit, offset)
