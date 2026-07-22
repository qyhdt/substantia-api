# -*- coding: utf-8 -*-
"""
加额度/充值申请：用户提交 → admin 审核（批准即加余额）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from utils import db as db_util


async def submit(user_id: int, requested_micro_usd: int, reason: Optional[str] = None,
                 proof_url: Optional[str] = None) -> Dict[str, Any]:
    if requested_micro_usd <= 0:
        raise HTTPException(status_code=400, detail="requested amount must be > 0")
    # proof_url 为转账凭证截图（可空）。
    row = await db_util.fetchrow(
        "INSERT INTO ak_topup_requests (user_id, requested_micro_usd, reason, proof_url) "
        "VALUES ($1, $2, $3, $4) RETURNING *",
        user_id, int(requested_micro_usd), reason, proof_url,
    )
    return dict(row)


async def list_for_user(user_id: int) -> List[Dict[str, Any]]:
    rows = await db_util.fetch(
        "SELECT * FROM ak_topup_requests WHERE user_id = $1 ORDER BY created_at DESC", user_id
    )
    return [dict(r) for r in rows]


async def list_all(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    if status_filter:
        rows = await db_util.fetch(
            "SELECT t.*, u.email FROM ak_topup_requests t JOIN ak_users u ON u.id = t.user_id "
            "WHERE t.status = $1 ORDER BY t.created_at DESC",
            status_filter,
        )
    else:
        rows = await db_util.fetch(
            "SELECT t.*, u.email FROM ak_topup_requests t JOIN ak_users u ON u.id = t.user_id "
            "ORDER BY t.created_at DESC"
        )
    return [dict(r) for r in rows]


async def review(
    topup_id: int, *, approve: bool, reviewer_id: int, note: Optional[str] = None
) -> Dict[str, Any]:
    """审核：批准则在同一事务里把金额加到用户余额。重复审核 → 409。"""
    new_status = "approved" if approve else "rejected"
    async with db_util.transaction() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM ak_topup_requests WHERE id = $1 FOR UPDATE", topup_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="topup not found")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"already {row['status']}")

        await conn.execute(
            "UPDATE ak_topup_requests SET status = $1, review_note = $2, reviewed_by = $3, "
            "reviewed_at = now() WHERE id = $4",
            new_status, note, reviewer_id, topup_id,
        )
        if approve:
            await conn.execute(
                "UPDATE ak_users SET balance_micro_usd = balance_micro_usd + $1, "
                "full_model_access = true WHERE id = $2",
                int(row["requested_micro_usd"]), row["user_id"],
            )
        updated = await conn.fetchrow("SELECT * FROM ak_topup_requests WHERE id = $1", topup_id)
    return dict(updated)
