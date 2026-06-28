# -*- coding: utf-8 -*-
"""
网关 /v1/messages —— Anthropic Messages 兼容（sk-key 鉴权，不走 JWT）。

链路：鉴权 sk-key → 余额/封顶/模型白名单前置校验 → 把 messages 摊平成 prompt →
services.apikey.runner.run（复用容器团队路由 + 故障转移，sub 用光自动接 api_key slot）→
按命中模型计费扣余额 + 记 usage → 回 Anthropic 风格响应（支持 stream）。
"""
import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from pydantic import BaseModel, Field

from frame.sse import encode_event, sse_response
from security.api_key_auth import authenticate_key
from services.apikey import passthrough as pt
from services.apikey import pricing  # noqa: F401  (用户用到，保留)
from services.apikey import runner
from services.apikey import usage as usage_svc
from services.claude import docker_manager as dm
from services.claude.registry import get_router
from config.settings import settings
from utils.pm_logger import get_app_logger
from utils.request_context import request_context

router = APIRouter(prefix="/v1", tags=["gateway"])
log = get_app_logger()


class MessagesIn(BaseModel):
    model: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    max_tokens: Optional[int] = None
    stream: bool = False


def _safe_uid(user: dict) -> str:
    """与 controller/claude.py 的 _safe_uid 保持一致：同一用户经 /v1/messages 或
    /claude/chat 都路由到同一 slot、同一容器工作目录。"""
    raw = str(user.get("id") or user.get("email") or "anon")
    return "u-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _text_of(content: Any) -> str:
    """Anthropic content 可为 str 或 [{type:text,text:..}]；抽出纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    out.append(str(block.get("text", "")))
            elif isinstance(block, str):
                out.append(block)
        return "\n".join(out)
    return str(content)


def _build_prompt(payload: MessagesIn) -> str:
    system = _text_of(payload.system)
    msgs = payload.messages or []
    # 常见单轮：无 system + 单条 user → 直接用其文本，避免给 claude 加噪声前缀
    if not system and len(msgs) == 1 and msgs[0].get("role", "user") == "user":
        return _text_of(msgs[0].get("content"))
    parts = []
    if system:
        parts.append(f"System: {system}")
    for m in msgs:
        role = (m.get("role") or "user").capitalize()
        parts.append(f"{role}: {_text_of(m.get('content'))}")
    return "\n\n".join(parts).strip()


def _anthropic_response(result: runner.RunnerResult, model: str) -> Dict[str, Any]:
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": result.text or ""}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": result.prompt_tokens,
            "output_tokens": result.completion_tokens,
        },
    }


def _sse_stream(resp: Dict[str, Any]):
    """把完整响应转成 Anthropic SSE 事件序列（一次性把全文作为一个 text_delta 发出）。"""
    msg_id = resp["id"]
    text = resp["content"][0]["text"]
    usage = resp["usage"]

    async def gen():
        yield encode_event(
            {"type": "message_start", "message": {**resp, "content": [], "usage": {
                "input_tokens": usage["input_tokens"], "output_tokens": 0}}},
            event="message_start",
        )
        yield encode_event(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            event="content_block_start",
        )
        yield encode_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": text}},
            event="content_block_delta",
        )
        yield encode_event({"type": "content_block_stop", "index": 0}, event="content_block_stop")
        yield encode_event(
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
             "usage": {"output_tokens": usage["output_tokens"]}},
            event="message_delta",
        )
        yield encode_event({"type": "message_stop"}, event="message_stop")

    return sse_response(gen())


async def _run_and_bill(key: dict, user: dict, model: str, prompt: str, request: Request):
    """跑 runner + 计费 + 记 usage（Anthropic / OpenAI 两个入口共用）。返回 (result, billed)。失败抛 HTTPException。"""
    request_id = (request_context.get({}) or {}).get("trace_id")
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(runner.run, _safe_uid(user), prompt, model)
    except Exception as e:
        from services.claude.router import NoRoutableSlotError
        code = 503 if isinstance(e, NoRoutableSlotError) else 502
        await usage_svc.record_and_charge(
            api_key_id=key["id"], user_id=user["id"], slot_id=None, model=model,
            prompt_tokens=0, completion_tokens=0, latency_ms=int((time.monotonic() - started) * 1000),
            status_str="error", error_code=type(e).__name__, request_id=request_id,
        )
        log.warning("gateway upstream error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=code, detail=f"upstream unavailable: {e}")

    latency_ms = int((time.monotonic() - started) * 1000)
    if result is None or (not result.ok and result.auth_failed):
        await usage_svc.record_and_charge(
            api_key_id=key["id"], user_id=user["id"],
            slot_id=(result.slot_id if result else None), model=model,
            prompt_tokens=0, completion_tokens=0, latency_ms=latency_ms,
            attempts=(result.attempts if result else 1),
            status_str="error", error_code="auth_failed", request_id=request_id,
        )
        raise HTTPException(status_code=502, detail="all upstream credentials failed auth")

    billed = await usage_svc.record_and_charge(
        api_key_id=key["id"], user_id=user["id"], slot_id=result.slot_id, model=result.model,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        latency_ms=latency_ms, attempts=result.attempts,
        status_str=("ok" if result.ok else "error"),
        error_code=(None if result.ok else f"exit_{result.exit_code}"),
        request_id=request_id,
    )
    return result, billed


# ============================ 原生透传（带 tools 走这条，支持 agent 工具调用）============================
async def _bill_pt(key, user, model, in_tok, out_tok, latency_ms, request, *, slot_id=None,
                   status="ok", error_code=None):
    request_id = (request_context.get({}) or {}).get("trace_id")
    return await usage_svc.record_and_charge(
        api_key_id=key["id"], user_id=user["id"], slot_id=slot_id, model=model,
        prompt_tokens=in_tok, completion_tokens=out_tok, latency_ms=latency_ms,
        attempts=1, status_str=status, error_code=error_code, request_id=request_id,
    )


def _usage_anthropic(data: dict):
    u = (data or {}).get("usage") or {}
    return (int(u.get("input_tokens", 0) or 0) + int(u.get("cache_read_input_tokens", 0) or 0),
            int(u.get("output_tokens", 0) or 0))


async def _passthrough_anthropic(key: dict, user: dict, raw: dict, request: Request):
    """带 tools 的请求：拿 slot 凭据直打 api.anthropic.com/v1/messages，原样转发/回传。"""
    router = get_router()
    uid = _safe_uid(user)
    raw = {**raw, "model": pt.normalize_model(raw.get("model")) or settings.AK_DEFAULT_MODEL}
    model = raw["model"]
    stream = bool(raw.get("stream"))
    client_beta = request.headers.get("anthropic-beta")
    max_attempts = max(1, settings.CLAUDE_EXEC_MAX_ATTEMPTS)

    async def pick():
        s = router.route(uid)
        try:
            await asyncio.to_thread(dm.ensure_slot_container, s)  # 确保订阅凭据文件就绪
        except Exception:
            pass
        return s

    if not stream:
        last = None
        for _ in range(max_attempts):
            slot = await pick()
            base, headers, oauth = pt.upstream_for(slot, client_beta)
            body = pt.inject_identity(dict(raw)) if oauth else dict(raw)
            started = time.monotonic()
            async with httpx.AsyncClient(timeout=300.0) as c:
                r = await c.post(f"{base}/v1/messages", headers=headers, json=body)
            latency = int((time.monotonic() - started) * 1000)
            if r.status_code == 401 and oauth:
                router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
                last = r
                continue
            try:
                data = r.json()
            except Exception:
                data = {"error": {"type": "upstream", "message": r.text[:500]}}
            if r.status_code < 300:
                in_tok, out_tok = _usage_anthropic(data)
                await _bill_pt(key, user, data.get("model") or model, in_tok, out_tok, latency, request, slot_id=slot.id)
            return JSONResponse(data, status_code=r.status_code)
        return JSONResponse(
            (last.json() if last is not None else {"error": "all upstream credentials failed auth"}),
            status_code=(last.status_code if last is not None else 502),
        )

    # 流式：单次尝试，逐字节原样回传，结束后按 usage 计费
    slot = await pick()
    base, headers, oauth = pt.upstream_for(slot, client_beta)
    body = pt.inject_identity(dict(raw)) if oauth else dict(raw)
    started = time.monotonic()

    async def gen():
        buf: List[bytes] = []
        try:
            async with httpx.AsyncClient(timeout=600.0) as c:
                async with c.stream("POST", f"{base}/v1/messages", headers=headers, json=body) as r:
                    if r.status_code >= 400:
                        err = await r.aread()
                        if r.status_code == 401 and oauth:
                            router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
                        yield "event: error\ndata: " + err.decode("utf-8", "replace") + "\n\n"
                        return
                    async for ch in r.aiter_bytes():
                        buf.append(ch)
                        yield ch
        finally:
            text = b"".join(buf).decode("utf-8", "replace")
            mi = re.search(r'"input_tokens"\s*:\s*(\d+)', text)
            mo = re.findall(r'"output_tokens"\s*:\s*(\d+)', text)
            in_tok = int(mi.group(1)) if mi else 0
            out_tok = int(mo[-1]) if mo else 0
            await _bill_pt(key, user, model, in_tok, out_tok,
                           int((time.monotonic() - started) * 1000), request, slot_id=slot.id)

    return sse_response(gen())


@router.post("/messages", summary="Anthropic Messages 兼容入口（sk-key；带 tools 自动走原生透传）")
async def messages(payload: MessagesIn, request: Request, auth: dict = Depends(authenticate_key)):
    key, user = auth["key"], auth["user"]
    model = pt.normalize_model(payload.model) or settings.AK_DEFAULT_MODEL
    usage_svc.precheck(key, user, model)  # 有效余额 / key 封顶 / 模型白名单

    # 带 tools → 原生透传（支持 agent 工具调用）；否则走 CLI（便宜）
    raw = await request.json()
    if raw.get("tools"):
        return await _passthrough_anthropic(key, user, raw, request)

    prompt = _build_prompt(payload)
    if not prompt:
        raise HTTPException(status_code=400, detail="empty prompt")

    result, billed = await _run_and_bill(key, user, model, prompt, request)
    resp = _anthropic_response(result, result.model)
    resp["_substantia"] = {
        "slot_id": result.slot_id, "slot_type": result.slot_type,
        "cost_micro_usd": billed["cost_micro_usd"], "attempts": result.attempts,
        "estimated_tokens": result.estimated,
    }
    if not result.ok:
        resp["stop_reason"] = "error"
    if payload.stream:
        return _sse_stream(resp)
    return JSONResponse(resp)


# ============================ OpenAI Chat Completions 兼容 ============================
class ChatCompletionsIn(BaseModel):
    model: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    max_tokens: Optional[int] = None
    stream: bool = False


def _openai_response(result: runner.RunnerResult, model: str) -> Dict[str, Any]:
    p = result.prompt_tokens or 0
    c = result.completion_tokens or 0
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.text or ""},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c},
    }


def _openai_sse_stream(resp: Dict[str, Any]):
    """OpenAI 流式：data: {chat.completion.chunk}\\n\\n ... data: [DONE]。整段文本作为一个 delta。"""
    text = resp["choices"][0]["message"]["content"]
    cid, created, model = resp["id"], resp["created"], resp["model"]

    def chunk(delta: dict, finish):
        return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    async def gen():
        yield "data: " + json.dumps(chunk({"role": "assistant"}, None), ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps(chunk({"content": text}, None), ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps(chunk({}, "stop"), ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return sse_response(gen())


@router.get("/models", summary="OpenAI Models 列表（Cursor BYOK 验证用）")
async def list_models(auth: dict = Depends(authenticate_key)):
    key = auth["key"]
    allowed = key.get("allowed_models")
    if isinstance(allowed, str):
        try:
            allowed = json.loads(allowed)
        except Exception:
            allowed = None

    rows = await pricing.list_prices()
    names = [r["model"] for r in rows if r.get("enabled", True)]
    if allowed:
        names = [m for m in names if m in allowed]
    if not names:
        names = [settings.AK_DEFAULT_MODEL]

    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "substantia"}
            for m in names
        ],
    }


async def _passthrough_openai(key: dict, user: dict, raw: dict, request: Request):
    """OpenAI 带 tools 的请求：翻译成 Anthropic → 原生透传（非流式上游）→ 翻译回 OpenAI。"""
    router = get_router()
    uid = _safe_uid(user)
    raw = {**raw, "model": pt.normalize_model(raw.get("model")) or settings.AK_DEFAULT_MODEL}
    model = raw["model"]
    want_stream = bool(raw.get("stream"))
    anth = pt.openai_to_anthropic(raw)
    anth["stream"] = False
    client_beta = request.headers.get("anthropic-beta")
    max_attempts = max(1, settings.CLAUDE_EXEC_MAX_ATTEMPTS)

    last = None
    for _ in range(max_attempts):
        slot = router.route(uid)
        try:
            await asyncio.to_thread(dm.ensure_slot_container, slot)
        except Exception:
            pass
        base, headers, oauth = pt.upstream_for(slot, client_beta)
        body = pt.inject_identity(dict(anth)) if oauth else dict(anth)
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(f"{base}/v1/messages", headers=headers, json=body)
        latency = int((time.monotonic() - started) * 1000)
        if r.status_code == 401 and oauth:
            router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
            last = r
            continue
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code >= 300:
            msg = (data.get("error") or {}).get("message") or r.text[:300]
            return JSONResponse({"error": {"message": msg, "type": "upstream_error"}}, status_code=r.status_code)
        in_tok, out_tok = _usage_anthropic(data)
        await _bill_pt(key, user, data.get("model") or model, in_tok, out_tok, latency, request, slot_id=slot.id)
        comp = pt.anthropic_to_openai(data)
        if want_stream:
            chunks = pt.openai_stream_chunks(comp)

            async def gen():
                for ch in chunks:
                    yield ch

            return sse_response(gen())
        return JSONResponse(comp)
    return JSONResponse({"error": {"message": "all upstream credentials failed auth"}},
                        status_code=(last.status_code if last is not None else 502))


@router.post("/chat/completions", summary="OpenAI Chat Completions 兼容入口（sk-key；带 tools 自动走原生透传）")
async def chat_completions(payload: ChatCompletionsIn, request: Request, auth: dict = Depends(authenticate_key)):
    key, user = auth["key"], auth["user"]
    model = pt.normalize_model(payload.model) or settings.AK_DEFAULT_MODEL
    usage_svc.precheck(key, user, model)

    # 带 tools → 翻译透传（支持 agent 工具调用）；否则走 CLI（便宜）
    raw = await request.json()
    if raw.get("tools"):
        return await _passthrough_openai(key, user, raw, request)

    # OpenAI 的 system 是 messages 里 role=system 的一条；_build_prompt 已支持
    prompt = _build_prompt(MessagesIn(messages=payload.messages))
    if not prompt:
        raise HTTPException(status_code=400, detail="empty prompt")

    result, _billed = await _run_and_bill(key, user, model, prompt, request)
    resp = _openai_response(result, result.model)
    if payload.stream:
        return _openai_sse_stream(resp)
    return JSONResponse(resp)
