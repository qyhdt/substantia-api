# -*- coding: utf-8 -*-
"""
请求级 ContextVar。中间件在 dispatch 入口写入，业务代码与日志 Filter 读取。
"""
from contextvars import ContextVar
from typing import Dict

request_context: ContextVar[Dict] = ContextVar(
    "request_context",
    default={},
)


def update_context(**kwargs) -> None:
    """Safely update context (get → update → set)."""
    ctx = request_context.get().copy()
    ctx.update(kwargs)
    request_context.set(ctx)
