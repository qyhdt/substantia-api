# -*- coding: utf-8 -*-
"""
统一异常响应：所有错误返回结构一致的 JSON，并带上 trace_id 便于排查。

响应格式：
    {
        "code": int,        # HTTP 状态码
        "message": str,     # 简要错误信息
        "detail": Any,      # 校验错误等结构化信息（可选）
        "trace_id": str,    # 与日志串联
    }
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.pm_logger import get_app_logger
from utils.request_context import request_context

logger = get_app_logger()


def _trace_id() -> str:
    return (request_context.get({}) or {}).get("trace_id", "-")


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else "error",
            "detail": exc.detail if not isinstance(exc.detail, str) else None,
            "trace_id": _trace_id(),
        },
        headers=exc.headers or None,
    )


async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "request validation failed",
            "detail": exc.errors(),
            "trace_id": _trace_id(),
        },
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception: %s %s", request.method, request.url.path, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "internal server error",
            "detail": None,
            "trace_id": _trace_id(),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    # Starlette HTTPException 覆盖 FastAPI HTTPException、路由未匹配 404 等
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
