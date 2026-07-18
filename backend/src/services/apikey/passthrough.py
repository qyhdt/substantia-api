# -*- coding: utf-8 -*-
"""
原生 Messages API 透传（带 tools 的请求走这条，支持 Cursor 等 agent 的工具调用）。

为什么需要：Claude Code CLI 不接收客户端 tools、也不回 tool_use，所以 agent 模式跑不了。
本模块直接拿 slot 的凭据打原生 `api.anthropic.com/v1/messages`：
- subscription slot：用 OAuth access token（Bearer + anthropic-beta: oauth-2025-04-20），
  并在 system 开头注入 Claude Code 身份（否则订阅 OAuth 会被拒）。走订阅、不按 API 官方价。
- api_key slot：转发到 slot 配的 ANTHROPIC_BASE_URL；AUTH_TOKEN 用 Bearer，API_KEY 用 x-api-key。

token 轮换：每次实时读 slot 的 .credentials.json（probe_loop 保活续期）。
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.claude import docker_manager as dm
from services.claude.slots import Slot, SlotType
from services.apikey import public_identity

log = logging.getLogger("ak.passthrough")


class UpstreamConfigurationError(RuntimeError):
    """slot 缺少原生透传所需配置；异常文本不得包含凭据。"""


ANTHROPIC_API = "https://api.anthropic.com"
# 订阅 OAuth 必须的身份系统提示（缺它会被 429/拒）
CC_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# 出站身份对齐真实 Claude Code CLI（抓包自 claude-cli 2.1.195，见 endpoint/capture.py）。
# 否则默认 UA 是 python-httpx/x.y.z，且缺这些头会暴露是代理、订阅 OAuth 更易被风控。
CLAUDE_CLI_VERSION = "2.1.195"
CLAUDE_CLI_UA = f"claude-cli/{CLAUDE_CLI_VERSION} (external, cli)"
# 真实 CLI 默认携带的 claude-code beta（与 oauth/client beta 合并）
CLAUDE_CODE_BETA = "claude-code-20250219"
# 官方 SDK（@anthropic-ai/sdk via Stainless）的固定指纹头
CC_IDENT_HEADERS = {
    "x-app": "cli",
    "anthropic-dangerous-direct-browser-access": "true",
    "x-stainless-lang": "js",
    "x-stainless-runtime": "node",
    "x-stainless-runtime-version": "v26.3.0",
    "x-stainless-package-version": "0.94.0",
    "x-stainless-os": "MacOS",
    "x-stainless-arch": "arm64",
    "x-stainless-retry-count": "0",
    "x-stainless-timeout": "600",
}


def _merge_beta(*parts: Optional[str]) -> str:
    """按顺序去重合并 anthropic-beta 段（claude-code → client → oauth）。"""
    seen: List[str] = []
    for p in parts:
        for b in (p or "").split(","):
            b = b.strip()
            if b and b not in seen:
                seen.append(b)
    return ",".join(seen)


# 来源是真 Claude CLI 时，把它自己的指纹头原样透传（只换 authorization）。
# 只转发这些「安全」的指纹/协议头，绝不转发 authorization / x-api-key（下游 sk-key，
# 不能泄漏到上游）、host、content-length（httpx 自算）等。
_FORWARD_HEADERS = frozenset({
    "user-agent", "anthropic-version", "anthropic-beta",
    "x-app", "anthropic-dangerous-direct-browser-access",
})
_FORWARD_HEADER_PREFIXES = ("x-stainless-",)


def is_claude_cli(client_headers: Optional[Dict[str, str]]) -> bool:
    """入站请求是否来自真实 Claude Code CLI（据 UA / x-app 判断）。"""
    if not client_headers:
        return False
    ua = (client_headers.get("user-agent") or "").lower()
    xapp = (client_headers.get("x-app") or "").lower()
    return ua.startswith("claude-cli/") or xapp == "cli"


def _forwarded_fingerprint(client_headers: Dict[str, str]) -> Dict[str, str]:
    """从入站头挑出可安全转发的指纹头（小写键）。"""
    out: Dict[str, str] = {}
    for k, v in client_headers.items():
        lk = k.lower()
        if lk in _FORWARD_HEADERS or lk.startswith(_FORWARD_HEADER_PREFIXES):
            out[lk] = v
    return out


# 规范 Anthropic model id（透传到原生 API 必须用这些精确名）
_CANON = {
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-fable-5",
}
# 无 family 词、仅版本号 → 按版本归属 family（如 Cursor 里随手起名 "claude4.8"）
_BY_VERSION = {
    "4-8": "claude-opus-4-8", "4-7": "claude-opus-4-7", "4-6": "claude-sonnet-4-6",
    "4-5": "claude-haiku-4-5",
}
_FAMILY_DEFAULT = {
    "opus": "claude-opus-4-8", "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5", "fable": "claude-fable-5",
}


def normalize_model(m: Optional[str]) -> Optional[str]:
    """把宽松/带版本点的模型名归一成 Anthropic 认的规范 id。认不出则原样返回。

    例：claude4.8→claude-opus-4-8，opus→claude-opus-4-8，claude-sonnet-4.6→claude-sonnet-4-6。
    """
    if not m:
        return m
    s = m.strip().lower().replace(" ", "")
    if s in _CANON:
        return s
    s2 = s.replace(".", "-")
    if s2 in _CANON:
        return s2
    fam = next((f for f in _FAMILY_DEFAULT if f in s), None)
    ver = re.search(r"(\d+)[.\-](\d+)", s)
    if fam and ver:
        cand = f"claude-{fam}-{ver.group(1)}-{ver.group(2)}"
        return cand if cand in _CANON else _FAMILY_DEFAULT[fam]
    if fam:
        return _FAMILY_DEFAULT[fam]
    # 无 family 词、只有版本（claude4.8 / claude-4-6 ...）→ 按版本归属
    only = re.search(r"^claude-?(\d+)[.\-](\d+)$", s)
    if only:
        return _BY_VERSION.get(f"{only.group(1)}-{only.group(2)}", m)
    return m


def slot_oauth_token(slot: Slot) -> Optional[str]:
    """读 subscription slot 的 OAuth access token（实时，应对轮换）。"""
    if slot.type != SlotType.SUBSCRIPTION:
        return None
    try:
        p = dm.slot_creds_dir(slot) / ".credentials.json"
        o = json.loads(p.read_text(encoding="utf-8"))
        return (o.get("claudeAiOauth") or o).get("accessToken")
    except Exception as e:  # noqa: BLE001
        log.warning("read oauth token failed for slot %s: %s", slot.id, e)
        return None


def _has_identity(sys: Any) -> bool:
    """system 里是否已带 Claude Code 身份（真 CLI 请求自带，避免重复注入）。"""
    if isinstance(sys, str):
        return CC_IDENTITY in sys
    if isinstance(sys, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" and CC_IDENTITY in (b.get("text") or "")
            for b in sys
        )
    return False


def inject_identity(body: Dict[str, Any]) -> Dict[str, Any]:
    """把 Claude Code 身份块放到 system 最前（保留客户端原有 system 在其后）。
    幂等：若 system 里已含该身份（如来源就是真 CLI），原样返回不重复注入。"""
    sys = body.get("system")
    if _has_identity(sys):
        return body
    cc = {"type": "text", "text": CC_IDENTITY}
    if sys is None:
        body["system"] = [cc]
    elif isinstance(sys, str):
        body["system"] = [cc, {"type": "text", "text": sys}]
    elif isinstance(sys, list):
        body["system"] = [cc, *sys]
    return body


# prompt caching：Anthropic 一次请求最多 4 个 cache_control 断点。
_CACHE_CONTROL = {"type": "ephemeral"}
_MAX_BREAKPOINTS = 4


def _mark(block: Any) -> bool:
    """给一个 content block 打 cache_control（仅 dict 块支持）。已打过则视为成功不重复。返回是否占用一个断点。"""
    if not isinstance(block, dict):
        return False
    if "cache_control" in block:
        return False  # 已有（客户端自己打的），不重复占额度
    block["cache_control"] = dict(_CACHE_CONTROL)
    return True


def inject_cache_breakpoints(body: Dict[str, Any]) -> Dict[str, Any]:
    """给透传请求体注入 prompt caching 断点，让多轮 Agent（Cursor 等）重复的
    system / tools / 历史消息命中缓存（cache_read 仅 10% 价），大幅降本。

    打点策略（按"越稳定越靠前"，至多 4 个，倒序消耗额度以优先缓存最大、最稳定的前缀）：
      1) tools 最后一项     —— 工具定义每轮不变，通常最大块
      2) system 最后一块    —— 系统提示稳定
      3) 倒数第二条 message  —— 缓存到上一轮为止的历史（本轮新增之前的全部前缀）
    客户端已自带 cache_control 时尊重其断点、不重复占额度。就地修改并返回 body。
    """
    budget = _MAX_BREAKPOINTS
    # 统计客户端已用掉的断点，避免超过 4 个被 Anthropic 拒
    used = _count_existing_breakpoints(body)
    budget -= used
    if budget <= 0:
        return body

    # 1) tools[-1]
    tools = body.get("tools")
    if budget > 0 and isinstance(tools, list) and tools:
        if _mark(tools[-1]):
            budget -= 1

    # 2) system[-1]（system 为 list 时打最后一个 text 块；为 str 时转成 block 再打）
    if budget > 0:
        sys = body.get("system")
        if isinstance(sys, str) and sys:
            body["system"] = [{"type": "text", "text": sys, "cache_control": dict(_CACHE_CONTROL)}]
            budget -= 1
        elif isinstance(sys, list) and sys:
            for blk in reversed(sys):
                if isinstance(blk, dict) and blk.get("type") == "text":
                    if _mark(blk):
                        budget -= 1
                    break

    # 3) 倒数第二条 message 的最后一个可缓存块（缓存历史前缀；留最后一条为"新输入"全价）
    if budget > 0:
        msgs = body.get("messages")
        if isinstance(msgs, list) and len(msgs) >= 2:
            if _mark_message_tail(msgs[-2]):
                budget -= 1

    return body


def _mark_message_tail(msg: Any) -> bool:
    """给一条 message 的内容打一个断点：content 为 str → 转 block 打；为 list → 打最后一个 dict 块。"""
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, str) and content:
        msg["content"] = [{"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL)}]
        return True
    if isinstance(content, list):
        for blk in reversed(content):
            if isinstance(blk, dict):
                return _mark(blk)
    return False


def _count_existing_breakpoints(body: Dict[str, Any]) -> int:
    """统计 body 里已存在的 cache_control 数（客户端自带的），用于不超 4 个上限。"""
    n = 0
    sys = body.get("system")
    if isinstance(sys, list):
        n += sum(1 for b in sys if isinstance(b, dict) and "cache_control" in b)
    for t in (body.get("tools") or []):
        if isinstance(t, dict) and "cache_control" in t:
            n += 1
    for m in (body.get("messages") or []):
        c = isinstance(m, dict) and m.get("content")
        if isinstance(c, list):
            n += sum(1 for b in c if isinstance(b, dict) and "cache_control" in b)
    return n


def upstream_for(slot: Slot, client_headers: Optional[Dict[str, str]] = None) -> Tuple[str, Dict[str, str], bool]:
    """返回 (base_url, headers, is_oauth)。is_oauth=True 表示需要注入身份。

    client_headers：入站请求头。来源若是真 Claude CLI（is_claude_cli），则原样转发它
    自己的指纹头（UA / anthropic-beta / x-stainless-* 等），只把 authorization 换成 slot
    的凭据——上游看到的就是一个真实 CLI 请求。否则合成对齐真 CLI 的指纹头（供 SDK/curl 用）。
    """
    client_headers = client_headers or {}
    cli = is_claude_cli(client_headers)
    client_beta = client_headers.get("anthropic-beta")

    if slot.type == SlotType.SUBSCRIPTION:
        tok = slot_oauth_token(slot)
        if not tok:
            raise RuntimeError(f"slot {slot.id} 无 OAuth token（凭据未就绪）")
        if cli:
            headers = _forwarded_fingerprint(client_headers)
            headers["authorization"] = f"Bearer {tok}"
            headers["content-type"] = "application/json"
            headers.setdefault("anthropic-version", "2023-06-01")
            headers["anthropic-beta"] = _merge_beta(headers.get("anthropic-beta"), "oauth-2025-04-20")
            return ANTHROPIC_API, headers, True
        betas = _merge_beta(CLAUDE_CODE_BETA, client_beta, "oauth-2025-04-20")
        return ANTHROPIC_API, {
            "authorization": f"Bearer {tok}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": betas,
            "content-type": "application/json",
            "user-agent": CLAUDE_CLI_UA,
            **CC_IDENT_HEADERS,
        }, True

    # api_key slot：转发到其配置的端点，用其 key
    env = slot.env or {}
    base = (env.get("ANTHROPIC_BASE_URL") or ANTHROPIC_API).rstrip("/")
    auth_token = (env.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    api_key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if cli:
        headers = _forwarded_fingerprint(client_headers)
        headers["content-type"] = "application/json"
        headers.setdefault("anthropic-version", "2023-06-01")
    else:
        headers = {"anthropic-version": "2023-06-01", "content-type": "application/json",
                   "user-agent": CLAUDE_CLI_UA}
        if client_beta:
            headers["anthropic-beta"] = client_beta
    # Claude Code 官方约定：ANTHROPIC_AUTH_TOKEN 是 Bearer token；只有
    # ANTHROPIC_API_KEY 才走 x-api-key。两者并存时 AUTH_TOKEN 优先。
    if auth_token:
        headers["authorization"] = f"Bearer {auth_token}"
    elif api_key:
        headers["x-api-key"] = api_key
    else:
        raise UpstreamConfigurationError(f"slot {slot.id} 缺少 API 凭据")
    return base, headers, False


def body_for_slot(slot: Slot, raw: Dict[str, Any], *, oauth: bool) -> Dict[str, Any]:
    """从客户端原始 body 为一次 slot attempt 构造独立请求体。

    每次 deep-copy 可避免前一档注入的身份、cache breakpoint 或 Gemini model
    残留到下一档 GLM。API-key slot 必须用自身 ``ANTHROPIC_MODEL`` 覆盖客户端
    Claude model；对外响应和计费模型由 gateway 单独保持为客户端模型。
    """
    body = copy.deepcopy(raw)
    if slot.type == SlotType.API_KEY:
        # ``body.model`` 此刻仍是客户端规范化后的 Claude model。先用它建立公开
        # 身份契约，再换成本档内部 deployment model；不安全/非 Claude 型号不插值。
        public_identity.inject_public_identity_policy(body, body.get("model"))
        configured_model = ((slot.env or {}).get("ANTHROPIC_MODEL") or "").strip()
        if not configured_model:
            raise UpstreamConfigurationError(f"slot {slot.id} 缺少 ANTHROPIC_MODEL")
        body["model"] = configured_model
    if oauth:
        inject_identity(body)
    inject_cache_breakpoints(body)
    return body


# ============================ OpenAI ↔ Anthropic 协议翻译 ============================
def _oai_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content)


def _oai_user_content(content: Any):
    """user/tool 文本或多模态 → Anthropic content（str 或 blocks）。"""
    if isinstance(content, str) or content is None:
        return content or ""
    if isinstance(content, list):
        blocks: List[dict] = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                blocks.append({"type": "text", "text": p.get("text", "")})
            elif p.get("type") == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if url.startswith("data:") and "," in url:
                    head, data = url.split(",", 1)
                    mt = head.split(":", 1)[1].split(";", 1)[0] if ":" in head else "image/png"
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}})
                elif url:
                    blocks.append({"type": "image", "source": {"type": "url", "url": url}})
        return blocks or ""
    return str(content)


def openai_to_anthropic(raw: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI ChatCompletions 请求 → Anthropic Messages 请求体。"""
    system_parts: List[str] = []
    a_msgs: List[dict] = []
    pending_results: List[dict] = []

    def flush():
        nonlocal pending_results
        if pending_results:
            a_msgs.append({"role": "user", "content": pending_results})
            pending_results = []

    for m in raw.get("messages") or []:
        role = m.get("role")
        if role == "system":
            system_parts.append(_oai_text(m.get("content")))
            continue
        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or "",
                "content": _oai_text(m.get("content")),
            })
            continue
        flush()
        if role == "assistant":
            blocks: List[dict] = []
            txt = _oai_text(m.get("content"))
            if txt:
                blocks.append({"type": "text", "text": txt})
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    inp = {}
                blocks.append({"type": "tool_use", "id": tc.get("id") or "", "name": fn.get("name") or "", "input": inp})
            a_msgs.append({"role": "assistant", "content": blocks if blocks else txt})
        else:
            a_msgs.append({"role": "user", "content": _oai_user_content(m.get("content"))})
    flush()

    body: Dict[str, Any] = {
        "model": raw.get("model"),
        "max_tokens": int(raw.get("max_tokens") or raw.get("max_completion_tokens") or 4096),
        "messages": a_msgs,
    }
    if system_parts:
        body["system"] = "\n\n".join([s for s in system_parts if s])
    tools = raw.get("tools")
    if tools:
        body["tools"] = [
            {
                "name": (t.get("function") or {}).get("name"),
                "description": (t.get("function") or {}).get("description") or "",
                "input_schema": (t.get("function") or {}).get("parameters") or {"type": "object", "properties": {}},
            }
            for t in tools if (t.get("type", "function") == "function")
        ]
    tc = raw.get("tool_choice")
    if tc == "auto":
        body["tool_choice"] = {"type": "auto"}
    elif tc == "required":
        body["tool_choice"] = {"type": "any"}
    elif isinstance(tc, dict) and tc.get("type") == "function":
        body["tool_choice"] = {"type": "tool", "name": (tc.get("function") or {}).get("name")}
    # tc == "none" 或缺省：不传，用默认 auto
    return body


_STOP_MAP = {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop", "tool_use": "tool_calls"}


def anthropic_to_openai(data: Dict[str, Any]) -> Dict[str, Any]:
    """Anthropic Messages 响应 → OpenAI ChatCompletion。"""
    text_parts: List[str] = []
    tool_calls: List[dict] = []
    for b in data.get("content") or []:
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id"),
                "type": "function",
                "function": {"name": b.get("name"), "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
            })
    msg: Dict[str, Any] = {"role": "assistant", "content": ("".join(text_parts) or None)}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    u = data.get("usage") or {}
    # OpenAI 的 prompt_tokens 用于回给客户端展示：含新输入 + 缓存读 + 缓存写（与 total 口径一致）。
    # 真实计费在 gateway 用 _usage_anthropic 拆分后按各自单价算，不走这里。
    in_tok = int(u.get("input_tokens", 0) or 0)
    cache_read = int(u.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(u.get("cache_creation_input_tokens", 0) or 0)
    p = in_tok + cache_read + cache_write
    c = int(u.get("output_tokens", 0) or 0)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data.get("model"),
        "choices": [{"index": 0, "message": msg, "finish_reason": _STOP_MAP.get(data.get("stop_reason"), "stop")}],
        "usage": {
            "prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c,
            "prompt_tokens_details": {"cached_tokens": cache_read},
        },
    }


def openai_stream_chunks(comp: Dict[str, Any]) -> List[str]:
    """把完整 OpenAI completion 拆成 SSE chunk 序列（含 tool_calls）。"""
    cid, created, model = comp["id"], comp["created"], comp["model"]
    choice = comp["choices"][0]
    msg = choice["message"]

    def frame(delta: dict, finish=None) -> str:
        return "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }, ensure_ascii=False) + "\n\n"

    out = [frame({"role": "assistant"})]
    if msg.get("content"):
        out.append(frame({"content": msg["content"]}))
    for i, tc in enumerate(msg.get("tool_calls") or []):
        out.append(frame({"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                          "function": {"name": tc["function"]["name"], "arguments": ""}}]}))
        out.append(frame({"tool_calls": [{"index": i, "function": {"arguments": tc["function"]["arguments"]}}]}))
    out.append(frame({}, choice["finish_reason"]))
    out.append("data: [DONE]\n\n")
    return out
