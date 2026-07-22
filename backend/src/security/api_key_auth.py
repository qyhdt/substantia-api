# -*- coding: utf-8 -*-
"""
下游 sk-key 的生成与校验（网关 /v1/* 用，**不走 JWT**）。

- 生成：`sk-substantia-<urlsafe>`，库里只存 sha256(明文)，明文只在签发时返回一次。
- 校验：从 `x-api-key`（Anthropic 风格）或 `Authorization: Bearer` 取 key，
        sha256 后查 ak_api_keys，校验 status/expires，并带出所属用户。
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status

from utils import db as db_util

KEY_PREFIX = "sk-substantia-"  # 默认/回退前缀；实际按请求品牌（current_brand）取，见 generate_key


def generate_key() -> tuple[str, str, str]:
    """返回 (明文 key, 展示用 prefix, sha256 hash)。明文只此一次。
    前缀按当前请求品牌（yayaok→sk-yaya-，其余→sk-substantia-）；校验走 sha256 哈希，与前缀无关。"""
    from config.brands import current_brand
    prefix = current_brand().get("key_prefix") or KEY_PREFIX
    plain = prefix + secrets.token_urlsafe(32)
    display = plain[: len(prefix) + 6] + "…"
    return plain, display, hash_key(plain)


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def extract_key(request: Request) -> Optional[str]:
    """从 x-api-key 或 Authorization: Bearer 取 sk-key。"""
    xk = (request.headers.get("x-api-key") or "").strip()
    if xk:
        return xk
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


async def authenticate_key(request: Request) -> Dict[str, Any]:
    """
    网关依赖：校验 sk-key，返回 {key: <ak_api_keys 行>, user: <ak_users 行>}。
    失败抛 401/403。余额/限额校验在 service 层做（需要更细的错误语义）。
    """
    plain = extract_key(request)
    if not plain:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing api key")

    row = await db_util.fetchrow(
        """
        SELECT k.*, u.email AS user_email, u.role AS user_role,
               u.status AS user_status, u.balance_micro_usd AS user_balance,
               u.trial_micro_usd AS user_trial, u.trial_expires_at AS user_trial_expires_at,
               u.trial_permanent AS user_trial_permanent,
               u.full_model_access AS user_full_model_access
        FROM ak_api_keys k
        JOIN ak_users u ON u.id = k.user_id
        WHERE k.key_hash = $1
        """,
        hash_key(plain),
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")

    key = dict(row)
    if key["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"key {key['status']}")
    if key["user_status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user disabled")

    expires_at = key.get("expires_at")
    if expires_at is not None:
        from datetime import datetime, timezone

        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="key expired")

    user = {
        "id": key["user_id"],
        "email": key["user_email"],
        "role": key["user_role"],
        "balance_micro_usd": key["user_balance"],
        "trial_micro_usd": key["user_trial"],
        "trial_expires_at": key["user_trial_expires_at"],
        "trial_permanent": key["user_trial_permanent"],
        "full_model_access": bool(key["user_full_model_access"]),
    }
    return {"key": key, "user": user}
