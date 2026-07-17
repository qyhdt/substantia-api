# -*- coding: utf-8 -*-
"""ChatGPT 上游编排：优先订阅（codex 容器），其次 OpenAI API key 兜底。

对齐 substantia「订阅用光自动接 api_key」的哲学：codex 账号池有账号就先用订阅，
codex 整体不可用（没账号/异常）再落到 OpenAI 官方 key（若配了）。两条都没配 → configured()=False。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from config.settings import settings
from services.chatgpt import codex, openai_api
from services.chatgpt.models import normalize_model
from services.chatgpt.result import ChatGptResult

log = logging.getLogger("chatgpt.provider")


def configured() -> bool:
    """任一条 ChatGPT 上游可用（网关据此决定是否受理 gpt-* 请求）。"""
    return codex.configured() or openai_api.configured()


def status() -> Dict[str, Any]:
    """给前端/admin 的门控状态。"""
    accs = codex.list_accounts()
    return {
        "enabled": configured(),
        "codex_enabled": bool(accs),
        "codex_accounts": accs,
        "openai_key_enabled": openai_api.configured(),
        "default_model": settings.CODEX_DEFAULT_MODEL,
    }


async def run(uid: str, prompt: str, model: Optional[str],
              messages: Optional[List[Dict[str, Any]]] = None,
              max_tokens: Optional[int] = None) -> ChatGptResult:
    """跑一次 ChatGPT。prompt=压平文本（codex 用）；messages=OpenAI 结构化消息（openai key 用，缺则从 prompt 合成）。

    失败策略：codex 失败且配了 OpenAI key → 落 key 兜底；都失败则抛最后一个异常。
    """
    m = normalize_model(model, settings.CODEX_DEFAULT_MODEL)
    oai_messages = messages or [{"role": "user", "content": prompt}]

    last_err: Optional[Exception] = None
    if codex.configured():
        try:
            return await asyncio.to_thread(codex.run_codex, uid, prompt, m)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("codex 上游失败，尝试兜底：%s", e)

    if openai_api.configured():
        return await openai_api.chat(oai_messages, m, max_tokens)

    if last_err:
        raise last_err
    raise codex.CodexError("ChatGPT 上游未配置（无 codex 账号且无 OPENAI_API_KEY）")
