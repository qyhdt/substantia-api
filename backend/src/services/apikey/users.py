# -*- coding: utf-8 -*-
"""
用户：自助注册（自动充 $20 + 签发首把 key）、登录校验、余额操作。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from config.settings import settings
from security.password import hash_password, verify_password
from services.apikey import keys as keys_svc
from utils import db as db_util
from utils.pm_logger import get_app_logger

log = get_app_logger()


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    d.pop("password_hash", None)
    return d


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    row = await db_util.fetchrow("SELECT * FROM ak_users WHERE id = $1", user_id)
    return _public(dict(row)) if row else None


async def get_by_email(email: str) -> Optional[Dict[str, Any]]:
    row = await db_util.fetchrow("SELECT * FROM ak_users WHERE email = $1", email.lower())
    return dict(row) if row else None  # 含 password_hash，供登录校验


async def register(email: str, password: str, *, device_id: Optional[str] = None,
                   ip: Optional[str] = None) -> Dict[str, Any]:
    """注册：建用户 + 签发首把默认 key（事务）。
    设备/IP 去重赠送：同一 device_id 或 ip 已领过 $20 → 本次 trial=0（账号照样建）。
    返回 {user, api_key_plain}。邮箱已存在 → 409。"""
    email = email.strip().lower()
    if await get_by_email(email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")

    # bootstrap admin：邮箱在白名单则注册即 admin
    role = "admin" if email in {e.lower() for e in settings.admin_emails_list} else "user"
    pwd_hash = hash_password(password)
    trial_grant = settings.AK_TRIAL_GRANT_MICRO_USD
    trial_days = settings.AK_TRIAL_EXPIRE_DAYS
    dev = (device_id or "").strip() or None
    ip = (ip or "").strip() or None

    async with db_util.transaction() as conn:
        # 设备/IP 去重：任一已领过赠送 → 本次不送（防清缓存反复注册薅 $20）
        granted_before = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM ak_signup_grants
                WHERE granted_micro_usd > 0
                  AND ( ($1::text IS NOT NULL AND device_id = $1)
                     OR ($2::text IS NOT NULL AND ip = $2) )
            )
            """,
            dev, ip,
        )
        trial = 0 if granted_before else trial_grant
        # 赠送进「试用桶」，有效期 trial_days 天（trial=0 时不设过期）；实付桶（balance）初始 0
        urow = await conn.fetchrow(
            """
            INSERT INTO ak_users (email, password_hash, role, balance_micro_usd,
                                  trial_micro_usd, trial_expires_at)
            VALUES ($1, $2, $3, 0, $4::bigint,
                    CASE WHEN $4::bigint > 0 THEN now() + make_interval(days => $5) ELSE NULL END)
            RETURNING *
            """,
            email, pwd_hash, role, trial, int(trial_days),
        )
        user = _public(dict(urow))
        # 记录本次发放（trial=0 也记，便于审计；去重判断只看 granted>0）
        await conn.execute(
            "INSERT INTO ak_signup_grants (user_id, device_id, ip, granted_micro_usd) "
            "VALUES ($1, $2, $3, $4)",
            user["id"], dev, ip, trial,
        )
        issued = await keys_svc.issue_key(user["id"], name="default", conn=conn)

    log.info("ak_register email=%s id=%s trial_micro=%s device=%s ip=%s granted_before=%s",
             email, user["id"], trial, dev, ip, granted_before)
    return {"user": user, "api_key_plain": issued["plain"], "api_key": issued["key"]}


async def authenticate(email: str, password: str) -> Dict[str, Any]:
    """登录校验，成功返回脱敏 user。失败 401。"""
    row = await get_by_email(email)
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if row["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user disabled")
    return _public(row)


async def adjust_balance(user_id: int, delta_micro_usd: int) -> int:
    """原子加/减余额，返回新余额。减到负不在这里拦（扣费链路用带条件的 UPDATE）。"""
    new_bal = await db_util.fetchval(
        "UPDATE ak_users SET balance_micro_usd = balance_micro_usd + $1 WHERE id = $2 "
        "RETURNING balance_micro_usd",
        int(delta_micro_usd), user_id,
    )
    return int(new_bal) if new_bal is not None else 0


async def list_users(limit: int = 200) -> List[Dict[str, Any]]:
    """用户列表。返回的 balance_micro_usd 是「有效余额」= 实付桶 + 有效试用桶，
    使前端看到的余额与门户 /portal/me 一致（注册送的 $20 进试用桶，曾被漏算成 $0）。"""
    from services.apikey.balance import effective_balance, trial_active

    rows = await db_util.fetch(
        "SELECT id, email, role, status, balance_micro_usd, "
        "trial_micro_usd, trial_expires_at, trial_permanent, created_at "
        "FROM ak_users ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        u = dict(r)
        paid = int(u.get("balance_micro_usd") or 0)
        active = trial_active(u)
        u["paid_micro_usd"] = paid                               # 实付桶
        u["trial_active"] = active                               # 试用是否有效
        u["trial_micro_usd"] = int(u.get("trial_micro_usd") or 0)
        u["effective_micro_usd"] = effective_balance(u)          # 实付 + 有效试用
        u["balance_micro_usd"] = u["effective_micro_usd"]        # 前端「余额」列展示有效总额
        out.append(u)
    return out


async def user_detail(user_id: int) -> Optional[Dict[str, Any]]:
    """单用户详情：账户 + 余额分桶 + 用量聚合（按模型）+ 最近用量明细 + key 列表。
    供 admin「点击邮箱看详细消费」用。"""
    from services.apikey import usage as usage_svc
    from services.apikey.balance import effective_balance, trial_active

    row = await db_util.fetchrow(
        "SELECT id, email, role, status, balance_micro_usd, "
        "trial_micro_usd, trial_expires_at, trial_permanent, created_at "
        "FROM ak_users WHERE id = $1",
        user_id,
    )
    if not row:
        return None
    u = dict(row)
    active = trial_active(u)
    u["paid_micro_usd"] = int(u.get("balance_micro_usd") or 0)
    u["trial_micro_usd"] = int(u.get("trial_micro_usd") or 0)
    u["trial_active"] = active
    u["effective_micro_usd"] = effective_balance(u)

    spend = await usage_svc.user_spend_summary(user_id)
    keys = await db_util.fetch(
        "SELECT id, name, key_prefix, status, spent_micro_usd, quota_cap_micro_usd, "
        "last_used_at, created_at FROM ak_api_keys WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
    )
    recent = await usage_svc.usage_for_user(user_id, limit=20, offset=0)
    return {
        "user": u,
        "spend": spend,                  # {total_cost_micro_usd, total_calls, total_tokens, by_model}
        "keys": [dict(k) for k in keys],
        "recent_usage": recent["items"],
        "recent_total": recent["total"],
    }


async def set_role(user_id: int, role: str) -> bool:
    res = await db_util.execute("UPDATE ak_users SET role = $1 WHERE id = $2", role, user_id)
    return res.endswith("1")


async def set_status(user_id: int, status_val: str) -> bool:
    res = await db_util.execute("UPDATE ak_users SET status = $1 WHERE id = $2", status_val, user_id)
    return res.endswith("1")
