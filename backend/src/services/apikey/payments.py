# -*- coding: utf-8 -*-
"""
Polar.sh 自助充值（海外 MoR，收美元）。移植自 digital-platform 的 Polar 流程，适配 AK 余额（micro-USD）。

流程：
  POST /api/portal/recharge {usd}  →  create_checkout → 落 ak_payments(pending) + 调 Polar 建结账 → 返回 url（前端跳转）
  Polar webhook → handle_webhook：Standard Webhooks 验签 → order.paid → 按 out_trade_no 幂等加余额。

幂等：ak_payments.out_trade_no 唯一；只在 pending→paid 这一跳里加余额。
共用 Polar 账号：metadata 带 out_trade_no（substantia 专属前缀），非本系统的事件（如 digital-platform）
查不到对应 ak_payments → 直接忽略，安全。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Optional

import httpx
from fastapi import HTTPException, status

from config.settings import settings
from services.apikey import users as users_svc
from utils import db as db_util

log = logging.getLogger("ak.payments")

MIN_USD = 1.0
MAX_USD = 10000.0

# 充值阶梯赠送（充值越多送越多）。每档 (实付门槛$, 赠送$)，降序排列；
# 按实付额匹配「不超过它的最高一档」赠送。赠送进永久余额（balance），与实付同桶、不过期。
RECHARGE_BONUS_TIERS = [(100.0, 25.0), (50.0, 10.0), (20.0, 2.0)]


def recharge_bonus_micro(paid_micro: int) -> int:
    """按实付额匹配赠送档，返回赠送的微美元（无匹配档=0）。"""
    paid_usd = int(paid_micro) / 1_000_000
    for threshold_usd, bonus_usd in RECHARGE_BONUS_TIERS:
        if paid_usd + 1e-9 >= threshold_usd:
            return int(round(bonus_usd * 1_000_000))
    return 0


def bonus_tiers() -> list[dict]:
    """供前端展示的赠送档（按门槛升序：充 threshold_usd 送 bonus_usd）。"""
    tiers = [{"threshold_usd": th, "bonus_usd": b} for th, b in RECHARGE_BONUS_TIERS]
    return sorted(tiers, key=lambda x: x["threshold_usd"])


def configured() -> bool:
    return bool(settings.POLAR_ACCESS_TOKEN and settings.POLAR_PRODUCT_ID)


def _polar_api() -> str:
    return "https://sandbox-api.polar.sh" if settings.POLAR_SANDBOX else "https://api.polar.sh"


def _new_out_trade_no(user_id: int) -> str:
    # substantia 专属前缀，便于和共用 Polar 账号的其它项目区分
    return f"sa_{user_id}_{secrets.token_hex(8)}"


async def create_checkout(user_id: int, email: Optional[str], usd: float) -> dict:
    """建 Polar 结账，返回 {url}。usd 范围校验 + 落库 pending 订单。"""
    if not configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "充值未接入（Polar 未配置）")
    if usd is None or usd < MIN_USD or usd > MAX_USD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"金额需在 ${MIN_USD:.0f}–${MAX_USD:.0f}")

    otn = _new_out_trade_no(user_id)
    micro = int(round(usd * 1_000_000))
    await db_util.execute(
        "INSERT INTO ak_payments (user_id, provider, out_trade_no, amount_micro_usd) "
        "VALUES ($1, 'polar', $2, $3)",
        user_id, otn, micro,
    )

    payload = {
        "products": [settings.POLAR_PRODUCT_ID],
        "amount": round(usd * 100),  # 分；product 须配成 pay-what-you-want
        "customer_email": email or None,
        "success_url": settings.PAYMENT_RETURN_URL,
        # ⚠️ 共用 Polar 账号：故意**不放** bare `user_id`。digital-platform 的 webhook 一看到
        # metadata.user_id 就给它自己的同号 vibe 用户加款（不校验订单归属）。我们只放 out_trade_no，
        # 本系统 webhook 按订单号查 ak_payments 取 user_id；dp 看到没 user_id 直接忽略 → 互不串。
        "metadata": {"app": "substantia-api", "out_trade_no": otn, "sa_user_id": str(user_id)},
    }
    headers = {
        "Authorization": f"Bearer {settings.POLAR_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(f"{_polar_api()}/v1/checkouts/", headers=headers, json=payload)
        if r.status_code >= 300:
            log.warning("polar checkout failed %s: %s", r.status_code, r.text[:400])
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "创建结账失败，请稍后再试")
        return {"url": r.json()["url"], "out_trade_no": otn}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("polar checkout error: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "创建结账失败，请稍后再试")


def verify_signature(headers, raw: bytes, tolerance_sec: int = 300) -> bool:
    """Standard Webhooks 验签：signed = "{id}.{ts}.{body}"，sig=base64(HMAC-SHA256(key, signed))。"""
    secret = settings.POLAR_WEBHOOK_SECRET
    if not secret:
        return False
    msg_id = headers.get("webhook-id", "")
    ts = headers.get("webhook-timestamp", "")
    sig_header = headers.get("webhook-signature", "")
    if not (msg_id and ts and sig_header):
        return False
    try:
        if abs(int(time.time()) - int(ts)) > tolerance_sec:
            return False
    except ValueError:
        return False
    key = base64.b64decode(secret[len("whsec_"):]) if secret.startswith("whsec_") else secret.encode()
    signed = f"{msg_id}.{ts}.{raw.decode()}".encode()
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in sig_header.split(" "):
        if "," in part and hmac.compare_digest(part.split(",", 1)[1], expected):
            return True
    return False


async def handle_webhook(headers, raw: bytes) -> dict:
    """验签 + 解析 + 幂等加余额。返回处理结果（始终 200，避免 Polar 重投风暴）。"""
    if not configured():
        return {"ignored": "not configured"}
    if not verify_signature(headers, raw):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid signature")

    try:
        event = json.loads(raw.decode())
    except Exception:
        return {"ignored": "bad json"}

    etype = event.get("type") or ""
    data = event.get("data") or {}
    # 只认已支付：order.paid，或 checkout.updated 且状态成功
    paid = etype == "order.paid" or (
        etype == "checkout.updated" and str(data.get("status") or "").lower() in ("succeeded", "confirmed")
    )
    if not paid:
        return {"ignored": f"event {etype}"}

    meta = data.get("metadata") or {}
    otn = meta.get("out_trade_no")
    if not otn:
        return {"ignored": "no out_trade_no"}

    row = await db_util.fetchrow("SELECT * FROM ak_payments WHERE out_trade_no = $1", otn)
    if not row:
        return {"ignored": "unknown order (not ours)"}  # 共用 Polar 账号的别的项目事件
    if row["status"] == "paid":
        return {"already": True, "out_trade_no": otn}

    # 幂等加款：仅 pending→paid 这一跳成功者加余额
    amount = await db_util.fetchval(
        "UPDATE ak_payments SET status = 'paid', paid_at = now(), provider_ref = $2 "
        "WHERE out_trade_no = $1 AND status = 'pending' RETURNING amount_micro_usd",
        otn, str(data.get("id") or ""),
    )
    if amount is None:
        return {"already": True, "out_trade_no": otn}  # 并发竞态，别人已处理

    # 实付 + 阶梯赠送，一并加进永久余额（赠送基于实付额，不含上一次的赠送）
    bonus = recharge_bonus_micro(int(amount))
    new_bal = await users_svc.adjust_balance(int(row["user_id"]), int(amount) + bonus)
    await users_svc.set_full_model_access(int(row["user_id"]), True)

    # 充值达标且试用仍在有效期内 → 把剩余试用额度转为永久有效（按实付额判定，不含赠送）
    if int(amount) >= settings.AK_TRIAL_ACTIVATE_MIN_MICRO_USD:
        await db_util.execute(
            "UPDATE ak_users SET trial_permanent = true "
            "WHERE id = $1 AND trial_micro_usd > 0 AND NOT trial_permanent "
            "AND trial_expires_at IS NOT NULL AND trial_expires_at > now()",
            int(row["user_id"]),
        )

    log.info("polar recharge ok user=%s otn=%s paid=%d bonus=%d micro, balance=%d",
             row["user_id"], otn, int(amount), bonus, new_bal)
    return {"granted": True, "out_trade_no": otn, "bonus_micro_usd": bonus,
            "balance_micro_usd": new_bal}


async def list_for_user(user_id: int, limit: int = 50, offset: int = 0) -> dict:
    """分页：返回 {items, total}。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total = await db_util.fetchval("SELECT count(*) FROM ak_payments WHERE user_id = $1", user_id)
    rows = await db_util.fetch(
        "SELECT id, provider, out_trade_no, amount_micro_usd, amount_rmb, status, created_at, paid_at "
        "FROM ak_payments WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        user_id, limit, offset,
    )
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}


async def list_all(limit: int = 50, offset: int = 0, status: str | None = None) -> dict:
    """admin：全站充值订单（自助 Polar/虎皮椒），带用户邮箱。分页 {items, total}。
    status 可选过滤（pending/paid）。金额是 micro-USD，虎皮椒另带 amount_rmb 对账列。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where = ""
    args: list = []
    if status:
        args.append(status)
        where = f"WHERE p.status = ${len(args)}"
    total = await db_util.fetchval(f"SELECT count(*) FROM ak_payments p {where}", *args)
    rows = await db_util.fetch(
        "SELECT p.id, p.user_id, u.email AS user_email, p.provider, p.out_trade_no, "
        "p.amount_micro_usd, p.amount_rmb, p.status, p.provider_ref, p.created_at, p.paid_at "
        "FROM ak_payments p JOIN ak_users u ON u.id = p.user_id "
        f"{where} ORDER BY p.created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
        *args, limit, offset,
    )
    return {"items": [dict(r) for r in rows], "total": int(total or 0)}
