# -*- coding: utf-8 -*-
"""
支付 webhook 接收（无鉴权，自带签名校验）。Polar 后台把端点配到：
    https://api.substantia.ai/api/webhooks/polar
"""
import logging

from fastapi import APIRouter, Request

from services.apikey import payments as payments_svc

log = logging.getLogger("ak.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/polar", summary="Polar 支付回调")
async def polar_webhook(request: Request):
    raw = await request.body()
    # headers 大小写不敏感；Standard Webhooks 用 webhook-id/-timestamp/-signature
    result = await payments_svc.handle_webhook(request.headers, raw)
    return result
