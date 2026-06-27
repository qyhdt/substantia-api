# -*- coding: utf-8 -*-
"""
JSON 单行日志格式：所有日志输出为 JSON，便于聚合（ELK / Loki / CloudWatch）。
通过 RequestContextFilter 注入的请求级字段（trace_id 等）会作为顶层字段进入 JSON。
"""
import datetime
import json
import logging
import os

# 优先使用环境变量 LOG_TIMEZONE（如 Asia/Shanghai），未设置则用系统时区
_TZ_NAME = os.getenv("LOG_TIMEZONE", "")
try:
    import zoneinfo
    _TZ = zoneinfo.ZoneInfo(_TZ_NAME) if _TZ_NAME else None
except Exception:
    _TZ = None


class JsonFormatter(logging.Formatter):
    KEEP_FIELDS = {
        "filename",
        "lineno",
        "funcName",
        "created",
        "thread",
        "threadName",
        "process",
        "processName",
    }

    SKIP_FIELDS = {
        "msg",
        "args",
        "levelname",
        "levelno",
        "name",
        "pathname",
        "module",
        "taskName",
        "relativeCreated",
        "msecs",
        "stack_info",
        "exc_info",
        "exc_text",
    }

    def formatTime(self, record: logging.LogRecord, datefmt=None) -> str:
        """带时区缩写的时间，如 2026-02-19 08:43:18,439 CST。"""
        if _TZ:
            dt = datetime.datetime.fromtimestamp(record.created, tz=_TZ)
            fmt = datefmt or "%Y-%m-%d %H:%M:%S"
            s = dt.strftime(fmt)
            tz_abbr = dt.strftime("%Z")
            return f"{s},{record.msecs:03.0f} {tz_abbr}"
        import time as _time
        ct = _time.localtime(record.created)
        fmt = datefmt or "%Y-%m-%d %H:%M:%S"
        s = _time.strftime(fmt, ct)
        tz_abbr = _time.strftime("%Z", ct)
        return f"{s},{record.msecs:03.0f} {tz_abbr}"

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        for field in self.KEEP_FIELDS:
            payload[field] = getattr(record, field, None)

        for k, v in record.__dict__.items():
            if k.startswith("_"):
                continue
            if k in self.SKIP_FIELDS or k in payload or k in self.KEEP_FIELDS:
                continue
            payload[k] = self._safe(v)

        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _safe(value):
        try:
            json.dumps(value)
            return value
        except Exception:
            return str(value)
