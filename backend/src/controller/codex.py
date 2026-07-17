# -*- coding: utf-8 -*-
"""Codex（ChatGPT 订阅）admin 接口：门控状态 + 账号池 + 网页 device-auth 登录。

登录网页终端与 claude 完全同架子（xterm ↔ PTY，轮询传输），见 services/chatgpt/login.py。
"""
from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config.settings import settings
from security.admin import require_admin
from services.chatgpt import codex as codex_svc
from services.chatgpt import login as codex_login
from services.chatgpt import provider as chatgpt

admin_router = APIRouter(prefix="/admin/codex", tags=["admin-codex"])


@admin_router.get("/status", dependencies=[Depends(require_admin)], summary="ChatGPT 上游门控状态")
async def status():
    return chatgpt.status()


@admin_router.get("/accounts", dependencies=[Depends(require_admin)], summary="列出 codex 订阅账号池")
async def accounts():
    return {"accounts": await asyncio.to_thread(codex_svc.list_accounts),
            "accounts_dir": settings.CODEX_ACCOUNTS_DIR}


@admin_router.delete("/accounts/{acc}", dependencies=[Depends(require_admin)], summary="删除某 codex 账号")
async def delete_account(acc: str):
    codex_login._assert_safe(acc)
    d = Path(settings.CODEX_ACCOUNTS_DIR).expanduser() / acc
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="account not found")
    await asyncio.to_thread(shutil.rmtree, d, True)
    return {"ok": True, "removed": acc}


# ---------------- 网页交互式登录 ----------------
class LoginStartIn(BaseModel):
    account_id: str = ""     # 留空自动分配 accN


class LoginWriteIn(BaseModel):
    session_id: str
    data: str


class LoginSessIn(BaseModel):
    session_id: str


@admin_router.post("/login/start", dependencies=[Depends(require_admin)], summary="起一个 codex 登录会话")
async def login_start(payload: LoginStartIn):
    try:
        sid = await asyncio.to_thread(codex_login.start_login, payload.account_id)
    except codex_login.CodexLoginError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"session_id": sid}


@admin_router.get("/login/read", dependencies=[Depends(require_admin)], summary="轮询：从 offset 起返回新输出（base64）")
async def login_read(session_id: str, offset: int = 0):
    try:
        data, new_offset, exited, creds_ready = await asyncio.to_thread(
            codex_login.read_login, session_id, offset)
    except codex_login.CodexLoginError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "offset": new_offset,
        "exited": exited,
        "creds_ready": creds_ready,
    }


@admin_router.post("/login/write", dependencies=[Depends(require_admin)], summary="把前端按键写进登录终端")
async def login_write(payload: LoginWriteIn):
    try:
        await asyncio.to_thread(codex_login.write_login, payload.session_id,
                                payload.data.encode("utf-8"))
    except codex_login.CodexLoginError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@admin_router.post("/login/finish", dependencies=[Depends(require_admin)], summary="收尾：凭据已写出则纳管")
async def login_finish(payload: LoginSessIn):
    try:
        out = await asyncio.to_thread(codex_login.finish_login, payload.session_id)
    except codex_login.CodexLoginError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


@admin_router.post("/login/cancel", dependencies=[Depends(require_admin)], summary="放弃一个登录会话")
async def login_cancel(payload: LoginSessIn):
    await asyncio.to_thread(codex_login.cancel_login, payload.session_id)
    return {"ok": True}
