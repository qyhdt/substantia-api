# -*- coding: utf-8 -*-
"""
示例路由：演示
1. 鉴权 Depends 用法
2. 调用 service 层
3. 日志自动带 trace_id / user_id
4. 校验错误经统一异常处理返回
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from security.dependencies import current_user, require_access_token
from services.example_service import echo_user, mock_create_item

router = APIRouter(prefix="/example", tags=["example"])


class CreateItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=1000)


@router.get("/ping", summary="无需鉴权：返回 pong")
async def ping():
    return {"pong": True}


@router.get("/me", dependencies=[Depends(require_access_token)], summary="返回当前用户（需鉴权）")
async def me(user: dict = Depends(current_user)):
    return await echo_user(user)


@router.post("/items", dependencies=[Depends(require_access_token)], summary="示例：创建 item（需鉴权）")
async def create_item(payload: CreateItemIn, user: dict = Depends(current_user)):
    return await mock_create_item(payload.name, payload.quantity, user.get("id"))
