# -*- coding: utf-8 -*-
"""ChatGPT 模型识别：网关据此把请求分流到 ChatGPT 上游（而非 Claude）。"""
from __future__ import annotations

import re

# gpt-*（gpt-4o/gpt-5…）、o1/o3/o4 系推理模型、chatgpt-*、codex-* 都算 ChatGPT 系。
_CHATGPT_RE = re.compile(r"^(gpt[-.]?|o[1345]([-.]|$)|chatgpt|codex)", re.IGNORECASE)


def is_chatgpt_model(model: str | None) -> bool:
    """请求模型是否属于 ChatGPT 系（决定走 ChatGPT 上游还是 Claude）。"""
    if not model:
        return False
    return bool(_CHATGPT_RE.match(model.strip()))


def normalize_model(model: str | None, default: str) -> str:
    """去空白；空则回落默认。ChatGPT 型号名直接透传给 codex/openai，不做别名改写。"""
    m = (model or "").strip()
    return m or default
