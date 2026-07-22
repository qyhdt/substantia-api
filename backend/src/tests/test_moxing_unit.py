# -*- coding: utf-8 -*-
"""moxing 公开模型与用户路由标签测试（不访问网络/数据库）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config.settings import settings
from controller import gateway
from controller.gateway import ChatCompletionsIn, _force_glm_for_user
from services.moxing import provider as moxing


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
