# -*- coding: utf-8 -*-
"""多品牌配置（一套代码，按请求域名切换）。

yayaok.com → yaya；其余（含未知/内部）→ substantia。
只有品牌层不同：API key 前缀、名称、邮件署名/主题、支付回跳。别的代码完全共用。
中间件按 Host 解析后写入 request_context['brand']，业务处用 current_brand() 读取。
"""
from __future__ import annotations

from typing import Dict

BRANDS: Dict[str, dict] = {
    "substantia": {
        "key": "substantia",
        "name": "Substantia",
        "key_prefix": "sk-substantia-",
        "email_subject": "Substantia verification code",
    },
    "yaya": {
        "key": "yaya",
        "name": "Yaya",
        "key_prefix": "sk-yaya-",
        "email_subject": "Yaya verification code",
    },
}

DEFAULT_BRAND = "substantia"


def brand_key_from_host(host: str | None) -> str:
    """host（如 www.yayaok.com）→ 品牌 key。含 yayaok/yaya → yaya；其余 → substantia。"""
    h = (host or "").lower()
    if "yayaok" in h or "yaya" in h:
        return "yaya"
    return DEFAULT_BRAND


def brand(key: str | None) -> dict:
    return BRANDS.get(key or DEFAULT_BRAND, BRANDS[DEFAULT_BRAND])


def current_brand() -> dict:
    """从请求上下文取当前品牌（中间件已按 Host 写入）；无上下文回落默认。"""
    try:
        from utils.request_context import request_context
        return brand(request_context.get().get("brand"))
    except Exception:
        return BRANDS[DEFAULT_BRAND]
