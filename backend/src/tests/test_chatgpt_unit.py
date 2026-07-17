# -*- coding: utf-8 -*-
"""ChatGPT 上游纯逻辑测试（不碰 docker / 网络）：模型识别 + codex JSONL 解析 + openai 用量解析。

跑法（backend/src 下）：python -m pytest tests/test_chatgpt_unit.py -q
"""
from services.chatgpt.models import is_chatgpt_model, normalize_model
from services.chatgpt.codex import _StreamAcc
from services.chatgpt import openai_api


def test_is_chatgpt_model():
    for m in ["gpt-5", "gpt-4o", "gpt-4.1-mini", "o3", "o1-mini", "o4-mini",
              "chatgpt-4o-latest", "codex-mini", "GPT-5"]:
        assert is_chatgpt_model(m), m
    for m in ["claude-sonnet-5", "claude-opus-4-8", "opus", "haiku", "", None]:
        assert not is_chatgpt_model(m), m


def test_normalize_model_default():
    assert normalize_model("", "gpt-5") == "gpt-5"
    assert normalize_model("  gpt-4o ", "gpt-5") == "gpt-4o"
    assert normalize_model(None, "gpt-5") == "gpt-5"


def test_codex_stream_full_message_and_usage():
    acc = _StreamAcc()
    acc.feed('{"type":"item.completed","item":{"type":"agent_message","id":"m1","text":"Hello world"}}')
    acc.feed('{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":20,'
             '"output_tokens":30,"reasoning_output_tokens":5}}')
    r = acc.result("gpt-5", "acc1")
    assert r.text == "Hello world"
    assert r.prompt_tokens == 80          # 100 - 20 cached
    assert r.completion_tokens == 35      # 30 + 5 reasoning
    assert r.cache_read_tokens == 20
    assert r.provider == "codex"
    assert r.slot_id == "codex:acc1"


def test_codex_stream_incremental_dedup():
    """增量(item.updated) 后又来全量(item.completed) 不应重复吐字。"""
    acc = _StreamAcc()
    acc.feed('{"type":"item.updated","item":{"type":"agent_message","id":"m1","text":"Hello"}}')
    acc.feed('{"type":"item.completed","item":{"type":"agent_message","id":"m1","text":"Hello world"}}')
    assert acc.result("gpt-5", "a").text == "Hello world"


def test_codex_stream_turn_failed():
    acc = _StreamAcc()
    acc.feed('{"type":"turn.failed","error":{"message":"model not supported"}}')
    assert acc.error and "model not supported" in acc.error


def test_openai_usage_parse():
    data = {
        "model": "gpt-5",
        "choices": [{"message": {"content": "hi there"}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 12,
                  "prompt_tokens_details": {"cached_tokens": 10}},
    }
    prompt, completion, cached = openai_api._usage(data)
    assert (prompt, completion, cached) == (40, 12, 10)  # 50 - 10 cached
    assert openai_api._text(data) == "hi there"
