# -*- coding: utf-8 -*-
"""
管理端 /api/admin/*（需 admin）：
- 充值审核、用户管理、key 管理、模型定价
- 上游 slot 管理（= sub 认证 / api_key 渠道 / 容器），直接操作容器团队的 services.claude

slot 池的真源是 services.claude（slots.json + 进程内 router）；这里只是它的 HTTP 管理面。
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from config.settings import settings
from security.admin import require_admin
from services.apikey import keys as keys_svc
from services.apikey import fx
from services.apikey import moxing_accounting as moxing_acct
from services.apikey import payments as payments_svc
from services.apikey import pricing as pricing_svc
from services.apikey import topups as topups_svc
from services.apikey import usage as usage_svc
from services.apikey import users as users_svc
from utils.pm_logger import get_app_logger

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
log = get_app_logger()


async def _owner_guard(admin: dict, target_user_id: int) -> None:
    """保护「所有者」账号：非所有者的 admin 不能管理所有者账号。
    未配置 OWNER_EMAILS 时不启用；请求者本身是所有者则放行。命中则 403。
    对应 Go handlers/admin_apikey.go 的 ownerGuard。"""
    owners = {e.lower() for e in settings.owner_emails_list}
    if not owners:
        return
    if (admin.get("email") or "").strip().lower() in owners:
        return
    u = await users_svc.get_user(target_user_id)
    if u and (u.get("email") or "").strip().lower() in owners:
        raise HTTPException(status_code=403, detail="not permitted on this account")


# ============================== 充值审核 ==============================
class ReviewIn(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("/topups", summary="充值申请列表")
async def list_topups(status: Optional[str] = None):
    return await topups_svc.list_all(status)


@router.get("/payments", summary="全站充值订单列表（自助 Polar/虎皮椒，带用户邮箱）")
async def list_payments(limit: int = 50, offset: int = 0, status: Optional[str] = None):
    return await payments_svc.list_all(limit=limit, offset=offset, status=status)


@router.post("/topups/{topup_id}/review", summary="审核充值（批准即加余额）")
async def review_topup(topup_id: int, payload: ReviewIn, admin: dict = Depends(require_admin)):
    return await topups_svc.review(
        topup_id, approve=payload.approve, reviewer_id=int(admin["id"]), note=payload.note
    )


# ============================== 用户管理 ==============================
class GrantIn(BaseModel):
    amount_usd: float = Field(description="可负数（扣减）")


class CreateUserIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(default="", max_length=128)  # 空则用默认密码 123456
    role: str = Field(default="user")
    balance_usd: float = Field(default=0)


class MultiplierIn(BaseModel):
    multiplier: float = Field(ge=0, le=100, description="价格系数：实扣=模型价×系数")


@router.get("/users", summary="用户列表")
async def list_users(admin: dict = Depends(require_admin)):
    out = await users_svc.list_users()
    # 所有者账号对非所有者 admin 隐藏。
    owners = {e.lower() for e in settings.owner_emails_list}
    if owners and (admin.get("email") or "").strip().lower() not in owners:
        out = [u for u in out if (u.get("email") or "").strip().lower() not in owners]
    return out


@router.post("/users", summary="后台新建用户（默认密码 123456，首登强制改密）")
async def create_user(payload: CreateUserIn):
    from services.apikey import to_micro
    return await users_svc.create_user_by_admin(
        payload.email, payload.password, payload.role, to_micro(payload.balance_usd)
    )


@router.post("/users/{user_id}/multiplier", summary="设置用户价格系数（实扣=模型价×系数）")
async def set_multiplier(user_id: int, payload: MultiplierIn, admin: dict = Depends(require_admin)):
    await _owner_guard(admin, user_id)
    ok = await users_svc.set_price_multiplier(user_id, payload.multiplier)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True, "user_id": user_id, "multiplier": payload.multiplier}


@router.post("/users/{user_id}/full-model-access", summary="设置全模型权限标签")
async def set_full_model_access(user_id: int, enabled: bool, admin: dict = Depends(require_admin)):
    await _owner_guard(admin, user_id)
    ok = await users_svc.set_full_model_access(user_id, enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True, "user_id": user_id, "full_model_access": enabled}


@router.get("/users/{user_id}/detail", summary="单用户详情：余额分桶 + 消费聚合 + 用量明细")
async def user_detail(user_id: int, admin: dict = Depends(require_admin)):
    await _owner_guard(admin, user_id)
    detail = await users_svc.user_detail(user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="user not found")
    exchange = await fx.current_usd_cny()
    detail.update({
        "rmb_per_usd": exchange["rate"],
        "rmb_rate_date": exchange["date"],
        "rmb_rate_source": exchange["source"],
        "rmb_rate_live": exchange["live"],
    })
    return detail


@router.post("/users/{user_id}/grant", summary="手动调整用户余额")
async def grant(user_id: int, payload: GrantIn, admin: dict = Depends(require_admin)):
    from services.apikey import to_micro
    await _owner_guard(admin, user_id)
    new_bal = await users_svc.adjust_balance(user_id, to_micro(payload.amount_usd))
    return {"user_id": user_id, "balance_micro_usd": new_bal}


@router.post("/users/{user_id}/role", summary="设角色 user|admin")
async def set_role(user_id: int, role: str, admin: dict = Depends(require_admin)):
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role must be user|admin")
    await _owner_guard(admin, user_id)
    ok = await users_svc.set_role(user_id, role)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@router.post("/users/{user_id}/status", summary="启用/禁用用户 active|disabled")
async def set_user_status(user_id: int, status: str, admin: dict = Depends(require_admin)):
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status must be active|disabled")
    await _owner_guard(admin, user_id)
    ok = await users_svc.set_status(user_id, status)
    if not ok:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


# ============================== Key 管理 ==============================
class AdminCreateKeyIn(BaseModel):
    user_id: int
    name: str = Field(default="admin-issued", max_length=64)
    allowed_models: Optional[List[str]] = None
    quota_cap_usd: Optional[float] = None
    rate_limit_rpm: Optional[int] = None


@router.post("/keys", summary="给指定用户签发 key")
async def admin_issue_key(payload: AdminCreateKeyIn, admin: dict = Depends(require_admin)):
    from services.apikey import to_micro
    await _owner_guard(admin, payload.user_id)
    cap = to_micro(payload.quota_cap_usd) if payload.quota_cap_usd is not None else None
    issued = await keys_svc.issue_key(
        payload.user_id, name=payload.name, allowed_models=payload.allowed_models,
        quota_cap_micro_usd=cap, rate_limit_rpm=payload.rate_limit_rpm,
    )
    return {"api_key": issued["plain"], "info": issued["key"]}


@router.post("/keys/{key_id}/status", summary="改 key 状态 active|disabled|revoked")
async def admin_key_status(key_id: int, status: str):
    if status not in ("active", "disabled", "revoked"):
        raise HTTPException(status_code=400, detail="bad status")
    ok = await keys_svc.set_status(key_id, status)
    if not ok:
        raise HTTPException(status_code=404, detail="key not found")
    return {"ok": True}


# ============================== 模型定价 ==============================
class PriceIn(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    display_name: Optional[str] = None
    input_micro_usd_per_1k: int = Field(ge=0)
    output_micro_usd_per_1k: int = Field(ge=0)
    # 缓存价可选；不传则按官方比例从输入价派生（read 10% / write 125%）。
    cache_read_micro_usd_per_1k: Optional[int] = Field(default=None, ge=0)
    cache_write_micro_usd_per_1k: Optional[int] = Field(default=None, ge=0)
    enabled: bool = True


@router.get("/model-prices", summary="模型定价列表")
async def list_prices():
    return await pricing_svc.list_prices(include_supplier_terms=True)


@router.post("/model-prices", summary="新增/更新模型定价")
async def upsert_price(payload: PriceIn):
    if await moxing_acct.is_managed_model(payload.model):
        raise HTTPException(status_code=409, detail="该模型由墨行对账页的官网价与销售折扣统一管理")
    return await pricing_svc.upsert_price(
        payload.model, display_name=payload.display_name,
        input_micro_usd_per_1k=payload.input_micro_usd_per_1k,
        output_micro_usd_per_1k=payload.output_micro_usd_per_1k,
        cache_read_micro_usd_per_1k=payload.cache_read_micro_usd_per_1k,
        cache_write_micro_usd_per_1k=payload.cache_write_micro_usd_per_1k,
        enabled=payload.enabled,
    )


# ============================== 用量看板 ==============================
@router.get("/usage/summary", summary="用量看板聚合")
async def usage_summary(days: int = 7):
    result = await usage_svc.admin_summary(days=days)
    exchange = await fx.current_usd_cny()
    result.update({
        "rmb_per_usd": exchange["rate"],
        "rmb_rate_date": exchange["date"],
        "rmb_rate_source": exchange["source"],
        "rmb_rate_live": exchange["live"],
    })
    return result


@router.get("/usage/details", summary="全站调用明细（邮箱与日期筛选）")
async def usage_details(email: Optional[str] = None, start_date: Optional[date] = None,
                        end_date: Optional[date] = None, limit: int = 50, offset: int = 0):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    return await usage_svc.admin_usage_details(
        email=email, start_date=start_date, end_date=end_date, limit=limit, offset=offset,
    )


@router.get("/usage/details/export", summary="按当前筛选导出全站调用明细 Excel")
async def export_usage_details(email: Optional[str] = None, start_date: Optional[date] = None,
                               end_date: Optional[date] = None, currency: str = "RMB"):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    code = currency.strip().upper()
    if code not in {"RMB", "USD"}:
        raise HTTPException(status_code=422, detail="currency must be RMB or USD")
    result = await usage_svc.admin_usage_details(
        email=email, start_date=start_date, end_date=end_date, limit=100_000, offset=0, export=True,
    )
    exchange = await fx.current_usd_cny()
    from services.apikey.xlsx_export import usage_details_xlsx
    content = usage_details_xlsx(
        result["items"], currency=code, rmb_per_usd=float(exchange["rate"]),
    )
    filename = f"usage-details-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================== 墨行资金 / 成本 / 销售对账 ==============================
class MoxingMoneyIn(BaseModel):
    amount: Decimal
    currency: str = Field(default="RMB", pattern="^(?i:USD|RMB)$")
    reference: Optional[str] = Field(default=None, max_length=128)
    note: Optional[str] = Field(default=None, max_length=500)


class MoxingTermsIn(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)
    official_input_cny_per_million: Decimal = Field(ge=0)
    official_output_cny_per_million: Decimal = Field(ge=0)
    official_cache_read_cny_per_million: Decimal = Field(ge=0)
    official_cache_write_cny_per_million: Decimal = Field(ge=0)
    supplier_multiplier: Decimal = Field(ge=0, le=100)
    sale_multiplier: Decimal = Field(ge=0, le=100)


@router.get("/moxing/accounting", summary="墨行余额、成本、销售额与利润对账")
async def moxing_accounting(days: int = 30, limit: int = 100):
    result = await moxing_acct.accounting_summary(days=days, limit=limit)
    exchange = await fx.current_usd_cny()
    result.update({
        "rmb_per_usd": exchange["rate"],
        "rmb_rate_date": exchange["date"],
        "rmb_rate_source": exchange["source"],
        "rmb_rate_live": exchange["live"],
    })
    return result


@router.post("/moxing/topups", summary="登记墨行充值（不可修改；错账用调整项冲正）")
async def moxing_topup(payload: MoxingMoneyIn, admin: dict = Depends(require_admin)):
    try:
        return await moxing_acct.add_funds(
            amount=payload.amount, currency=payload.currency, entry_type="topup",
            admin_id=int(admin["id"]), reference=payload.reference, note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/moxing/adjustments", summary="登记墨行余额调整（正数增加、负数减少）")
async def moxing_adjustment(payload: MoxingMoneyIn, admin: dict = Depends(require_admin)):
    try:
        return await moxing_acct.add_funds(
            amount=payload.amount, currency=payload.currency, entry_type="adjustment",
            admin_id=int(admin["id"]), reference=payload.reference, note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/moxing/terms/{model}", summary="调整墨行商务成本折扣与本站销售折扣")
async def moxing_terms(model: str, payload: MoxingTermsIn, admin: dict = Depends(require_admin)):
    try:
        return await moxing_acct.update_terms(
            model=model, display_name=payload.display_name,
            official_input=payload.official_input_cny_per_million,
            official_output=payload.official_output_cny_per_million,
            official_cache_read=payload.official_cache_read_cny_per_million,
            official_cache_write=payload.official_cache_write_cny_per_million,
            supplier_multiplier=payload.supplier_multiplier,
            sale_multiplier=payload.sale_multiplier,
            admin_id=int(admin["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ============================== 上游 slot / 容器 ==============================
# 上游凭据(slot=sub/api_key) + 容器 + 健康看板由容器团队的 controller/claude.py 提供：
#   GET/PUT/DELETE /api/admin/claude/slots[...]、/api/admin/claude/containers[...]
# 本文件不重复实现，前端「上游凭据 / 容器编排」页直接对接那批接口。
