# -*- coding: utf-8 -*-
"""无需登录的官网展示接口；只返回可公开的销售信息。"""
from fastapi import APIRouter

from services.apikey import pricing as pricing_svc

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/prices", summary="官网公开模型价格")
async def public_prices():
    rows = await pricing_svc.list_prices()
    return [row for row in rows if row.get("enabled", True)]
