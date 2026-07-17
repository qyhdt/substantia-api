# -*- coding: utf-8 -*-
"""ChatGPT API-key 上游：passthrough 到 OpenAI 官方 chat/completions。

配置门控：OPENAI_API_KEY 留空 → configured()=False，网关不会用这条兜底。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from config.settings import settings
from services.chatgpt.result import ChatGptResult

log = logging.getLogger("chatgpt.openai")


class OpenAIError(RuntimeError):
    """OpenAI 上游返回非 2xx / 网络错误。"""

    def __init__(self, msg: str, status: int = 502):
        super().__init__(msg)
        self.status = status


def configured() -> bool:
    return bool((settings.OPENAI_API_KEY or "").strip())


def _usage(data: dict) -> tuple[int, int, int]:
    u = (data or {}).get("usage") or {}
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return max(0, prompt - cached), completion, cached


def _text(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


async def chat(messages: List[Dict[str, Any]], model: str,
               max_tokens: Optional[int] = None) -> ChatGptResult:
    """打一次 OpenAI chat/completions（非流式），返回归一化结果。失败抛 OpenAIError。"""
    base = (settings.OPENAI_API_BASE or "https://api.openai.com/v1").rstrip("/")
    body: Dict[str, Any] = {"model": model, "messages": messages}
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {
        "authorization": f"Bearer {settings.OPENAI_API_KEY.strip()}",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(f"{base}/chat/completions", headers=headers, json=body)
    except Exception as e:
        raise OpenAIError(f"连接 OpenAI 失败：{e}", status=502) from e

    if r.status_code >= 300:
        try:
            msg = (r.json().get("error") or {}).get("message") or r.text[:300]
        except Exception:
            msg = r.text[:300]
        raise OpenAIError(f"OpenAI 上游错误：{msg}", status=r.status_code)

    data = r.json()
    prompt_tok, completion_tok, cached = _usage(data)
    return ChatGptResult(
        model=data.get("model") or model,
        text=_text(data),
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
        cache_read_tokens=cached,
        cache_write_tokens=0,
        slot_id="openai",
        provider="openai",
    )
