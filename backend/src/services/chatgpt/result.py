# -*- coding: utf-8 -*-
"""归一化结果：与 services.apikey.runner.RunnerResult 同构，让网关的响应格式化函数
（_anthropic_response / _openai_response）无需分支即可复用。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatGptResult:
    model: str                    # 计价/回显用的实际模型名
    text: str                     # 助手回复全文
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    slot_id: str = "chatgpt"      # 命中的账号/上游标识（记 usage 用）
    provider: str = "codex"       # codex | openai
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return True
