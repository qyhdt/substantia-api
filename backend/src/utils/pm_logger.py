# -*- coding: utf-8 -*-
"""
四类日志：app / error / perf / latency。
- app.log:     所有 INFO+
- error.log:   ERROR+
- perf.log:    perf_monitor 装饰器埋点
- latency.log: 慢函数/慢请求（WARNING+）

每条日志由 JsonFormatter 输出为单行 JSON；
RequestContextFilter 把 ContextVar 里的请求级字段（trace_id / user_id / path / ...）作为顶层 key 注入。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

from config.logging_config import (
    APP_LOG_FILE,
    ERROR_LOG_FILE,
    LATENCY_LOG_FILE,
    LOG_BACKUP_DAYS,
    LOG_CONSOLE_MESSAGE,
    LOG_DIR,
    LOG_ENCODING,
    LOG_ROTATE_INTERVAL,
    LOG_ROTATE_SUFFIX,
    LOG_ROTATE_WHEN,
    PERF_LOG_FILE,
)
from utils._sanitize import SensitiveDataFilter
from utils.json_formatter import JsonFormatter
from utils.request_context import request_context
from utils.text_utils import preview_text

os.makedirs(LOG_DIR, exist_ok=True)


class RequestContextFilter(logging.Filter):
    """把 ContextVar 中的请求级字段注入到 LogRecord，从而进入 JSON 顶层。"""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = request_context.get({})

        record.request_id = ctx.get("request_id", "-")
        record.trace_id = ctx.get("trace_id", "-")
        record.ip = ctx.get("ip", "-")
        record.method = ctx.get("method", "-")
        record.path = ctx.get("path", "-")
        record.status = ctx.get("status", "-")
        record.user_id = ctx.get("user_id", "-")
        record.session_id = ctx.get("session_id", "-")

        # 额外业务自定义头透传（中间件按 EXTRA_CONTEXT_HEADERS 写入）
        extras = ctx.get("extras") or {}
        for k, v in extras.items():
            setattr(record, k, v)

        # params 中的敏感长文本（如 prompt/text）做预览截断，避免日志爆
        raw_params = ctx.get("params", {}) or {}
        safe_params = dict(raw_params)
        for key in ("prompt", "text", "content"):
            val = safe_params.get(key)
            if isinstance(val, str):
                safe_params[key] = preview_text(val, limit=80)
        record.params = safe_params

        return True


def _build_handler(filename: str, *, level: int = logging.INFO) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, filename),
        when=LOG_ROTATE_WHEN,
        interval=LOG_ROTATE_INTERVAL,
        backupCount=LOG_BACKUP_DAYS,
        encoding=LOG_ENCODING,
    )
    handler.setLevel(level)
    handler.suffix = LOG_ROTATE_SUFFIX
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    # 全局兜底：替换日志里的 token / 内网 IP / 已知敏感 env 实际值
    handler.addFilter(SensitiveDataFilter())
    return handler


def _get_logger(name: str, filename: str, level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:
        return logger
    logger.addHandler(_build_handler(filename, level=level))
    logger.propagate = False
    return logger


# app/error 文件 handler 实例（get_app_logger 首次构建后缓存）。setup_root_logging 复用
# 同一批实例挂到 root，避免对同一文件建两套 handler 引发轮转竞争。
_app_file_handlers: tuple[TimedRotatingFileHandler, ...] = ()


def get_app_logger() -> logging.Logger:
    """INFO+ → app.log，ERROR+ → error.log（共一个 logger，handler 分级）。"""
    logger = logging.getLogger("pythonframe.app")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    app_h = _build_handler(APP_LOG_FILE, level=logging.INFO)
    err_h = _build_handler(ERROR_LOG_FILE, level=logging.ERROR)
    logger.addHandler(app_h)
    logger.addHandler(err_h)
    global _app_file_handlers
    _app_file_handlers = (app_h, err_h)

    if LOG_CONSOLE_MESSAGE:
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(message)s"))
        console.addFilter(SensitiveDataFilter())
        logger.addHandler(console)

    logger.propagate = False
    return logger


def setup_root_logging() -> None:
    """让所有 logger（vibe.* 业务 logger、三方库等，而不仅是 pythonframe.app）
    都写进持久化的 app.log / error.log（宿主卷），不再只进 stdout（容器重建即丢）。

    做法：把 get_app_logger 那批文件 handler **实例**直接挂到 root logger 上。
    复用同一实例（而非新建）→ 同一文件只有一套轮转逻辑，不会竞争。
    pythonframe.app 自己 propagate=False，不会经由 root 重复写。"""
    root = logging.getLogger()
    if getattr(root, "_pm_root_configured", False):
        return
    get_app_logger()  # 确保 _app_file_handlers 已构建
    root.setLevel(logging.INFO)
    for h in _app_file_handlers:
        root.addHandler(h)
    root._pm_root_configured = True  # type: ignore[attr-defined]


def get_perf_logger() -> logging.Logger:
    return _get_logger("pythonframe.perf", PERF_LOG_FILE)


def get_access_logger() -> logging.Logger:
    return _get_logger("pythonframe.access", APP_LOG_FILE)


def get_latency_logger() -> logging.Logger:
    return _get_logger("pythonframe.latency", LATENCY_LOG_FILE, level=logging.WARNING)
