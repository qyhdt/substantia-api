# -*- coding: utf-8 -*-
"""
APIKey 分发端到端冒烟测试。

需要一个真 Postgres（通过 DATABASE_URL 注入；无则 skip）。
覆盖：注册(自动充$20+签发key) → portal → 充值申请 → admin 审核加余额 → 模型定价 → 计费扣减。

注意：变更经 HTTP 接口触发（真打 handler/鉴权/序列化），但**变更后的状态读回用 service 直读**。
原因：starlette TestClient 给每个请求开独立事件循环，asyncpg 连接池按循环隔离，跨请求
read-after-write 会读到旧快照（已确认非产品 bug：service 直读与 DB 均为新值）。
"""
import asyncio
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL")

ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(scope="module")
def client():
    os.environ["ADMIN_EMAILS"] = ADMIN_EMAIL
    from config.settings import settings
    settings.ADMIN_EMAILS = ADMIN_EMAIL
    # E2E 只验证 API Key 业务流；不依赖部署机上的验证码和 SMTP 配置。
    settings.CAPTCHA_REQUIRED = False
    settings.EMAIL_VERIFY_REQUIRED = False
    settings.SMTP_HOST = ""
    settings.SMTP_USER = ""
    settings.SMTP_PASS = ""
    import main
    with TestClient(main.app) as c:   # lifespan 跑 migrations
        # 干净起点：清空本域表（migrations 已在 lifespan 建好）
        from utils import db as db_util
        asyncio.run(db_util.execute(
            "TRUNCATE ak_supplier_balance_snapshots, ak_supplier_ledger, "
            "ak_signup_grants, ak_users, ak_api_keys, ak_topup_requests, ak_usage_logs "
            "RESTART IDENTITY CASCADE"
        ))
        asyncio.run(db_util.execute("UPDATE ak_supplier_accounts SET balance_micro_usd=0"))
        yield c


def _run(coro):
    return asyncio.run(coro)


def test_full_flow(client):
    from services.apikey import usage as usage_svc
    from services.apikey import users as users_svc

    # 1) 注册：赠送 $20 试用桶 + 签发首把 key（实付桶仍为 0）
    r = client.post("/api/auth/register", json={"email": "u1@example.com", "password": "secret123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key"].startswith("sk-substantia-")
    assert body["user"]["balance_usd"] == 0.0
    assert body["user"]["trial_micro_usd"] == 20_000_000
    u1 = int(body["user"]["id"])
    h = {"Authorization": f"Bearer {body['access_token']}"}

    # service 直读核对余额 + 首 key
    user = _run(users_svc.get_user(u1))
    assert user["balance_micro_usd"] == 0
    assert user["trial_micro_usd"] == 20_000_000
    from services.apikey import keys as keys_svc
    klist = _run(keys_svc.list_keys(u1))
    assert len(klist) == 1 and "key_hash" not in klist[0]
    key_id = klist[0]["id"]

    # 2) portal keys 接口脱敏
    keys_http = client.get("/api/portal/keys", headers=h).json()
    assert "key_hash" not in keys_http[0]

    # 3) 充值申请 $50（HTTP）
    tp = client.post("/api/portal/topups", json={"amount_usd": 50, "reason": "more"}, headers=h)
    assert tp.status_code == 200
    topup_id = tp.json()["id"]

    # 4) admin（白名单邮箱注册即 admin）审核批准
    ra = client.post("/api/auth/register", json={"email": ADMIN_EMAIL, "password": "secret123"})
    assert ra.json()["user"]["role"] == "admin"
    ah = {"Authorization": f"Bearer {ra.json()['access_token']}"}
    rev = client.post(f"/api/admin/topups/{topup_id}/review", json={"approve": True}, headers=ah)
    assert rev.status_code == 200 and rev.json()["status"] == "approved"

    # 批准后：实付桶 $50 + 试用桶 $20 = 有效余额 $70。
    topped_up = _run(users_svc.get_user(u1))
    assert topped_up["balance_micro_usd"] == 50_000_000
    assert topped_up["trial_micro_usd"] == 20_000_000

    # 重复审核 → 409
    assert client.post(f"/api/admin/topups/{topup_id}/review",
                       json={"approve": True}, headers=ah).status_code == 409

    # 5) admin 模型定价（HTTP）
    pr = client.post("/api/admin/model-prices", json={
        "model": "test-model", "input_micro_usd_per_1k": 1000,
        "output_micro_usd_per_1k": 2000, "enabled": True,
    }, headers=ah)
    assert pr.status_code == 200

    # 6) 计费：1000 in ×1000 + 500 out ×2000 /1000 = 2000 micro（charge+读回同一循环）
    async def _charge_and_read():
        billed = await usage_svc.record_and_charge(
            api_key_id=key_id, user_id=u1, slot_id="sub-a", model="test-model",
            prompt_tokens=1000, completion_tokens=500, latency_ms=12,
        )
        return billed, await users_svc.get_user(u1), await usage_svc.usage_for_user(u1)

    billed, user_after, rows = _run(_charge_and_read())
    assert billed["cost_micro_usd"] == 2000
    assert user_after["balance_micro_usd"] == 50_000_000
    assert user_after["trial_micro_usd"] == 20_000_000 - 2000
    assert rows["total"] == 1
    assert rows["items"][0]["cost_micro_usd"] == 2000
    assert rows["items"][0]["model"] == "test-model"


def test_gateway_requires_key(client):
    # 官网价格无需登录，但绝不能暴露供应商商务折扣与成本。
    prices = client.get("/api/public/prices")
    assert prices.status_code == 200
    glm = next(row for row in prices.json() if row["model"] == "glm-5.2")
    assert glm["supplier_managed"] is True
    assert "supplier" not in glm and "supplier_multiplier" not in glm

    # 无 sk-key → 401
    assert client.post("/api/v1/messages", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    # 乱 key → 401
    r = client.post("/api/v1/messages",
                    json={"messages": [{"role": "user", "content": "hi"}]},
                    headers={"x-api-key": "sk-substantia-bogus"})
    assert r.status_code == 401


def test_moxing_supplier_reconciliation_conserves_both_ledgers(client):
    from services.apikey import moxing_accounting as acct
    from services.apikey import pricing as pricing_svc
    from services.apikey import usage as usage_svc
    from services.apikey import users as users_svc

    async def _flow():
        created = await users_svc.create_user_by_admin(
            "moxing-ledger@example.com", "secret123", "user", 10_000_000,
        )
        user_id = int(created["user"]["id"])
        key_id = int(created["api_key"]["id"])
        await acct.add_funds(
            amount=Decimal("10"), currency="USD", entry_type="topup",
            admin_id=user_id, reference="mx-test-topup",
        )
        await acct.update_terms(
            model="glm-5.2", display_name="GLM 5.2",
            official_input=Decimal("1.4"), official_output=Decimal("4.4"),
            official_cache_read=Decimal("0.26"), official_cache_write=Decimal("1.4"),
            supplier_multiplier=Decimal("0.9"), sale_multiplier=Decimal("0.8"),
            admin_id=user_id,
        )
        billed = await usage_svc.record_and_charge(
            api_key_id=key_id, user_id=user_id, slot_id="direct-moxing", model="glm-5.2",
            upstream_model="glm-5.2", prompt_tokens=1000, completion_tokens=100,
            latency_ms=10, request_id="req-ledger-test",
        )
        await acct.add_balance_snapshot(
            amount=Decimal("9.998344"), currency="USD", admin_id=user_id,
            note="matching supplier statement",
        )
        public_prices = await pricing_svc.list_prices()
        public_usage = await usage_svc.usage_for_user(user_id)
        return (
            billed,
            await users_svc.get_user(user_id),
            await acct.accounting_summary(),
            public_prices,
            public_usage,
        )

    billed, user, summary, public_prices, public_usage = _run(_flow())
    assert billed["cost_micro_usd"] == 1472               # 官网价 × 销售八折
    assert billed["supplier_cost_micro_usd"] == 1656      # 官网价 × 供应商九折
    assert billed["charged_paid_micro_usd"] == 1472
    assert billed["charged_trial_micro_usd"] == 0
    assert user["balance_micro_usd"] == 10_000_000 - 1472
    assert summary["account"]["balance_micro_usd"] == 10_000_000 - 1656
    assert summary["ledger_totals"]["internal_variance_micro_usd"] == 0
    assert summary["latest_snapshot"]["variance_micro_usd"] == 0
    assert summary["period"]["gross_profit_micro_usd"] == 1472 - 1656
    glm_price = next(row for row in public_prices if row["model"] == "glm-5.2")
    assert glm_price["supplier_managed"] is True
    assert "supplier" not in glm_price and "supplier_multiplier" not in glm_price
    usage_row = public_usage["items"][0]
    assert "supplier_cost_micro_usd" not in usage_row
    assert "upstream_model" not in usage_row


def test_admin_guard(client):
    # 普通用户访问 admin → 403
    reg = client.post("/api/auth/register", json={"email": "u2@example.com", "password": "secret123"})
    uh = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client.get("/api/admin/users", headers=uh).status_code == 403
