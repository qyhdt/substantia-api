# -*- coding: utf-8 -*-
"""
支付 webhook 接收（无鉴权，自带签名校验）。后台端点配到：
    Polar    → https://dev.substantia.ai/api/webhooks/polar
    虎皮椒    → https://dev.substantia.ai/api/webhooks/xunhupay
"""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from services.apikey import payments as payments_svc
from services.apikey import xunhupay as xunhupay_svc

log = logging.getLogger("ak.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/polar", summary="Polar 支付回调")
async def polar_webhook(request: Request):
    raw = await request.body()
    # headers 大小写不敏感；Standard Webhooks 用 webhook-id/-timestamp/-signature
    result = await payments_svc.handle_webhook(request.headers, raw)
    return result


@router.post("/xunhupay", summary="虎皮椒支付回调（MD5 验签 → 加余额，回 success）")
async def xunhupay_webhook(request: Request) -> PlainTextResponse:
    # 通知为表单 POST；个别情况走 query，两者都取。
    try:
        form = dict(await request.form())
    except Exception:  # noqa: BLE001
        form = {}
    params = {**dict(request.query_params), **form}
    out = await xunhupay_svc.handle_notify(params)
    return PlainTextResponse(out)
