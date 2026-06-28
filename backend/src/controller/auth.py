# -*- coding: utf-8 -*-
"""
账户：自助注册（图形验证码 + 邮箱验证码 + 设备去重赠送 $20 + 签发首把 key）、登录（图形验证码）。

注册/登录成功会签发 JWT，并写进 cookie（前端 credentials:"include" 自动带），
同时在 body 里回 access_token 兼容非浏览器调用方。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from config.settings import settings
from security.jwt_handler import create_access_token
from services import captcha_service, email_service
from services.apikey import usd
from services.apikey import users as users_svc

router = APIRouter(prefix="/auth", tags=["auth"])

_CAPTCHA_ERR = "captcha invalid or expired"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    captcha_id: str = Field(default="", max_length=64)
    captcha_text: str = Field(default="", max_length=16)
    email_code: Optional[str] = Field(default=None, max_length=8)
    device_id: Optional[str] = Field(default=None, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    captcha_id: str = Field(default="", max_length=64)
    captcha_text: str = Field(default="", max_length=16)


class SendEmailCodeIn(BaseModel):
    email: EmailStr
    captcha_id: str = Field(default="", max_length=64)
    captcha_text: str = Field(default="", max_length=16)


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        key=settings.AUTH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        path="/",
    )


def _client_ip(request: Request) -> str:
    """取真实客户端 IP（backend 在 nginx 后，优先 X-Forwarded-For 首段）。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _email_verify_on() -> bool:
    return email_service.configured() or settings.EMAIL_VERIFY_REQUIRED


@router.get("/captcha", summary="获取图形验证码（SVG data URL + captcha_id）")
async def get_captcha() -> dict:
    return await captcha_service.issue()


@router.get("/signup-config", summary="注册/登录需要哪些校验（前端据此显示验证码 UI）")
async def signup_config() -> dict:
    return {"captcha_required": settings.CAPTCHA_REQUIRED, "email_verify_required": _email_verify_on()}


@router.post("/send-email-code", summary="发送注册邮箱验证码（校验图形码但不消费，留到注册用）")
async def send_email_code(body: SendEmailCodeIn) -> dict:
    if settings.CAPTCHA_REQUIRED and not await captcha_service.verify(
        body.captcha_id, body.captcha_text, consume=False
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _CAPTCHA_ERR)
    try:
        await email_service.send_code(str(body.email))
    except email_service.EmailError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True}


@router.post("/register", summary="自助注册：图形码 + 邮箱码 + 设备去重送 $20 + 签发首把 key")
async def register(payload: RegisterIn, request: Request, response: Response):
    if settings.CAPTCHA_REQUIRED and not await captcha_service.verify(payload.captcha_id, payload.captcha_text):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _CAPTCHA_ERR)
    if _email_verify_on() and not await email_service.verify_code(str(payload.email), payload.email_code or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email code invalid or expired")

    out = await users_svc.register(
        str(payload.email), payload.password,
        device_id=payload.device_id, ip=_client_ip(request),
    )
    user = out["user"]
    token = create_access_token(user)
    _set_cookie(response, token)
    return {
        "user": {**user, "balance_usd": usd(user["balance_micro_usd"])},
        "access_token": token,
        "api_key": out["api_key_plain"],  # 明文仅此一次
        "api_key_info": out["api_key"],
    }


@router.post("/login", summary="登录（图形验证码）")
async def login(payload: LoginIn, response: Response):
    if settings.CAPTCHA_REQUIRED and not await captcha_service.verify(payload.captcha_id, payload.captcha_text):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _CAPTCHA_ERR)
    user = await users_svc.authenticate(str(payload.email), payload.password)
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
