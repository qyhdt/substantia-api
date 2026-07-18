# -*- coding: utf-8 -*-
"""原生 API 透传的请求级 provider 故障转移策略。

这个模块只做三件事：

* 从 router 取得一次请求可尝试的全部 slot（顺序由 router 的 priority 策略决定）；
* 判断一个上游响应是否属于「换 provider 仍可能成功」的错误；
* 为日志清理 URL 中可能携带的认证信息。

凭据和请求 headers 不进入本模块，也绝不进入日志。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from services.claude.slots import Slot, SlotType


_RETRYABLE_STATUS = frozenset({401, 403, 408, 429})

# 有些兼容网关把额度耗尽错误包成 400，不能只看 HTTP status。
_QUOTA_MARKERS = (
    "insufficient_quota",
    "quota_error",
    "resource_exhausted",
    "quota exceeded",
    "quota has been exceeded",
    "quota exhausted",
    "quota limit",
    "out of quota",
    "exceeded your current quota",
    "usage limit",
    "weekly limit",
    "weekly usage",
    "rate_limit_error",
    "rate limit exceeded",
    "credit balance is too low",
    "billing hard limit",
)

# API-key slot 的 model 由服务端 slot.env 覆盖，不是客户端提供的。因此以下 400
# 属于 provider/slot 配置错误，可以安全换下一档；subscription 上同样的 400 仍是
# 客户端请求级错误，不能把整个账号池逐个打坏。
_API_KEY_CONFIG_MARKERS = (
    "invalid model",
    "invalid_model",
    "unknown model",
    "model not found",
    "model_not_found",
    "unsupported model",
    "unsupported_model",
    "does not exist",
    "no endpoints found",
    "convert_request_failed",
    "convert request failed",
)


def _body_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace").lower()
    if isinstance(body, str):
        return body.lower()
    try:
        return json.dumps(body, ensure_ascii=False, default=str).lower()
    except Exception:
        return ""


def is_retryable_response(status_code: int, body: Any = None, *, slot: Optional[Slot] = None) -> bool:
    """返回该响应是否应标记当前 slot 不健康并尝试下一 slot。

    普通客户端 4xx（例如参数校验失败、上下文过长）明确返回 False。API-key
    slot 的模型由服务端配置，所以只有错误体明确指出 model/协议转换配置异常时，
    才把 400 视为可降级错误。
    """
    if status_code in _RETRYABLE_STATUS or 500 <= status_code <= 599:
        return True
    text = _body_text(body)
    if any(marker in text for marker in _QUOTA_MARKERS):
        return True
    # API-key provider 的 URL 也由服务端 slot 配置；其 /v1/messages 404 是端点
    # 配错/通道未启用，不是客户端资源路径，应该继续到下一 provider。
    if status_code == 404 and slot is not None and slot.type == SlotType.API_KEY:
        return True
    return bool(
        status_code == 400
        and slot is not None
        and slot.type == SlotType.API_KEY
        and any(marker in text for marker in _API_KEY_CONFIG_MARKERS)
    )


def candidate_slots(slot_router: Any, user_id: str) -> List[Slot]:
    """取得本请求的完整、去重候选列表。

    新 router 的 ``route_candidates`` 已按 ``Slot.priority``（小者优先）排序，
    同 priority 内再应用 RR/HRW。兼容路径只用于滚动升级或隔离单测；它仍会把
    当前 route 命中的 slot 放最前，并把其余可路由 slot 全部纳入，绝不受旧的
    ``CLAUDE_EXEC_MAX_ATTEMPTS`` 限制。
    """
    route_candidates = getattr(slot_router, "route_candidates", None)
    if callable(route_candidates):
        slots = list(route_candidates(user_id))
    else:
        first = slot_router.route(user_id)
        rest: Iterable[Slot] = getattr(slot_router, "routable_slots")()
        slots = [first, *sorted(
            (s for s in rest if s.id != first.id),
            key=lambda s: (getattr(s, "priority", 0), s.id),
        )]

    out: List[Slot] = []
    seen: Set[str] = set()
    for slot in slots:
        if slot.id in seen:
            continue
        seen.add(slot.id)
        out.append(slot)
    return out


def safe_url_for_log(url: str) -> str:
    """去掉 URL userinfo/query/fragment，防止配置在 URL 里的 key 被审计日志带出。"""
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        if p.port is not None:
            host = f"{host}:{p.port}"
        return urlunsplit((p.scheme, host, p.path, "", ""))
    except Exception:
        return ""
