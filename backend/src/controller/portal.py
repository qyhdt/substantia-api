# -*- coding: utf-8 -*-
"""
用户端门户 /api/portal/*（需 JWT 鉴权）：余额、key 自助管理、用量、充值申请。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from pydantic import BaseModel, Field

from security.dependencies import current_user, require_access_token
from services.apikey import to_micro, usd
from services.apikey import keys as keys_svc
from services.apikey import payments as payments_svc
from services.apikey import pricing as pricing_svc
from services.apikey import topups as topups_svc
from services.apikey import usage as usage_svc
from services.apikey import users as users_svc
from services.apikey import xunhupay as xunhupay_svc

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
    proof_url: Optional[str] = Field(default=None, max_length=300)  # 转账凭证图片地址


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class RechargeIn(BaseModel):
    amount_usd: float = Field(ge=1, le=10000)


@router.get("/me", summary="账户 + 余额概览")
async def me(user: dict = Depends(current_user)):
    from services.apikey.balance import effective_balance, trial_active
    u = await users_svc.get_user(_uid(user))
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    eff = effective_balance(u)
    active = trial_active(u)
    return {
        **u,
        "paid_micro_usd": u["balance_micro_usd"],   # 实付桶
        "balance_micro_usd": eff,                   # 有效总额（前端展示）
        "balance_usd": usd(eff),
        "trial_active": active,                     # 试用是否有效
        "trial_usd": usd(u.get("trial_micro_usd") or 0) if active else "$0.0000",
    }


@router.get("/prices", summary="模型价格表（启用中的，供控制台随时查看）")
async def my_prices():
    """返回启用中的逐模型价格（micro-USD/1k token）。前端换算成 $/百万 token 展示。"""
    rows = await pricing_svc.list_prices()
    return [r for r in rows if r.get("enabled", True)]


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
async def my_usage(
    limit: int = 50, offset: int = 0, days: Optional[int] = None,
    user: dict = Depends(current_user),
):
    return await usage_svc.usage_for_user(_uid(user), limit, offset, days)


@router.get("/billing/summary", summary="我的账单聚合（总览 / 按日 / 按模型）")
async def my_billing_summary(days: int = 7, user: dict = Depends(current_user)):
    result = await usage_svc.billing_summary(_uid(user), days)
    result["rmb_per_usd"] = xunhupay_svc._rmb_per_usd()
    return result


@router.get("/topups", summary="我的充值申请列表")
async def my_topups(user: dict = Depends(current_user)):
    return await topups_svc.list_for_user(_uid(user))


@router.post("/topups", summary="提交加额度/充值申请（admin 审核，备用）")
async def submit_topup(payload: TopupIn, user: dict = Depends(current_user)):
    return await topups_svc.submit(
        _uid(user), to_micro(payload.amount_usd), payload.reason, proof_url=payload.proof_url
    )


@router.post("/change-password", summary="自助改密（首次登录强制改密也走这里）")
async def change_password(payload: ChangePasswordIn, user: dict = Depends(current_user)):
    await users_svc.change_password(_uid(user), payload.old_password, payload.new_password)
    return {"ok": True}


@router.post("/uploads/proof", summary="上传转账凭证图片，返回可回读的相对 URL")
async def upload_proof(file: UploadFile = File(...), user: dict = Depends(current_user)):
    from controller.uploads import save_proof
    _uid(user)  # 确保有合法登录上下文
    url = await save_proof(file)
    return {"url": url}


# ---------- 自助充值（Polar 信用卡 / 虎皮椒 微信·支付宝）----------
@router.get("/recharge/enabled", summary="充值渠道是否可用 + 赠送档")
async def recharge_enabled():
    polar_on = payments_svc.configured()
    xunhupay_on = xunhupay_svc.configured()
    methods = []
    if polar_on:
        methods.append({"id": "polar", "currency": "usd", "label_zh": "信用卡", "label_en": "Credit Card"})
    if xunhupay_on:
        methods.append({"id": "xunhupay", "currency": "cny", "label_zh": "微信 / 支付宝", "label_en": "WeChat / Alipay"})
    return {
        "enabled": polar_on,                 # 向后兼容：老前端只看 Polar
        "provider": "polar",
        "xunhupay_enabled": xunhupay_on,
        "methods": methods,
        "bonus_tiers": payments_svc.bonus_tiers(),
        "rmb_per_usd": xunhupay_svc._rmb_per_usd(),
    }


@router.post("/recharge", summary="自助充值：创建 Polar 结账，返回跳转 URL")
async def recharge(payload: RechargeIn, user: dict = Depends(current_user)):
    u = await users_svc.get_user(_uid(user))
    email = (u or {}).get("email")
    return await payments_svc.create_checkout(_uid(user), email, payload.amount_usd)


@router.post("/recharge/xunhupay", summary="自助充值：创建虎皮椒结账（微信/支付宝），返回收银台 URL")
async def recharge_xunhupay(payload: RechargeIn, user: dict = Depends(current_user)):
    return await xunhupay_svc.create_checkout(_uid(user), payload.amount_usd)


@router.get("/payments", summary="我的充值订单（分页）")
async def my_payments(limit: int = 50, offset: int = 0, user: dict = Depends(current_user)):
    return await payments_svc.list_for_user(_uid(user), limit, offset)
