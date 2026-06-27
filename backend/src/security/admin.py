# -*- coding: utf-8 -*-
"""
Admin 鉴权依赖：在 require_access_token 之上再叠一层"邮箱必须在 ADMIN_EMAILS 白名单"。

用法：
    from security.admin import require_admin

    @router.post("/dangerous-op", dependencies=[Depends(require_admin)])
    async def ...
"""
from fastapi import Depends, HTTPException, Request, status

from config.settings import settings
from security.dependencies import current_user, require_access_token


async def require_admin(
    request: Request,
    _: None = Depends(require_access_token),
) -> dict:
    """先走 access_token 鉴权（拿 user 上下文），再校验是否 admin。

    admin 判定（任一满足即可）：
      1. 邮箱在 settings.admin_emails_list 白名单（bootstrap admin，写死 .env）
      2. 数据库 vibe_users.role = 'admin'（运行时由现有 admin 提升）
    """
    user = current_user(request)
    email = (user.get("email") or "").strip().lower()
    role = (user.get("role") or "").strip().lower()

    allow = {e.lower() for e in settings.admin_emails_list}
    if email in allow or role == "admin":
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="admin only",
    )
