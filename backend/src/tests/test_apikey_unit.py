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
    # 真实 claude --output-format json：含 cache_read/cache_creation 与 modelUsage（取成本最高者为计价模型）。
    # 缓存 token 单独拆出（不再并进 input），供按官方折扣计价。
    out = ('{"result":"pong","usage":{"input_tokens":2465,"cache_read_input_tokens":16811,'
           '"cache_creation_input_tokens":1801,"output_tokens":4},'
           '"modelUsage":{"claude-haiku-4-5-20251001":{"costUSD":0.0005},"claude-opus-4-8":{"costUSD":0.0321}}}')
    p = _parse_usage(out)
    assert p["in"] == 2465 and p["out"] == 4
    assert p["cache_read"] == 16811 and p["cache_write"] == 1801
    assert p["model"] == "claude-opus-4-8"


def test_cache_price_derivation():
    # 缓存价：表里有非 0 值就用；为 0 时按官方比例从输入价派生（read 10% / write 125%）。
    from services.apikey.pricing import _cache_read_price, _cache_write_price
    # opus-4-8 实付输入价 2500 micro/1k → read=250, write=3125
    p0 = {"input_micro_usd_per_1k": 2500, "cache_read_micro_usd_per_1k": 0, "cache_write_micro_usd_per_1k": 0}
    assert _cache_read_price(p0) == 250
    assert _cache_write_price(p0) == 3125
    # 显式设过价则原样用
    p1 = {"input_micro_usd_per_1k": 2500, "cache_read_micro_usd_per_1k": 99, "cache_write_micro_usd_per_1k": 111}
    assert _cache_read_price(p1) == 99 and _cache_write_price(p1) == 111


def test_inject_cache_breakpoints_marks_tools_system_history():
    from services.apikey.passthrough import inject_cache_breakpoints
    body = {
        "system": [{"type": "text", "text": "sys"}],
        "tools": [{"name": "a"}, {"name": "b"}],
        "messages": [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ],
    }
    inject_cache_breakpoints(body)
    # tools 最后一项打点
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["tools"][0]
    # system 最后一个 text 块打点
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # 倒数第二条 message（a1）打点；最后一条（q2，本轮新输入）不打
    assert isinstance(body["messages"][-2]["content"], list)
    assert body["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(body["messages"][-1]["content"], str)  # 最后一条不动


def test_inject_cache_breakpoints_respects_4_cap():
    from services.apikey.passthrough import inject_cache_breakpoints
    # 客户端已自带 4 个断点 → 不再加，避免超 Anthropic 上限
    body = {
        "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "a", "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "m1", "cache_control": {"type": "ephemeral"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "m2", "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": "m3"},
        ],
    }
    inject_cache_breakpoints(body)
    # 最后一条 m3 仍是 str（没新增断点）
    assert isinstance(body["messages"][-1]["content"], str)


def test_inject_cache_breakpoints_string_system():
    from services.apikey.passthrough import inject_cache_breakpoints
    body = {"system": "just a string", "messages": [{"role": "user", "content": "hi"}]}
    inject_cache_breakpoints(body)
    # str system 被转成带 cache_control 的 block
    assert isinstance(body["system"], list)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_cli_model_normalize():
    assert _cli_model("opus") == "opus"
    assert _cli_model("claude-sonnet-4") == "sonnet"
    assert _cli_model("haiku") == "haiku"
    assert _cli_model("gpt-4o") is None
    assert _cli_model("") is None
    # Fable 5 / Sonnet 5：无短别名，传完整 id
    assert _cli_model("claude-fable-5") == "claude-fable-5"
    assert _cli_model("fable") == "claude-fable-5"
    assert _cli_model("claude-sonnet-5") == "claude-sonnet-5"


def test_normalize_model_new_families():
    from services.apikey.passthrough import normalize_model
    assert normalize_model("fable") == "claude-fable-5"
    assert normalize_model("claude-fable-5") == "claude-fable-5"
    assert normalize_model("sonnet") == "claude-sonnet-5"
    assert normalize_model("sonnet5") == "claude-sonnet-5"
    assert normalize_model("claude-sonnet-5") == "claude-sonnet-5"
    assert normalize_model("claude-sonnet-4.6") == "claude-sonnet-4-6"


def test_is_claude_cli():
    from services.apikey.passthrough import is_claude_cli
    assert is_claude_cli({"user-agent": "claude-cli/2.1.195 (external, cli)"}) is True
    assert is_claude_cli({"x-app": "cli"}) is True
    assert is_claude_cli({"user-agent": "python-httpx/0.27"}) is False
    assert is_claude_cli(None) is False


def test_forwarded_fingerprint_drops_secrets():
    from services.apikey.passthrough import _forwarded_fingerprint
    fp = _forwarded_fingerprint({
        "User-Agent": "claude-cli/2.1.195",
        "anthropic-beta": "fine-grained-tool-streaming-2025-05-14",
        "x-stainless-lang": "js",
        "authorization": "Bearer sk-leak",
        "x-api-key": "sk-leak",
        "host": "example.com",
    })
    assert fp == {
        "user-agent": "claude-cli/2.1.195",
        "anthropic-beta": "fine-grained-tool-streaming-2025-05-14",
        "x-stainless-lang": "js",
    }


def test_inject_identity_idempotent():
    from services.apikey.passthrough import inject_identity, CC_IDENTITY
    # 已含身份（真 CLI 请求）→ 不重复注入
    already = {"system": [{"type": "text", "text": CC_IDENTITY + " ..."}]}
    assert inject_identity(dict(already))["system"] == already["system"]
    # 无身份 → 注入到最前
    got = inject_identity({"system": "hi"})
    assert got["system"][0]["text"] == CC_IDENTITY
    assert got["system"][1]["text"] == "hi"


def test_xunhupay_sign_algorithm():
    import hashlib
    from services.apikey.xunhupay import _sign
    params = {"appid": "123", "total_fee": "10.00", "nonce_str": "abc", "empty": "", "none": None, "hash": "OLD"}
    got = _sign(params, "secret")
    # 去掉 hash 与空/None，按 key 升序拼接，末尾接 secret，md5 小写
    raw = "appid=123&nonce_str=abc&total_fee=10.00" + "secret"
    assert got == hashlib.md5(raw.encode()).hexdigest()
    # 幂等：同参数同签名
    assert _sign(params, "secret") == got
    # 篡改任一值 → 签名变化
    assert _sign({**params, "total_fee": "10.01"}, "secret") != got


def test_xunhupay_configured():
    from services.apikey import xunhupay
    from config.settings import settings
    old_a, old_s = settings.XUNHUPAY_APPID, settings.XUNHUPAY_APPSECRET
    try:
        settings.XUNHUPAY_APPID, settings.XUNHUPAY_APPSECRET = "", ""
        assert xunhupay.configured() is False
        settings.XUNHUPAY_APPID, settings.XUNHUPAY_APPSECRET = "app", "sec"
        assert xunhupay.configured() is True
    finally:
        settings.XUNHUPAY_APPID, settings.XUNHUPAY_APPSECRET = old_a, old_s


def test_xunhupay_out_trade_no_prefix():
    from services.apikey.xunhupay import _new_out_trade_no
    otn = _new_out_trade_no(42)
    assert otn.startswith("sx_42_") and len(otn) > 10
    assert _new_out_trade_no(42) != otn  # 每次不同


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


def test_china_model_currency_classification():
    from services.apikey.usage import is_china_model

    assert is_china_model("glm-5.2")
    assert is_china_model("Kimi-K3")
    assert is_china_model("qwen-max")
    assert is_china_model("deepseek-v3")
    assert not is_china_model("claude-opus-4-8")
    assert not is_china_model("gpt-5.4")
    assert not is_china_model(None)


def test_shell_exec_claude_keeps_prompt_off_argv():
    from services.claude.docker_manager import shell_exec_claude, _PROMPT_FILE

    huge = "x" * 200_000
    argv = shell_exec_claude("u-test", "--output-format", "json")
    joined = " ".join(argv)
    assert argv[0:2] == ["sh", "-lc"]
    assert _PROMPT_FILE in joined and "cat" in joined and "|" in joined
    assert "-p" in joined
    assert "$(cat" not in joined
    assert huge not in joined
