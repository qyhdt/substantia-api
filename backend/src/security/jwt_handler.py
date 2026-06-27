# -*- coding: utf-8 -*-
"""
JWT 编/解码：HS256 默认，可通过 settings.JWT_ALGORITHM 切换。

约定 payload 字段：
    sub:    user_id（字符串）
    email:  邮箱（可选）
    role:   角色（可选）
    type:   "access" 或 "refresh"
    iat / exp
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from jose import JWTError, jwt

from config.settings import settings


def _encode(payload: Dict[str, Any]) -> str:
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_data: Dict[str, Any], expire_hours: int = None) -> str:
    hours = expire_hours if expire_hours is not None else settings.ACCESS_TOKEN_EXPIRE_HOURS
    payload = {
        "sub": str(user_data["id"]),
        "email": user_data.get("email"),
        "role": user_data.get("role"),
        "type": "access",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
    }
    return _encode({k: v for k, v in payload.items() if v is not None})


def create_refresh_token(user_data: Dict[str, Any]) -> str:
    payload = {
        "sub": str(user_data["id"]),
        "type": "refresh",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.AUTH_TOKEN_EXPIRE_HOURS),
    }
    return _encode(payload)


def create_tokens(user_data: Dict[str, Any]) -> Dict[str, str]:
    """一次性生成 access + refresh。"""
    return {
        "access_token": create_access_token(user_data),
        "refresh_token": create_refresh_token(user_data),
    }


def verify_token(token: str, token_type: Optional[str] = None) -> Dict[str, Any]:
    """
    解码并验证 JWT；可选检查 type 字段。
    无效/过期/类型不符 → 401。
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e

    if token_type and payload.get("type") != token_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token type, expected {token_type}",
        )
    return payload
