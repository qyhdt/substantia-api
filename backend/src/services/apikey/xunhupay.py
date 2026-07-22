# -*- coding: utf-8 -*-
"""
虎皮椒 xunhupay 自助充值（国内个人收款，微信/支付宝，收人民币）。

与 Polar 同一套架子：复用 ak_payments 挂单（provider='xunhupay'）+ users.adjust_balance 到账，
共用 payments.recharge_bonus_micro 的阶梯赠送。差异只在下单/验签方式：
  - 下单 POST {API_BASE}/payment/do.html（表单），返回 JSON errcode==0 + url（收银台页）。
  - 异步通知 POST 表单，status=='OD' 为已支付；验签为 MD5(sorted(k=v&...)+appsecret)。回纯文本 success。

金额口径：用户仍按「美元」下单（余额是 micro-USD），落库 amount_micro_usd = usd*1e6；
向虎皮椒收人民币 rmb = usd * 当前 USD/CNY 参考汇率（另存 amount_rmb 便于对账）。
到账时按 amount_micro_usd(+赠送) 加余额，全站计价口径不变。

幂等：ak_payments.out_trade_no 唯一；只在 pending→paid 这一跳里加余额。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status

from config.settings import settings
from services.apikey import users as users_svc
from services.apikey.payments import MIN_USD, MAX_USD, recharge_bonus_micro
from utils import db as db_util

log = logging.getLogger("ak.xunhupay")


def configured() -> bool:
    return bool(settings.XUNHUPAY_APPID and settings.XUNHUPAY_APPSECRET)


def _rmb_per_usd() -> float:
    r = settings.XUNHUPAY_RMB_PER_USD
    return float(r) if r and r > 0 else 7.2


def _new_out_trade_no(user_id: int) -> str:
    # 与 Polar 的 sa_ 前缀区分：sx_ = substantia xunhupay
    return f"sx_{user_id}_{secrets.token_hex(8)}"


def _site_origin() -> str:
    p = urlparse(settings.PAYMENT_RETURN_URL)
    return f"{p.scheme}://{p.netloc}"


def _notify_url() -> str:
    return settings.XUNHUPAY_NOTIFY_URL or f"{_site_origin()}/api/webhooks/xunhupay"


def _return_url() -> str:
    return settings.XUNHUPAY_RETURN_URL or settings.RECHARGE_URL or settings.PAYMENT_RETURN_URL


def _sign(params: dict, secret: str) -> str:
    """参数(去 hash/空值)按 key ASCII 升序拼 "k=v&k=v"，末尾直接接 appsecret，md5 小写。"""
    filtered = {k: v for k, v in params.items() if k != "hash" and v not in ("", None)}
    raw = "&".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()


async def create_checkout(user_id: int, usd: float) -> dict:
    """建虎皮椒结账，返回 {url, out_trade_no}。usd 校验 + 落库 pending 订单。"""
    if not configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "充值未接入（虎皮椒未配置）")
    if usd is None or usd < MIN_USD or usd > MAX_USD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"金额需在 ${MIN_USD:.0f}–${MAX_USD:.0f}")

    otn = _new_out_trade_no(user_id)
    micro = int(round(usd * 1_000_000))
    from services.apikey import fx
    exchange = await fx.current_usd_cny()
    rmb = round(usd * float(exchange["rate"]), 2)
    await db_util.execute(
        "INSERT INTO ak_payments (user_id, provider, out_trade_no, amount_micro_usd, amount_rmb) "
        "VALUES ($1, 'xunhupay', $2, $3, $4)",
        user_id, otn, micro, rmb,
    )

    params = {
        "version": "1.1",
        "appid": str(settings.XUNHUPAY_APPID),
        "trade_order_id": otn,
        "total_fee": f"{rmb:.2f}",
        "title": f"Substantia API 充值 ${usd:g}",
        "time": str(int(time.time())),
        "notify_url": _notify_url(),
        "return_url": _return_url(),
        "nonce_str": uuid.uuid4().hex,
    }
    params["hash"] = _sign(params, settings.XUNHUPAY_APPSECRET)
    base = settings.XUNHUPAY_API_BASE.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.post(f"{base}/payment/do.html", data=params)
        j = r.json()
        if str(j.get("errcode")) != "0" or not j.get("url"):
            log.warning("xunhupay checkout failed: %s", str(j)[:400])
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "创建支付失败，请稍后再试")
        return {
            "url": j["url"], "out_trade_no": otn,
            "rmb_per_usd": exchange["rate"], "rate_date": exchange["date"],
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("xunhupay checkout error: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "创建支付失败，请稍后再试")


async def handle_notify(params: dict) -> str:
    """异步通知：MD5 验签 → status=='OD' 幂等加余额。返回给虎皮椒的纯文本（success/fail）。"""
    if not configured():
        return "fail"
    import hmac
    sign = params.get("hash", "")
    expected = _sign(params, settings.XUNHUPAY_APPSECRET)
    if not hmac.compare_digest(str(sign), expected):
        log.warning("xunhupay notify bad signature order=%s", params.get("trade_order_id"))
        return "fail"
    if params.get("status") != "OD":  # 非已支付：回 success 防重发，但不发货
        return "success"

    otn = params.get("trade_order_id") or ""
    row = await db_util.fetchrow("SELECT * FROM ak_payments WHERE out_trade_no = $1", otn)
    if not row:
        log.warning("xunhupay notify unknown order=%s", otn)
        return "fail"
    if row["status"] == "paid":
        return "success"

    # 幂等加款：仅 pending→paid 这一跳成功者加余额
    amount = await db_util.fetchval(
        "UPDATE ak_payments SET status = 'paid', paid_at = now(), provider_ref = $2 "
        "WHERE out_trade_no = $1 AND status = 'pending' RETURNING amount_micro_usd",
        otn, str(params.get("open_order_id") or params.get("transaction_id") or ""),
    )
    if amount is None:
        return "success"  # 并发竞态，别人已处理

    bonus = recharge_bonus_micro(int(amount))
    new_bal = await users_svc.adjust_balance(int(row["user_id"]), int(amount) + bonus)
    await users_svc.set_full_model_access(int(row["user_id"]), True)

    # 充值达标且试用仍有效 → 把剩余试用额度转为永久有效（按实付额判定，不含赠送）
    if int(amount) >= settings.AK_TRIAL_ACTIVATE_MIN_MICRO_USD:
        await db_util.execute(
            "UPDATE ak_users SET trial_permanent = true "
            "WHERE id = $1 AND trial_micro_usd > 0 AND NOT trial_permanent "
            "AND trial_expires_at IS NOT NULL AND trial_expires_at > now()",
            int(row["user_id"]),
        )

    log.info("xunhupay recharge ok user=%s otn=%s paid=%d bonus=%d micro, balance=%d",
             row["user_id"], otn, int(amount), bonus, new_bal)
    return "success"
