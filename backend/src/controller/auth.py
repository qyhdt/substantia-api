# -*- coding: utf-8 -*-
"""
账户：自助注册（自动充 $20 + 签发首把 key）、登录。

注册/登录成功会签发 JWT，并写进 cookie（前端 credentials:"include" 自动带），
同时在 body 里回 access_token 兼容非浏览器调用方。
"""
from fastapi import APIRouter, Response
from pydantic import BaseModel, EmailStr, Field

from config.settings import settings
from security.jwt_handler import create_access_token
from services.apikey import usd
from services.apikey import users as users_svc

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        key=settings.AUTH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        path="/",
    )


@router.post("/register", summary="自助注册：自动送 $20 余额 + 签发首把 key")
async def register(payload: RegisterIn, response: Response):
    out = await users_svc.register(payload.email, payload.password)
    user = out["user"]
    token = create_access_token(user)
    _set_cookie(response, token)
    return {
        "user": {**user, "balance_usd": usd(user["balance_micro_usd"])},
        "access_token": token,
        "api_key": out["api_key_plain"],  # 明文仅此一次
        "api_key_info": out["api_key"],
    }


@router.post("/login", summary="登录")
async def login(payload: LoginIn, response: Response):
    user = await users_svc.authenticate(payload.email, payload.password)
    token = create_access_token(user)
    _set_cookie(response, token)
    return {
        "user": {**user, "balance_usd": usd(user["balance_micro_usd"])},
        "access_token": token,
    }


@router.post("/logout", summary="登出")
async def logout(response: Response):
    response.delete_cookie(settings.AUTH_COOKIE, path="/")
    return {"ok": True}
