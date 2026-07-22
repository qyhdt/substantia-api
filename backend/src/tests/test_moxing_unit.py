# -*- coding: utf-8 -*-
"""moxing 公开模型与用户路由标签测试（不访问网络/数据库）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from decimal import Decimal

import pytest

from config.settings import settings
from controller import gateway
from controller.gateway import ChatCompletionsIn, _force_glm_for_user
from services.moxing import provider as moxing
from services.apikey import moxing_accounting as accounting


def test_direct_model_detection():
    assert moxing.is_direct_model("glm-5.2")
    assert moxing.is_direct_model(" KIMI-K3 ")
    assert not moxing.is_direct_model("claude-opus-4-8")


def test_full_model_access_tag_controls_forced_glm():
    assert _force_glm_for_user({"full_model_access": False})
    assert _force_glm_for_user({})
    assert not _force_glm_for_user({"full_model_access": True})


def test_direct_router_reuses_fallback_credentials(monkeypatch):
    monkeypatch.setattr(settings, "MOXING_API_BASE", "")
    monkeypatch.setattr(settings, "MOXING_API_KEY", "")
    monkeypatch.setattr(settings, "CLAUDE_FALLBACK_MOXING_BASE_URL", "https://www.moxing.pro")
    monkeypatch.setattr(settings, "CLAUDE_FALLBACK_MOXING_AUTH_TOKEN", "test-secret")

    slots = moxing.direct_router("kimi-k3").all_slots()
    assert len(slots) == 1
    assert slots[0].id == "direct-moxing"
    assert slots[0].env == {
        "ANTHROPIC_BASE_URL": "https://www.moxing.pro",
        "ANTHROPIC_AUTH_TOKEN": "test-secret",
        "ANTHROPIC_MODEL": "kimi-k3",
    }


def test_moxing_usage_splits_cached_input():
    data = {
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 20},
        }
    }
    assert moxing.usage(data) == (100, 30, 20)


def test_moxing_accounting_model_and_slot_detection():
    assert accounting.canonical_model("GLM-5.2[1m]") == "glm-5.2"
    assert accounting.canonical_model(" kimi-k3 ") == "kimi-k3"
    assert accounting.is_moxing_slot("direct-moxing")
    assert accounting.is_moxing_slot("fallback-moxing")
    assert not accounting.is_moxing_slot("fallback-gemini")


def test_kimi_k3_cost_matches_moxing_bill_exactly():
    term = {
        "official_input_micro_cny_per_million": 20_000_000,
        "official_output_micro_cny_per_million": 100_000_000,
        "official_cache_read_micro_cny_per_million": 2_000_000,
        "official_cache_write_micro_cny_per_million": 20_000_000,
        "supplier_multiplier": Decimal("1"),
    }
    official, supplier = accounting.usage_cost(
        term, prompt_tokens=524_513, completion_tokens=561,
        cache_read_tokens=523_776, cache_write_tokens=0,
    )
    assert official == 11_593_912
    assert supplier == 11_593_912


@pytest.mark.asyncio
async def test_moxing_request_posts_supplier_ledger_and_balance():
    class Conn:
        def __init__(self):
            self.executed = []

        async def fetchrow(self, query, *args):
            if "SELECT * FROM ak_supplier_model_terms" in query:
                return {
                    "official_input_micro_cny_per_million": 20_000_000,
                    "official_output_micro_cny_per_million": 100_000_000,
                    "official_cache_read_micro_cny_per_million": 2_000_000,
                    "official_cache_write_micro_cny_per_million": 20_000_000,
                    "supplier_multiplier": Decimal("1"),
                }
            if "SELECT sale_multiplier" in query:
                return {"sale_multiplier": Decimal("0.8")}
            if "SELECT balance_micro_usd, balance_micro_cny" in query:
                return {"balance_micro_usd": 10_000_000, "balance_micro_cny": 20_000_000}
            raise AssertionError(query)

        async def execute(self, query, *args):
            self.executed.append((query, args))
            return "OK"

    conn = Conn()
    result = await accounting.record_usage(
        conn, usage_log_id=99, slot_id="direct-moxing", public_model="glm-5.2",
        upstream_model="kimi-k3", prompt_tokens=524_513, completion_tokens=561,
        cache_read_tokens=523_776, cache_write_tokens=0, request_id="req-1", user_multiplier=1,
        request_status="ok", customer_cost_micro_usd=1_714_000,
        charged_paid_micro_usd=1_714_000, billing_fx_rate=Decimal("6.7648"),
    )
    assert result["official_cost_micro_cny"] == 11_593_912
    assert result["supplier_cost_micro_cny"] == 11_593_912
    assert result["supplier_balance_after_micro_cny"] == 8_406_088
    ledger = next(item for item in conn.executed if "INSERT INTO ak_supplier_ledger" in item[0])
    assert ledger[1][3] == -11_593_912
    assert ledger[1][4] == 8_406_088


@pytest.mark.asyncio
async def test_unlabeled_openai_request_is_rewritten_and_billed_as_glm(monkeypatch):
    monkeypatch.setattr(settings, "MOXING_API_BASE", "https://www.moxing.pro")
    monkeypatch.setattr(settings, "MOXING_API_KEY", "test-secret")
    monkeypatch.setattr(gateway.usage_svc, "precheck", lambda *_args: None)
    passthrough = AsyncMock(return_value="forced-glm")
    monkeypatch.setattr(gateway, "_passthrough_openai", passthrough)
    request = SimpleNamespace(json=AsyncMock(return_value={
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hello"}],
    }))
    auth = {
        "key": {"id": 1},
        "user": {"id": 2, "full_model_access": False},
    }

    result = await gateway.chat_completions(
        ChatCompletionsIn(model="claude-opus-4-8", messages=[]), request, auth,
    )

    assert result == "forced-glm"
    args, kwargs = passthrough.await_args
    assert args[2]["model"] == "glm-5.2"
    slots = kwargs["slot_router"].all_slots()
    assert slots[0].env["ANTHROPIC_MODEL"] == "glm-5.2"
