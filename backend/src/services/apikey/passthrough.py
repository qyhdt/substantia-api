# -*- coding: utf-8 -*-
"""
原生 Messages API 透传（带 tools 的请求走这条，支持 Cursor 等 agent 的工具调用）。

为什么需要：Claude Code CLI 不接收客户端 tools、也不回 tool_use，所以 agent 模式跑不了。
本模块直接拿 slot 的凭据打原生 `api.anthropic.com/v1/messages`：
- subscription slot：用 OAuth access token（Bearer + anthropic-beta: oauth-2025-04-20），
  并在 system 开头注入 Claude Code 身份（否则订阅 OAuth 会被拒）。走订阅、不按 API 官方价。
- api_key slot：转发到 slot 配的 ANTHROPIC_BASE_URL，用其 key（x-api-key）。

token 轮换：每次实时读 slot 的 .credentials.json（probe_loop 保活续期）。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.claude import docker_manager as dm
from services.claude.slots import Slot, SlotType

log = logging.getLogger("ak.passthrough")

ANTHROPIC_API = "https://api.anthropic.com"
# 订阅 OAuth 必须的身份系统提示（缺它会被 429/拒）
CC_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


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


def inject_identity(body: Dict[str, Any]) -> Dict[str, Any]:
    """把 Claude Code 身份块放到 system 最前（保留客户端原有 system 在其后）。"""
    cc = {"type": "text", "text": CC_IDENTITY}
    sys = body.get("system")
    if sys is None:
        body["system"] = [cc]
    elif isinstance(sys, str):
        body["system"] = [cc, {"type": "text", "text": sys}]
    elif isinstance(sys, list):
        body["system"] = [cc, *sys]
    return body


def upstream_for(slot: Slot, client_beta: Optional[str] = None) -> Tuple[str, Dict[str, str], bool]:
    """返回 (base_url, headers, is_oauth)。is_oauth=True 表示需要注入身份。"""
    if slot.type == SlotType.SUBSCRIPTION:
        tok = slot_oauth_token(slot)
        if not tok:
            raise RuntimeError(f"slot {slot.id} 无 OAuth token（凭据未就绪）")
        betas = "oauth-2025-04-20"
        if client_beta and "oauth-2025-04-20" not in client_beta:
            betas = f"{client_beta},oauth-2025-04-20"
        return ANTHROPIC_API, {
            "authorization": f"Bearer {tok}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": betas,
            "content-type": "application/json",
        }, True
    # api_key slot：转发到其配置的端点，用其 key
    env = slot.env or {}
    base = (env.get("ANTHROPIC_BASE_URL") or ANTHROPIC_API).rstrip("/")
    key = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
    if client_beta:
        headers["anthropic-beta"] = client_beta
    if key:
        headers["x-api-key"] = key
    return base, headers, False


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
    p = int(u.get("input_tokens", 0) or 0) + int(u.get("cache_read_input_tokens", 0) or 0)
    c = int(u.get("output_tokens", 0) or 0)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data.get("model"),
        "choices": [{"index": 0, "message": msg, "finish_reason": _STOP_MAP.get(data.get("stop_reason"), "stop")}],
        "usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c},
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
