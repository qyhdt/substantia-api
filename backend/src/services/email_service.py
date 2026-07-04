# -*- coding: utf-8 -*-
"""邮箱验证码：生成 6 位码存 Redis + 通过 SMTP 发信。注册时校验。

移植自 digital-platform（qqyhdt@gmail.com 发信）。
- 验证码存 Redis: ak:email_code:{email}，TTL = settings.EMAIL_CODE_TTL
- 重发节流:     ak:email_code_sent:{email}（短 TTL，存在则拒绝重发）
- 错误计数:     ak:email_code_try:{email}（超 5 次作废，防暴力）
SMTP 用标准库 smtplib，放线程跑（避免阻塞事件循环 / 新依赖）。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from config.settings import settings
from utils import redis as redis_util

log = logging.getLogger("ak.email")

_CODE_KEY = "ak:email_code:{}"
_SENT_KEY = "ak:email_code_sent:{}"
_TRY_KEY = "ak:email_code_try:{}"
_MAX_TRY = 5


def configured() -> bool:
    """SMTP 是否已配置（HOST + USER + PASS 齐全）。"""
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASS)


class EmailError(Exception):
    """发送/校验过程中的可向用户展示的错误。"""


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _send_smtp_sync(to_email: str, code: str) -> None:
    """同步发信（在线程里调用）。失败抛异常。"""
    from config.brands import current_brand
    b = current_brand()
    sender = settings.SMTP_FROM or settings.SMTP_USER
    subject = b.get("email_subject") or "Substantia verification code"
    from_name = settings.SMTP_FROM_NAME or b.get("name") or "Substantia"
    body = (
        f"Your verification code is: {code}\n\n"
        f"It is valid for {settings.EMAIL_CODE_TTL // 60} minutes. Do not share it.\n"
        f"If you did not request this, please ignore this email."
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), sender))
    msg["To"] = to_email

    port = int(settings.SMTP_PORT or 587)
    if port == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, port, timeout=20) as s:
            s.login(settings.SMTP_USER, settings.SMTP_PASS)
            s.sendmail(sender, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(settings.SMTP_HOST, port, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(settings.SMTP_USER, settings.SMTP_PASS)
            s.sendmail(sender, [to_email], msg.as_string())


async def send_code(email: str) -> None:
    """生成验证码并发邮件。已配置才发；节流；失败抛 EmailError。"""
    email = _norm(email)
    if not email or "@" not in email:
        raise EmailError("invalid email")
    if not configured():
        raise EmailError("email service not configured")
    # 重发节流
    if await redis_util.exists(_SENT_KEY.format(email)):
        raise EmailError(f"too frequent, retry in {settings.EMAIL_CODE_RESEND_SECONDS}s")

    code = f"{secrets.randbelow(1000000):06d}"
    try:
        await asyncio.to_thread(_send_smtp_sync, email, code)
    except Exception as e:  # noqa: BLE001
        log.warning("send email code failed to=%s err=%s", email, e)
        raise EmailError("failed to send code, please retry")

    await redis_util.setex(_CODE_KEY.format(email), settings.EMAIL_CODE_TTL, code)
    await redis_util.setex(_SENT_KEY.format(email), settings.EMAIL_CODE_RESEND_SECONDS, "1")
    await redis_util.delete(_TRY_KEY.format(email))
    log.info("email code sent to=%s", email)


async def verify_code(email: str, code: str) -> bool:
    """校验验证码。通过即删码；错误累计超 _MAX_TRY 次则作废。"""
    email = _norm(email)
    code = (code or "").strip()
    if not email or not code:
        return False
    saved = await redis_util.get(_CODE_KEY.format(email))
    if not saved:
        return False
    try_key = _TRY_KEY.format(email)
    tries = int(await redis_util.get(try_key) or 0)
    if tries >= _MAX_TRY:
        await redis_util.delete(_CODE_KEY.format(email))
        return False
    if saved == code:
        await redis_util.delete(_CODE_KEY.format(email))
        await redis_util.delete(try_key)
        return True
    await redis_util.setex(try_key, settings.EMAIL_CODE_TTL, str(tries + 1))
    return False
