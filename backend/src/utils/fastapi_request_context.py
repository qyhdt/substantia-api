# -*- coding: utf-8 -*-
"""
请求上下文中间件：

1. 为每个请求生成 / 透传 trace_id、request_id
2. 解析真实客户端 IP（兼容 Nginx 反代）
3. 写入 ContextVar，供后续日志、业务代码读取
4. 出口写 access 日志；慢请求写 latency.log
5. dispatch 末尾 reset ContextVar，防止异步上下文泄漏
"""
import hashlib
import json
import time
import uuid
from typing import Any, Dict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import settings
from utils.pm_logger import get_app_logger, get_latency_logger
from utils.request_context import request_context

logger = get_app_logger()
latency_logger = get_latency_logger()


def _sanitize_header(raw: str, max_len: int = 128) -> str:
    raw = (raw or "").strip()[:max_len]
    if not raw or "\r" in raw or "\n" in raw or "\x00" in raw:
        return "-"
    return raw


def _read_extras(request: Request) -> Dict[str, str]:
    """把 settings.EXTRA_CONTEXT_HEADERS 列出的头按小写键收集，注入到 ctx['extras']。"""
    extras: Dict[str, str] = {}
    for h in settings.extra_context_headers_list:
        key = h.lower().replace("-", "_")
        extras[key] = _sanitize_header(request.headers.get(h, ""))
    return extras


def _client_ip(request: Request) -> str:
    """优先 X-Real-IP（前端 nginx 经 real_ip 模块写的真实客户端，不可伪造），
    其次 X-Forwarded-For 首段，最后 request.client.host。
    与 auth / chat_public / moderation 取 IP 一致，保证日志与防刷看到同一个真实 IP。"""
    xri = request.headers.get("x-real-ip", "").strip()
    if xri:
        return xri
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip() or "-"
    return request.client.host if request.client else "-"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """请求上下文中间件。注意：本中间件假定 CORS 已注册在更外层。"""

    def __init__(self, app):
        super().__init__(app)
        self.access_logger = logger
        self._log_params = settings.LOG_REQUEST_PARAMS

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()

        # ====== 收集请求参数 ======
        params: Dict[str, Any] = dict(request.query_params)
        if request.path_params:
            params.update(request.path_params)

        body_repr = None
        if request.method in ("POST", "PUT", "PATCH"):
            content_type = (request.headers.get("content-type") or "").lower()
            if "multipart/form-data" in content_type:
                body_repr = "<multipart>"
            else:
                try:
                    body_bytes = await request.body()
                    if body_bytes:
                        body_repr = body_bytes.decode("utf-8", errors="replace")[:2000]
                except Exception:
                    logger.error("unable to decode body", exc_info=True)
                    body_repr = "<unreadable>"
        if body_repr:
            params["body"] = body_repr

        if self._log_params and params:
            self.access_logger.info("request-params: %s %s %s", request.method, request.url.path, params)

        # ====== request_id / trace_id ======
        request_id = request.headers.get("X-Request-Id") or "-"
        trace_id = request.headers.get("X-Trace-Id")
        if not trace_id:
            trace_id = hashlib.md5(uuid.uuid4().hex.encode("utf-8")).hexdigest()

        # ====== 用户上下文兜底（鉴权依赖会覆盖）======
        user_id = params.get("user_id", "-")
        session_id = params.get("session_id", "-")

        client_ip = _client_ip(request)
        extras = _read_extras(request)

        token = request_context.set({
            "request_id": request_id,
            "trace_id": trace_id,
            "ip": client_ip,
            "method": request.method,
            "path": request.url.path,
            "status": "-",
            "user_id": user_id,
            "session_id": session_id,
            "params": params,
            "extras": extras,
        })

        try:
            response: Response = await call_next(request)
            cost = time.perf_counter() - start

            # 鉴权 Depends 在子 Context 写 user_id，本层读不到；通过 request.state 补回
            ctx = request_context.get().copy()
            ctx["status"] = response.status_code
            authed_uid = getattr(request.state, "authed_user_id", None)
            if authed_uid:
                ctx["user_id"] = authed_uid
            request_context.set(ctx)

            # 健康检查 GET / 不记 INFO，避免每 30s 探活刷屏
            if request.method != "GET" or request.url.path != "/":
                self.access_logger.info(
                    "request-access",
                    extra={
                        "ip": client_ip,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "cost": f"{cost:.4f}s",
                    },
                )

            if cost >= settings.REQUEST_LATENCY_THRESHOLD:
                latency_logger.warning(
                    "request-access cost=%.4fs threshold=%.1fs %s %s status=%s",
                    cost,
                    settings.REQUEST_LATENCY_THRESHOLD,
                    request.method,
                    request.url.path,
                    response.status_code,
                    extra={"ip": client_ip},
                )

            response.headers["X-Request-Id"] = request_id
            response.headers["X-Trace-Id"] = trace_id
            return response

        finally:
            request_context.reset(token)
