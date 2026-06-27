# -*- coding: utf-8 -*-
"""健康检查 & 版本信息。"""
from fastapi import APIRouter

from config.version import APP_NAME, __version__

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
async def health():
    return {"status": "ok"}


@router.get("/version", summary="服务版本")
async def version():
    return {"name": APP_NAME, "version": __version__}
