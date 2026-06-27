# -*- coding: utf-8 -*-
"""
示例 service：演示如何从业务层读取请求上下文、写日志，并被 perf_monitor 装饰。
"""
from utils.perf_monitor import perf_monitor
from utils.pm_logger import get_app_logger
from utils.request_context import request_context

log = get_app_logger()


@perf_monitor
async def echo_user(user: dict) -> dict:
    ctx = request_context.get({})
    log.info("echo_user called")
    return {
        "user": user,
        "trace_id": ctx.get("trace_id"),
        "request_id": ctx.get("request_id"),
        "ip": ctx.get("ip"),
    }


@perf_monitor
async def mock_create_item(name: str, quantity: int, user_id: str | None) -> dict:
    log.info("mock_create_item name=%s qty=%d user=%s", name, quantity, user_id)
    return {
        "ok": True,
        "item": {"name": name, "quantity": quantity},
        "owner": user_id,
    }
