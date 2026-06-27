# -*- coding: utf-8 -*-
"""
自定义 APIRoute：在原 handler 之外，对响应做简单摘要日志。
- JSONResponse：解析后截断打印
- 其它（流/文件）：只打类名
- 健康检查 GET / 不打日志
"""
import json

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, StreamingResponse

from utils.pm_logger import get_app_logger

logger = get_app_logger()


class BaseAPIRoute(APIRoute):
    MAX_BODY_LEN = 2048  # 最多打 2KB

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            response: Response = await original_handler(request)

            body = None
            if isinstance(response, JSONResponse):
                try:
                    raw = response.body.decode("utf-8")
                    if len(raw) > self.MAX_BODY_LEN:
                        body = raw[: self.MAX_BODY_LEN] + "...<truncated>"
                    else:
                        body = json.loads(raw)
                except Exception:
                    body = "<unparseable-json>"
            else:
                body = f"<{response.__class__.__name__}>"

            if not isinstance(response, StreamingResponse) and request.url.path != "/":
                body_str = str(body)
                logger.info("response=%s", body_str[:80] + ("..." if len(body_str) > 80 else ""))

            return response

        return custom_handler
