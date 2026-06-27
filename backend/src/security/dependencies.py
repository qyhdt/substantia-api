# -*- coding: utf-8 -*-
"""
鉴权依赖：从 Authorization: Bearer <token> 中取 access token，验签后把用户信息注入：
1. ContextVar request_context（供日志/业务读）
2. request.state（跨 asyncio Context 共享，供 RequestContextMiddleware 补回 user_id）

测试模式（settings.AUTH_DISABLED=true）下返回 dummy user，跳过验签。
"""
from fastapi import HTTPException, Request, status

from config.settings import settings
from security.jwt_handler import verify_token
from utils.pm_logger import get_app_logger
from utils.request_context import request_context

logger = get_app_logger()

DUMMY_USER = {
    "user_id": "dev-user",
    "user_email": "dev@example.com",
    "user_role": "dev",
}


async def require_access_token(request: Request):
    """
    依赖用法：
        from security.dependencies import require_access_token
        @router.get("/me", dependencies=[Depends(require_access_token)])
    """
    # CORS preflight 直接放行
    if request.method == "OPTIONS":
        return

    if settings.AUTH_DISABLED:
        _inject(request, DUMMY_USER["user_id"], DUMMY_USER["user_email"], DUMMY_USER["user_role"])
        return

    # 优先从 cookie 读（前端走 credentials:"include"），兼容 Authorization Bearer（旧调用方）
    token = (request.cookies.get(settings.AUTH_COOKIE) or "").strip()
    if not token:
        auth = request.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1].strip()

    if not token:
        logger.warning("auth_failed path=%s reason=missing_token", request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    try:
        payload = verify_token(token, token_type="access")
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("auth_failed path=%s reason=no_sub", request.url.path)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        _inject(
            request,
            user_id=user_id,
            user_email=payload.get("email"),
            user_role=payload.get("role"),
            extra_claims={k: v for k, v in payload.items() if k not in {"sub", "email", "role", "type", "iat", "exp"}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("auth_failed path=%s reason=invalid_token msg=%s", request.url.path, str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e


def _inject(request: Request, user_id: str, user_email=None, user_role=None, extra_claims: dict | None = None):
    """把用户上下文写入 ContextVar 和 request.state。"""
    ctx = request_context.get().copy()
    ctx.update(
        {
            "user_id": user_id,
            "user_email": user_email,
            "user_role": user_role,
            "user": {"id": user_id, "email": user_email, "role": user_role},
        }
    )
    if extra_claims:
        ctx["user_claims"] = extra_claims
    request_context.set(ctx)

    # BaseHTTPMiddleware 的 call_next 在子 Context；子写不到父，借 request.state 共享
    request.state.authed_user_id = user_id
    request.state.authed_user_email = user_email


def current_user(request: Request) -> dict:
    """
    在已通过 require_access_token 的接口里取当前用户：
        @router.get("/me")
        async def me(user: dict = Depends(current_user)): ...
    """
    return (request_context.get({}) or {}).get("user") or {}
