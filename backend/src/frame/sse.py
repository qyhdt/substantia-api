# -*- coding: utf-8 -*-
"""
SSE 工具：编码事件、心跳、统一响应头。

事件格式（JSON 行）：
    event: <type>
    data: {"type": "...", ...}

前端：new EventSource(url, { withCredentials: true })，监听 onmessage 或具名 event。
"""
import json
from typing import Any, AsyncIterator

from starlette.responses import StreamingResponse

# 推到客户端时建议 SSE 行长不要太大；如果一段超过这个就拆
MAX_LINE = 8 * 1024


def encode_event(payload: dict[str, Any], *, event: str | None = None) -> str:
    """把 dict 编成 SSE 帧。多行 data 自动拆。"""
    body = json.dumps(payload, ensure_ascii=False)
    lines = []
    if event:
        lines.append(f"event: {event}")
    for chunk in _split(body, MAX_LINE):
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"


def encode_comment(text: str) -> str:
    """心跳/注释（前端忽略）。"""
    return f": {text}\n\n"


def _split(s: str, n: int):
    if len(s) <= n:
        yield s
        return
    for i in range(0, len(s), n):
        yield s[i : i + n]


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # nginx 反代必须配 proxy_buffering off；同时下面这个让中间件也别 buffer
    "X-Accel-Buffering": "no",
}


def sse_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
