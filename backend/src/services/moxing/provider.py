# -*- coding: utf-8 -*-
"""moxing 公开模型直连。

公开模型请求与 Claude 的透明 fallback 是两套语义：这里保留客户端选择的
GLM/Kimi 模型名并按对应价格计费；原 fallback 链仍由 services.claude.registry 管理。
"""
from __future__ import annotations

import copy
from typing import Any, Dict

import httpx

from config.settings import settings
from services.claude.router import SlotRouter
from services.claude.slots import Slot, SlotType

DIRECT_MODELS = frozenset({"glm-5.2", "kimi-k3"})
FORCED_MODEL = "glm-5.2"


class MoxingError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def normalize_model(model: str | None) -> str:
    return (model or "").strip().lower()


def is_direct_model(model: str | None) -> bool:
    return normalize_model(model) in DIRECT_MODELS


def _base_url() -> str:
    return (
        (settings.MOXING_API_BASE or "").strip()
        or (settings.CLAUDE_FALLBACK_MOXING_BASE_URL or "").strip()
    ).rstrip("/")


def _api_key() -> str:
    return (
        (settings.MOXING_API_KEY or "").strip()
        or (settings.CLAUDE_FALLBACK_MOXING_AUTH_TOKEN or "").strip()
    )


def configured() -> bool:
    return bool(_base_url() and _api_key())


def direct_router(upstream_model: str) -> SlotRouter:
    """构造只含一个 moxing API-key slot 的请求级 router；不改动全局 fallback router。"""
    if not configured():
        return SlotRouter([])
    slot = Slot(
        id="direct-moxing",
        type=SlotType.API_KEY,
        priority=0,
        env={
            "ANTHROPIC_BASE_URL": _base_url(),
            "ANTHROPIC_AUTH_TOKEN": _api_key(),
            "ANTHROPIC_MODEL": normalize_model(upstream_model),
        },
    )
    return SlotRouter([slot])


def _chat_url() -> str:
    base = _base_url()
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


async def chat_completion(raw: Dict[str, Any]) -> Dict[str, Any]:
    """调用 moxing OpenAI Chat Completions；上游统一非流，网关按需转为本地 SSE。"""
    if not configured():
        raise MoxingError("moxing upstream is not configured", status=503)
    body = copy.deepcopy(raw)
    body["model"] = normalize_model(body.get("model"))
    body["stream"] = False
    headers = {
        "authorization": f"Bearer {_api_key()}",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(_chat_url(), headers=headers, json=body)
    except Exception as exc:
        raise MoxingError(f"moxing connection failed: {type(exc).__name__}", status=502) from exc

    try:
        data = response.json()
    except Exception as exc:
        raise MoxingError("moxing returned an invalid response", status=502) from exc
    if response.status_code >= 300:
        error = data.get("error") if isinstance(data, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        raise MoxingError(message or "moxing upstream rejected the request", status=response.status_code)
    if not isinstance(data, dict):
        raise MoxingError("moxing returned an invalid response", status=502)
    return data


def usage(data: Dict[str, Any]) -> tuple[int, int, int]:
    info = data.get("usage") or {}
    prompt = int(info.get("prompt_tokens") or 0)
    completion = int(info.get("completion_tokens") or 0)
    cached = int((info.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return max(0, prompt - cached), completion, cached
