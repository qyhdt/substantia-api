# -*- coding: utf-8 -*-
"""APIKey 分发的纯逻辑单测（无需 DB / docker）。"""
from controller.gateway import _build_prompt, _safe_uid, _text_of, MessagesIn
from security.api_key_auth import KEY_PREFIX, generate_key, hash_key
from services.apikey import to_micro, usd
from services.apikey.runner import _cli_model, _estimate_tokens, _parse_usage


def test_micro_usd_roundtrip():
    assert to_micro(20) == 20_000_000
    assert usd(20_000_000) == 20.0
    assert to_micro(0.000001) == 1


def test_generate_key_shape():
    plain, prefix, h = generate_key()
    assert plain.startswith(KEY_PREFIX)
    assert prefix.startswith(KEY_PREFIX) and prefix.endswith("…")
    assert h == hash_key(plain) and len(h) == 64
    # 两次生成不同
    assert generate_key()[0] != plain


def test_text_of_variants():
    assert _text_of("hi") == "hi"
    assert _text_of([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert _text_of(None) == ""


def test_build_prompt_single_user_is_raw():
    p = MessagesIn(messages=[{"role": "user", "content": "hello world"}])
    assert _build_prompt(p) == "hello world"


def test_build_prompt_with_system_and_turns():
    p = MessagesIn(
        system="be brief",
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
    )
    out = _build_prompt(p)
    assert "System: be brief" in out and "User: hi" in out and "Assistant: yo" in out


def test_safe_uid_stable_and_safe():
    import re
    a = _safe_uid({"id": 42})
    b = _safe_uid({"id": 42})
    assert a == b and re.match(r"^[A-Za-z0-9_-]{1,64}$", a)


def test_parse_usage_json():
    out = '{"type":"result","result":"hello","usage":{"input_tokens":10,"output_tokens":5}}'
    parsed = _parse_usage(out)
    assert parsed["text"] == "hello" and parsed["in"] == 10 and parsed["out"] == 5
    assert parsed["model"] is None  # 无 modelUsage


def test_parse_usage_real_claude_output():
    # 真实 claude --output-format json：含 cache_read 与 modelUsage（取成本最高者为计价模型）
    out = ('{"result":"pong","usage":{"input_tokens":2465,"cache_read_input_tokens":16811,"output_tokens":4},'
           '"modelUsage":{"claude-haiku-4-5-20251001":{"costUSD":0.0005},"claude-opus-4-8":{"costUSD":0.0321}}}')
    p = _parse_usage(out)
    assert p["in"] == 2465 + 16811 and p["out"] == 4
    assert p["model"] == "claude-opus-4-8"


def test_cli_model_normalize():
    assert _cli_model("opus") == "opus"
    assert _cli_model("claude-sonnet-4") == "sonnet"
    assert _cli_model("haiku") == "haiku"
    assert _cli_model("gpt-4o") is None
    assert _cli_model("") is None


def test_parse_usage_embedded_json():
    out = 'some log line\n{"result":"hi","usage":{"input_tokens":3,"output_tokens":2}}\n'
    parsed = _parse_usage(out)
    assert parsed["in"] == 3 and parsed["out"] == 2 and parsed["text"] == "hi"


def test_parse_usage_garbage_returns_none():
    assert _parse_usage("not json at all") is None
    assert _parse_usage("") is None


def test_estimate_tokens():
    assert _estimate_tokens("") == 1
    assert _estimate_tokens("abcd" * 10) == 10


def test_shell_exec_claude_keeps_prompt_off_argv():
    from services.claude.docker_manager import shell_exec_claude, _PROMPT_FILE

    huge = "x" * 200_000
    argv = shell_exec_claude("u-test", "--output-format", "json")
    joined = " ".join(argv)
    assert argv[0:2] == ["sh", "-lc"]
    assert _PROMPT_FILE in joined and "cat" in joined
    assert huge not in joined
