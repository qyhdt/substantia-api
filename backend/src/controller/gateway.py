# -*- coding: utf-8 -*-
"""
网关 /v1/messages —— Anthropic Messages 兼容（sk-key 鉴权，不走 JWT）。

链路：鉴权 sk-key → 余额/封顶/模型白名单前置校验 → 把 messages 摊平成 prompt →
services.apikey.runner.run（复用容器团队路由 + 故障转移，sub 用光自动接 api_key slot）→
始终按客户端请求的 Claude 模型计费扣余额 + 记 usage → 回 Anthropic 风格响应（支持 stream）。
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
from services.apikey import failover as pt_failover
from services.apikey import passthrough as pt
from services.apikey import public_identity
from services.apikey import upstream_audit as audit
from services.apikey import pricing  # noqa: F401  (用户用到，保留)
from services.apikey import runner
from services.apikey import usage as usage_svc
from services.chatgpt import models as cg_models
from services.chatgpt import provider as chatgpt
from services.claude import docker_manager as dm
from services.claude.registry import get_router
from services.claude.slots import SlotType
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
        # Gemini/GLM are internal continuity tiers.  Product pricing remains
        # tied to the Claude model the customer requested.
        api_key_id=key["id"], user_id=user["id"], slot_id=result.slot_id, model=model,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        cache_read_tokens=getattr(result, "cache_read_tokens", 0),
        cache_write_tokens=getattr(result, "cache_write_tokens", 0),
        latency_ms=latency_ms, attempts=result.attempts,
        status_str=("ok" if result.ok else "error"),
        error_code=(None if result.ok else f"exit_{result.exit_code}"),
        request_id=request_id,
    )
    return result, billed


# ============================ ChatGPT 上游（gpt-* / o* / codex 分流到这里）============================
async def _run_chatgpt_and_bill(key: dict, user: dict, model: str, prompt: str, request: Request,
                                *, messages=None, max_tokens=None):
    """跑 ChatGPT（codex 订阅 / OpenAI key）+ 计费 + 记 usage。返回 ChatGptResult。失败抛 HTTPException。"""
    request_id = (request_context.get({}) or {}).get("trace_id")
    started = time.monotonic()
    try:
        result = await chatgpt.run(_safe_uid(user), prompt, model,
                                   messages=messages, max_tokens=max_tokens)
    except Exception as e:
        code = getattr(e, "status", 502)
        code = code if isinstance(code, int) and code >= 400 else 502
        await usage_svc.record_and_charge(
            api_key_id=key["id"], user_id=user["id"], slot_id=None, model=model,
            prompt_tokens=0, completion_tokens=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            status_str="error", error_code=type(e).__name__, request_id=request_id,
        )
        log.warning("chatgpt upstream error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=code, detail=f"chatgpt upstream error: {e}")

    latency_ms = int((time.monotonic() - started) * 1000)
    await usage_svc.record_and_charge(
        api_key_id=key["id"], user_id=user["id"], slot_id=result.slot_id, model=result.model,
        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
        cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens,
        latency_ms=latency_ms, attempts=result.attempts, status_str="ok", request_id=request_id,
    )
    return result


# ============================ 原生透传（带 tools 走这条，支持 agent 工具调用）============================
async def _bill_pt(key, user, model, in_tok, out_tok, latency_ms, request, *, slot_id=None,
                   cache_read=0, cache_write=0, status="ok", error_code=None, attempts=1):
    request_id = (request_context.get({}) or {}).get("trace_id")
    return await usage_svc.record_and_charge(
        api_key_id=key["id"], user_id=user["id"], slot_id=slot_id, model=model,
        prompt_tokens=in_tok, completion_tokens=out_tok, latency_ms=latency_ms,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        attempts=max(1, int(attempts)), status_str=status, error_code=error_code, request_id=request_id,
    )


def _usage_anthropic(data: dict):
    """从 Anthropic 响应抽 (input, output, cache_read, cache_write)。缓存 token 单独返回，
    不再并进 input（计价时按官方折扣，见 services/apikey/pricing.py）。"""
    u = (data or {}).get("usage") or {}
    return (
        int(u.get("input_tokens", 0) or 0),
        int(u.get("output_tokens", 0) or 0),
        int(u.get("cache_read_input_tokens", 0) or 0),
        int(u.get("cache_creation_input_tokens", 0) or 0),
    )


def _mark_passthrough_failed(slot_router, slot, attempt: int, reason: str) -> None:
    """标记失败且只记非敏感摘要；reason 只能是 status/异常类名。"""
    try:
        slot_router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
    except Exception:
        pass
    log.warning("gateway passthrough slot %s failed (%s), trying next provider (attempt %d)",
                slot.id, reason, attempt)


async def _prepare_passthrough_attempt(slot, raw: dict, client_headers: dict, *, endpoint: str,
                                       uid: str, key_id: Any):
    """为一个 slot 构造独立请求；任何异常均由调用方按配置失败切下一档。"""
    if slot.type == SlotType.SUBSCRIPTION:
        # OAuth 文件可能要由容器初始化/续期；API-key 原生透传不需要 Docker。
        await asyncio.to_thread(dm.ensure_slot_container, slot)
    base, headers, oauth = pt.upstream_for(slot, client_headers)
    body = pt.body_for_slot(slot, raw, oauth=oauth)
    audit.record_upstream(
        endpoint=endpoint, body=body, uid=uid, key_id=key_id, slot_id=slot.id,
        oauth=oauth, base=pt_failover.safe_url_for_log(base),
    )
    return base, headers, body


async def _post_passthrough_with_failover(*, slot_router, uid: str, raw: dict,
                                          client_headers: dict, endpoint: str, key_id: Any):
    """非流式原生 POST；顺序尝试 router 给出的所有 slot，且每个 slot 至多一次。"""
    try:
        candidates = pt_failover.candidate_slots(slot_router, uid)
    except Exception as exc:  # no routable slot / router 配置异常
        log.warning("gateway passthrough has no candidates (%s)", type(exc).__name__)
        candidates = []

    if not candidates:
        return {
            "slot": None, "status_code": 503, "attempts": 0, "latency_ms": 0,
            "data": {"error": {"type": "upstream_unavailable", "message": "no upstream provider available"}},
        }

    total_started = time.monotonic()
    last = None
    for attempt, slot in enumerate(candidates, 1):
        try:
            base, headers, body = await _prepare_passthrough_attempt(
                slot, raw, client_headers, endpoint=endpoint, uid=uid, key_id=key_id,
            )
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(f"{base}/v1/messages", headers=headers, json=body)
        except Exception as exc:  # 网络、URL、凭据文件或 slot 配置异常
            _mark_passthrough_failed(slot_router, slot, attempt, type(exc).__name__)
            last = None
            continue

        raw_text = response.text
        parsed = True
        try:
            data = response.json()
        except Exception:
            parsed = False
            data = {"error": {"type": "upstream", "message": raw_text[:500]}}

        # 2xx 必须是 Messages JSON object；代理返回 HTML/空体属于本档异常，应切档。
        if response.status_code < 300 and (not parsed or not isinstance(data, dict)):
            _mark_passthrough_failed(slot_router, slot, attempt, "invalid_success_response")
            last = {
                "slot": slot, "status_code": 502, "attempts": attempt,
                "latency_ms": int((time.monotonic() - total_started) * 1000),
                "data": {"error": {"type": "upstream", "message": "invalid upstream response"}},
            }
            continue

        result = {
            "slot": slot, "status_code": response.status_code, "attempts": attempt,
            "latency_ms": int((time.monotonic() - total_started) * 1000), "data": data,
        }
        if response.status_code >= 300 and pt_failover.is_retryable_response(
            response.status_code, data if parsed else raw_text, slot=slot,
        ):
            _mark_passthrough_failed(slot_router, slot, attempt, f"http_{response.status_code}")
            last = result
            continue
        if response.status_code >= 300 and slot.type == SlotType.API_KEY:
            result["data"] = {
                "error": {
                    "type": "invalid_request_error" if response.status_code < 500 else "upstream_error",
                    "message": "selected model endpoint rejected the request",
                }
            }
        return result

    # 最终失败来自兼容 fallback 时，不把 provider/model/error 扩展回显给客户端。
    if (
        last is not None
        and last.get("slot") is not None
        and last["slot"].type == SlotType.API_KEY
    ):
        return {
            "slot": None, "status_code": 502, "attempts": last["attempts"],
            "latency_ms": last["latency_ms"],
            "data": {"error": {"type": "upstream_unavailable",
                               "message": "selected model endpoint is temporarily unavailable"}},
        }
    return last or {
        "slot": None, "status_code": 502, "attempts": len(candidates),
        "latency_ms": int((time.monotonic() - total_started) * 1000),
        "data": {"error": {"type": "upstream_unavailable", "message": "all upstream providers unavailable"}},
    }


def _sse_error(data: Any) -> str:
    """把上游错误包装成单个 SSE error frame。"""
    if isinstance(data, bytes):
        text = data.decode("utf-8", "replace")
    elif isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, ensure_ascii=False, default=str)
    return "event: error\ndata: " + text.replace("\n", " ") + "\n\n"


def _pop_sse_frame(buffer: bytes):
    """从任意 HTTP chunk 拼接结果中取一个完整 SSE frame；返回 (frame, sep, rest)。"""
    found = [(i, sep) for sep in (b"\n\n", b"\r\n\r\n") if (i := buffer.find(sep)) >= 0]
    if not found:
        return None
    i, sep = min(found, key=lambda item: item[0])
    return buffer[:i], sep, buffer[i + len(sep):]


def _anthropic_data_sse(data: Dict[str, Any]):
    """把一次完整 Anthropic Messages 响应包装为合法 SSE。

    身份问询的 fallback 先以非流方式取得真实 usage/slot，再走此本地流，确保任何
    provider 自述都不会在首个 delta 泄露。这里只用于明确身份问询。
    """
    usage = data.get("usage") or {}
    content = data.get("content") or []

    async def gen():
        start_message = {
            key: value for key, value in data.items()
            if key not in {"content", "stop_reason", "stop_sequence"}
        }
        start_message["content"] = []
        start_message["usage"] = {
            **usage,
            "output_tokens": 0,
        }
        yield encode_event(
            {"type": "message_start", "message": start_message},
            event="message_start",
        )
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                yield encode_event(
                    {"type": "content_block_start", "index": index,
                     "content_block": {"type": "text", "text": ""}},
                    event="content_block_start",
                )
                yield encode_event(
                    {"type": "content_block_delta", "index": index,
                     "delta": {"type": "text_delta", "text": block.get("text", "")}},
                    event="content_block_delta",
                )
            elif block_type == "tool_use":
                yield encode_event(
                    {"type": "content_block_start", "index": index,
                     "content_block": {"type": "tool_use", "id": block.get("id"),
                                       "name": block.get("name"), "input": {}}},
                    event="content_block_start",
                )
                yield encode_event(
                    {"type": "content_block_delta", "index": index,
                     "delta": {"type": "input_json_delta",
                               "partial_json": json.dumps(block.get("input") or {}, ensure_ascii=False)}},
                    event="content_block_delta",
                )
            else:
                continue
            yield encode_event(
                {"type": "content_block_stop", "index": index},
                event="content_block_stop",
            )
        yield encode_event(
            {"type": "message_delta",
             "delta": {"stop_reason": data.get("stop_reason") or "end_turn",
                       "stop_sequence": data.get("stop_sequence")},
             "usage": {"output_tokens": int(usage.get("output_tokens", 0) or 0)}},
            event="message_delta",
        )
        yield encode_event({"type": "message_stop"}, event="message_stop")

    return sse_response(gen())


async def _passthrough_identity_stream(key: dict, user: dict, raw: dict, request: Request,
                                       *, slot_router, uid: str, client_headers: dict):
    """明确模型身份问询：非流取上游结果，再用本地 SSE 输出，杜绝首包身份泄露。"""
    upstream_body = {**raw, "stream": False}
    result = await _post_passthrough_with_failover(
        slot_router=slot_router, uid=uid, raw=upstream_body, client_headers=client_headers,
        endpoint="anthropic", key_id=key.get("id"),
    )
    data = result["data"]
    slot = result["slot"]
    if result["status_code"] >= 300 or slot is None:
        async def error_gen():
            yield _sse_error({
                "error": {"type": "upstream_unavailable", "message": "upstream provider unavailable"}
            })
        return sse_response(error_gen())

    model = raw["model"]
    data = dict(data)
    data["model"] = model
    if slot.type == SlotType.API_KEY:
        data = public_identity.localize_api_key_success(data, model, raw)
    else:
        data = public_identity.enforce_selected_model_answer(data, model, raw)
    in_tok, out_tok, cr_tok, cw_tok = _usage_anthropic(data)
    await _bill_pt(
        key, user, model, in_tok, out_tok, result["latency_ms"], request,
        slot_id=slot.id, cache_read=cr_tok, cache_write=cw_tok,
        attempts=result["attempts"],
    )
    return _anthropic_data_sse(data)


async def _passthrough_anthropic(key: dict, user: dict, raw: dict, request: Request):
    """带 tools 的请求：拿 slot 凭据直打 api.anthropic.com/v1/messages，原样转发/回传。"""
    slot_router = get_router()
    uid = _safe_uid(user)
    raw = {**raw, "model": pt.normalize_model(raw.get("model")) or settings.AK_DEFAULT_MODEL}
    model = raw["model"]
    stream = bool(raw.get("stream"))
    client_headers = dict(request.headers)  # 来源是真 CLI 时原样转发其指纹头

    if not stream:
        result = await _post_passthrough_with_failover(
            slot_router=slot_router, uid=uid, raw=raw, client_headers=client_headers,
            endpoint="anthropic", key_id=key.get("id"),
        )
        data = result["data"]
        slot = result["slot"]
        if result["status_code"] < 300 and slot is not None:
            # 内部 Gemini/GLM 对客户端透明：响应与计费始终使用请求的 Claude model。
            data = dict(data)
            data["model"] = model
            if slot.type == SlotType.API_KEY:
                data = public_identity.localize_api_key_success(data, model, raw)
            else:
                data = public_identity.enforce_selected_model_answer(data, model, raw)
            in_tok, out_tok, cr_tok, cw_tok = _usage_anthropic(data)
            await _bill_pt(
                key, user, model, in_tok, out_tok, result["latency_ms"], request,
                slot_id=slot.id, cache_read=cr_tok, cache_write=cw_tok,
                attempts=result["attempts"],
            )
        return JSONResponse(data, status_code=result["status_code"])

    # 身份问询不能直接转发 provider 的流式首包：一旦发出就无法追回。仅对安全的
    # Claude model 启用本地确定性流；普通流式请求保持原来的零缓冲透传路径。
    if (public_identity.safe_public_model(model) is not None
            and public_identity.is_identity_inquiry(raw)):
        return await _passthrough_identity_stream(
            key, user, raw, request, slot_router=slot_router, uid=uid,
            client_headers=client_headers,
        )

    # 流式：只有在收到某档 2xx 前允许切档；接受 2xx 后绝不重放，避免重复输出/双扣。
    try:
        candidates = pt_failover.candidate_slots(slot_router, uid)
    except Exception as exc:
        log.warning("gateway passthrough stream has no candidates (%s)", type(exc).__name__)
        candidates = []
    total_started = time.monotonic()

    async def gen():
        for attempt, slot in enumerate(candidates, 1):
            accepted = False
            public_message_id = (
                public_identity.new_message_id() if slot.type == SlotType.API_KEY else None
            )
            try:
                base, headers, body = await _prepare_passthrough_attempt(
                    slot, raw, client_headers, endpoint="anthropic", uid=uid, key_id=key.get("id"),
                )
                async with httpx.AsyncClient(timeout=600.0) as client:
                    async with client.stream(
                        "POST", f"{base}/v1/messages", headers=headers, json=body,
                    ) as response:
                        if response.status_code >= 300:
                            error_bytes = await response.aread()
                            if pt_failover.is_retryable_response(
                                response.status_code, error_bytes, slot=slot,
                            ):
                                _mark_passthrough_failed(
                                    slot_router, slot, attempt, f"http_{response.status_code}",
                                )
                                continue
                            if slot.type == SlotType.API_KEY:
                                yield _sse_error({
                                    "error": {"type": "upstream_error",
                                              "message": "selected model endpoint rejected the request"}
                                })
                            else:
                                yield _sse_error(error_bytes)
                            return

                        accepted = True
                        raw_chunks: List[bytes] = []
                        pending = b""
                        try:
                            async for chunk in response.aiter_bytes():
                                raw_chunks.append(chunk)
                                pending += chunk
                                while (popped := _pop_sse_frame(pending)) is not None:
                                    frame, separator, pending = popped
                                    yield public_identity.rewrite_anthropic_sse_metadata(
                                        frame, model, public_message_id,
                                    ) + separator
                            if pending:
                                yield public_identity.rewrite_anthropic_sse_metadata(
                                    pending, model, public_message_id,
                                )
                        except Exception as exc:
                            # 已接受 2xx 后不能切档；仅返回安全错误，不输出异常文本。
                            log.warning("gateway passthrough stream interrupted after success (%s)",
                                        type(exc).__name__)
                            yield _sse_error({"error": {"type": "upstream_stream_error",
                                                        "message": "upstream stream interrupted"}})
                        finally:
                            text = b"".join(raw_chunks).decode("utf-8", "replace")
                            mi = re.search(r'"input_tokens"\s*:\s*(\d+)', text)
                            mo = re.findall(r'"output_tokens"\s*:\s*(\d+)', text)
                            mcr = re.search(r'"cache_read_input_tokens"\s*:\s*(\d+)', text)
                            mcw = re.search(r'"cache_creation_input_tokens"\s*:\s*(\d+)', text)
                            in_tok = int(mi.group(1)) if mi else 0
                            out_tok = int(mo[-1]) if mo else 0
                            cr_tok = int(mcr.group(1)) if mcr else 0
                            cw_tok = int(mcw.group(1)) if mcw else 0
                            await _bill_pt(
                                key, user, model, in_tok, out_tok,
                                int((time.monotonic() - total_started) * 1000), request,
                                slot_id=slot.id, cache_read=cr_tok, cache_write=cw_tok,
                                attempts=attempt,
                            )
                        return
            except Exception as exc:
                if accepted:
                    # 计费等 2xx 后内部异常不能驱动 provider 重放。
                    raise
                _mark_passthrough_failed(slot_router, slot, attempt, type(exc).__name__)
                continue
        # 所有可重试档均失败时统一返回公开错误，避免最后一个 fallback 的原始
        # provider/model 名称通过 SSE 泄露。
        yield _sse_error({
            "error": {"type": "upstream_unavailable",
                      "message": "selected model endpoint is temporarily unavailable"}
        })

    return sse_response(gen())


@router.post("/messages", summary="Anthropic Messages 兼容入口（sk-key；全部原生透传，支持 Claude Code CLI）")
async def messages(payload: MessagesIn, request: Request, auth: dict = Depends(authenticate_key)):
    key, user = auth["key"], auth["user"]

    # ChatGPT 系模型（gpt-* / o* / codex）分流到 ChatGPT 上游，压平成 prompt 跑 codex/openai，
    # 结果回成 Anthropic 响应格式。Claude 请求走下面原有的原生透传，零影响。
    if cg_models.is_chatgpt_model(payload.model):
        model = cg_models.normalize_model(payload.model, settings.CODEX_DEFAULT_MODEL)
        usage_svc.precheck(key, user, model)
        prompt = _build_prompt(payload)
        result = await _run_chatgpt_and_bill(key, user, model, prompt, request,
                                             max_tokens=payload.max_tokens)
        resp = _anthropic_response(result, result.model)
        return _sse_stream(resp) if payload.stream else JSONResponse(resp)

    model = pt.normalize_model(payload.model) or settings.AK_DEFAULT_MODEL
    usage_svc.precheck(key, user, model)  # 有效余额 / key 封顶 / 模型白名单

    # 全部走原生 API 透传（不再压平成文本走 CLI）：保留完整消息结构、tools、多模态；
    # 来源若是真 Claude Code CLI，upstream_for 会原样转发其指纹头。
    raw = await request.json()
    return await _passthrough_anthropic(key, user, raw, request)


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
    slot_router = get_router()
    uid = _safe_uid(user)
    raw = {**raw, "model": pt.normalize_model(raw.get("model")) or settings.AK_DEFAULT_MODEL}
    model = raw["model"]
    want_stream = bool(raw.get("stream"))
    anth = pt.openai_to_anthropic(raw)
    anth["stream"] = False
    client_headers = dict(request.headers)
    result = await _post_passthrough_with_failover(
        slot_router=slot_router, uid=uid, raw=anth, client_headers=client_headers,
        endpoint="openai", key_id=key.get("id"),
    )
    data = result["data"]
    slot = result["slot"]
    if result["status_code"] >= 300 or slot is None:
        err = data.get("error") if isinstance(data, dict) else None
        msg = (err or {}).get("message") if isinstance(err, dict) else None
        if not msg:
            msg = "upstream provider unavailable"
        return JSONResponse(
            {"error": {"message": msg, "type": "upstream_error"}},
            status_code=result["status_code"],
        )

    # 翻译结果、计费均保持客户端请求的 Claude model；实际 provider 只记 slot_id。
    data = dict(data)
    data["model"] = model
    if slot.type == SlotType.API_KEY:
        data = public_identity.localize_api_key_success(data, model, anth)
    else:
        data = public_identity.enforce_selected_model_answer(data, model, anth)
    in_tok, out_tok, cr_tok, cw_tok = _usage_anthropic(data)
    await _bill_pt(
        key, user, model, in_tok, out_tok, result["latency_ms"], request,
        slot_id=slot.id, cache_read=cr_tok, cache_write=cw_tok,
        attempts=result["attempts"],
    )
    comp = pt.anthropic_to_openai(data)
    if want_stream:
        chunks = pt.openai_stream_chunks(comp)

        async def gen():
            for chunk in chunks:
                yield chunk

        return sse_response(gen())
    return JSONResponse(comp)


@router.post("/chat/completions", summary="OpenAI Chat Completions 兼容入口（sk-key；全部翻译成原生透传）")
async def chat_completions(payload: ChatCompletionsIn, request: Request, auth: dict = Depends(authenticate_key)):
    key, user = auth["key"], auth["user"]

    # ChatGPT 系模型分流到 ChatGPT 上游；结构化 messages 直接给 OpenAI key 上游（保真），
    # codex 上游用压平 prompt。结果回成 OpenAI 响应格式。Claude 请求走原有翻译透传。
    if cg_models.is_chatgpt_model(payload.model):
        model = cg_models.normalize_model(payload.model, settings.CODEX_DEFAULT_MODEL)
        usage_svc.precheck(key, user, model)
        prompt = _build_prompt(MessagesIn(model=model, messages=payload.messages))
        result = await _run_chatgpt_and_bill(key, user, model, prompt, request,
                                             messages=payload.messages, max_tokens=payload.max_tokens)
        resp = _openai_response(result, result.model)
        return _openai_sse_stream(resp) if payload.stream else JSONResponse(resp)

    model = pt.normalize_model(payload.model) or settings.AK_DEFAULT_MODEL
    usage_svc.precheck(key, user, model)

    # 全部翻译成 Anthropic 原生请求再透传（不再压平走 CLI）：保留 tools、多模态、完整结构。
    raw = await request.json()
    return await _passthrough_openai(key, user, raw, request)
