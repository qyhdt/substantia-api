# -*- coding: utf-8 -*-
"""
网关 /v1/messages —— Anthropic Messages 兼容（sk-key 鉴权，不走 JWT）。

链路：鉴权 sk-key → 余额/封顶/模型白名单前置校验 → 把 messages 摊平成 prompt →
services.apikey.runner.run（复用容器团队路由 + 故障转移，sub 用光自动接 api_key slot）→
按命中模型计费扣余额 + 记 usage → 回 Anthropic 风格响应（支持 stream）。
"""
import asyncio
import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from frame.sse import encode_event, sse_response
from security.api_key_auth import authenticate_key
from services.apikey import runner
from services.apikey import usage as usage_svc
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


@router.post("/messages", summary="Anthropic Messages 兼容入口（sk-key）")
async def messages(payload: MessagesIn, request: Request, auth: dict = Depends(authenticate_key)):
    key, user = auth["key"], auth["user"]
    model = payload.model or settings.AK_DEFAULT_MODEL

    # 前置校验：有效余额（实付+有效试用）/ key 封顶 / 模型白名单
    usage_svc.precheck(key, user, model)

    prompt = _build_prompt(payload)
    if not prompt:
        raise HTTPException(status_code=400, detail="empty prompt")

    request_id = (request_context.get({}) or {}).get("trace_id")
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(runner.run, _safe_uid(user), prompt, model)
    except Exception as e:
        # 无可路由 slot / docker 不可达等 → 502，记一条失败日志（不扣费）
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

    # 计费按命中模型（api_key slot 降级后是该 slot 的模型）
    billed = await usage_svc.record_and_charge(
        api_key_id=key["id"], user_id=user["id"], slot_id=result.slot_id, model=result.model,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        latency_ms=latency_ms, attempts=result.attempts,
        status_str=("ok" if result.ok else "error"),
        error_code=(None if result.ok else f"exit_{result.exit_code}"),
        request_id=request_id,
    )

    resp = _anthropic_response(result, result.model)
    resp["_substantia"] = {
        "slot_id": result.slot_id, "slot_type": result.slot_type,
        "cost_micro_usd": billed["cost_micro_usd"], "attempts": result.attempts,
        "estimated_tokens": result.estimated,
    }
    if not result.ok:
        # claude 非鉴权类失败（如用户 prompt 报错）：照常计费但回 200 带错误文本，遵循 CLI 行为
        resp["stop_reason"] = "error"

    if payload.stream:
        return _sse_stream(resp)
    return JSONResponse(resp)
