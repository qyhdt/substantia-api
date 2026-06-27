# -*- coding: utf-8 -*-
"""
异常 / 日志字符串消毒：移除可能落库的敏感片段。

用法：
    from services.vibe._sanitize import sanitize_error
    await _emit(run_id, "run.error", {"message": sanitize_error(str(e))})

或挂到日志 handler 上做全局兜底：
    handler.addFilter(SensitiveDataFilter())
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

# 匹配 sk- / Bearer xxx / Anthropic-style auth token
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),                  # sk-xxx / sk-ant-xxx
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),       # Bearer xxx
    re.compile(r"(?i)authorization[:=]\s*\S{8,}"),
    re.compile(r"(?i)x-api-key[:=]\s*\S{8,}"),
]

# 私有 IP（内网 + docker 子网）
_PRIVATE_IP_PATTERNS = [
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b127\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b"),
]

# 一些常见敏感环境变量名
_SENSITIVE_ENV_KEYS = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "JWT_SECRET",
    "POSTGRES_PASSWORD",
)


def _env_values_to_redact() -> list[str]:
    out = []
    for k in _SENSITIVE_ENV_KEYS:
        v = (os.getenv(k) or "").strip()
        if v and len(v) >= 6:
            out.append(v)
    return out


def sanitize_error(msg: Optional[str], max_len: int = 500) -> str:
    """把字符串里的敏感内容替换成 [redacted]，并截断到 max_len。"""
    if not msg:
        return ""
    s = str(msg)

    # 1) 已知 env 实际值（最危险，可能在 docker run 命令 / curl 错误里出现）
    for v in _env_values_to_redact():
        s = s.replace(v, "[redacted]")

    # 2) 通用 token 模式
    for pat in _TOKEN_PATTERNS:
        s = pat.sub("[redacted-token]", s)

    # 3) 内网 IP / docker 网段（防泄露拓扑）
    for pat in _PRIVATE_IP_PATTERNS:
        s = pat.sub("[redacted-ip]", s)

    # 4) 截断
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


# 日志 filter 用的"不截断"上限：业务日志可能很长，sanitize 主要替换敏感片段，
# 让全局 filter 做硬截断会污染 perf/latency 日志统计——这里给个大值近似不截。
_LOG_FILTER_MAX_LEN = 100_000


class SensitiveDataFilter(logging.Filter):
    """全局日志兜底：sanitize record.msg / record.args 里的敏感字符串。

    挂在 handler 上（不是 logger 上），同一条 record 经过多个 handler 时
    第二次 sanitize 也是 idempotent，没副作用。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = sanitize_error(record.msg, max_len=_LOG_FILTER_MAX_LEN)
            args = record.args
            if args:
                if isinstance(args, tuple):
                    record.args = tuple(
                        sanitize_error(a, max_len=_LOG_FILTER_MAX_LEN)
                        if isinstance(a, str) else a
                        for a in args
                    )
                elif isinstance(args, dict):
                    record.args = {
                        k: (
                            sanitize_error(v, max_len=_LOG_FILTER_MAX_LEN)
                            if isinstance(v, str) else v
                        )
                        for k, v in args.items()
                    }
        except Exception:
            # 日志 filter 不能抛
            pass
        return True
