#!/usr/bin/env python3
"""Local OpenAI-compatible proxy → Substantia Anthropic /v1/messages for Cursor BYOK."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

SUBSTANTIA_BASE = os.environ.get("SUBSTANTIA_BASE", "https://api.substantia.ai").rstrip("/")
SUBSTANTIA_KEY = os.environ.get("SUBSTANTIA_API_KEY", "")
DEFAULT_MODEL = os.environ.get("SUBSTANTIA_MODEL", "claude-opus-4-8")
PORT = int(os.environ.get("CURSOR_PROXY_PORT", "8765"))

app = FastAPI(title="Substantia Cursor Proxy", docs_url=None, redoc_url=None)


def _pick_key(header_key: str | None) -> str:
    key = (header_key or "").strip() or SUBSTANTIA_KEY.strip()
    if not key:
        raise HTTPException(status_code=401, detail="missing api key")
    return key


def _to_anthropic(body: dict[str, Any]) -> dict[str, Any]:
    system_parts: list[str] = []
    messages: list[dict[str, Any]] = []
    for msg in body.get("messages") or []:
        role = msg.get("role") or "user"
        content = msg.get("content")
        if isinstance(content, list):
            text = "\n".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text = str(content or "")
        if role == "system":
            system_parts.append(text)
            continue
        if role == "tool":
            text = f"[tool result]\n{text}"
            role = "user"
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": text})
    if not messages:
        raise HTTPException(status_code=400, detail="empty messages")
    payload: dict[str, Any] = {
        "model": body.get("model") or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": body.get("max_tokens") or 8192,
        "stream": bool(body.get("stream")),
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


def _openai_response(anthropic: dict[str, Any], model: str) -> dict[str, Any]:
    text = ""
    for block in anthropic.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text") or ""
    usage = anthropic.get("usage") or {}
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(__import__("time").time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def _openai_stream_chunk(model: str, delta: str | None = None, finish: bool = False) -> str:
    if finish:
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": int(__import__("time").time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    else:
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": int(__import__("time").time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta or ""}, "finish_reason": None}],
        }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/v1/models")
@app.get("/models")
async def list_models(authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)):
    _pick_key(x_api_key or (authorization or "").removeprefix("Bearer ").strip())
    model = DEFAULT_MODEL
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": 0, "owned_by": "substantia"},
            {"id": "claude-sonnet-4-6", "object": "model", "created": 0, "owned_by": "substantia"},
        ],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)):
    key = _pick_key(x_api_key or (authorization or "").removeprefix("Bearer ").strip())
    body = await request.json()
    payload = _to_anthropic(body)
    model = payload["model"]
    headers = {"x-api-key": key, "content-type": "application/json", "anthropic-version": "2023-06-01"}

    if payload.pop("stream"):
        async def stream():
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream("POST", f"{SUBSTANTIA_BASE}/v1/messages", headers=headers, json={**payload, "stream": True}) as resp:
                    if resp.status_code >= 400:
                        detail = await resp.aread()
                        raise HTTPException(status_code=resp.status_code, detail=detail.decode("utf-8", "ignore"))
                    text = ""
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if evt.get("type") == "content_block_delta":
                            delta = (evt.get("delta") or {}).get("text") or ""
                            if delta:
                                text += delta
                                yield _openai_stream_chunk(model, delta)
                        elif evt.get("type") == "message_stop":
                            break
                    yield _openai_stream_chunk(model, finish=True)
                    yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{SUBSTANTIA_BASE}/v1/messages", headers=headers, json=payload)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return JSONResponse(_openai_response(resp.json(), model))


@app.get("/health")
async def health():
    return {"ok": True, "upstream": SUBSTANTIA_BASE}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
