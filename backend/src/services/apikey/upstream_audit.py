# -*- coding: utf-8 -*-
"""上游审计日志：把 substantia 封装后、发往 api.anthropic.com/v1/messages 的完整请求体落盘。

- 每条一行 JSON，**全量、不截断**（body 原样记录）。
- 按天轮转（when=midnight），轮转后的旧文件 gzip，保留 30 天（backupCount=30）。
- 落盘经 QueueHandler/QueueListener 异步化，不阻塞请求事件循环（gzip 在轮转线程里发生）。
- 开关：settings.LOG_UPSTREAM_BODY（默认 True）。目录：<LOG_DIR>/upstream/messages.jsonl[.YYYY-MM-DD.gz]。

记录失败绝不影响请求主流程（全部 try/except 吞掉）。
"""
from __future__ import annotations

import atexit
import gzip
import json
import logging
import os
import shutil
import time
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from queue import SimpleQueue
from typing import Any, Dict, Optional

from config.logging_config import LOG_DIR

log = logging.getLogger("ak.upstream_audit_err")  # 仅用于记录审计自身的异常

UPSTREAM_LOG_DIR = os.path.join(LOG_DIR, "upstream")
_RETAIN_DAYS = 30

_audit: Optional[logging.Logger] = None
_listener: Optional[QueueListener] = None


def _gzip_rotator(source: str, dest: str) -> None:
    """轮转时把当天写满的明文文件压成 .gz 并删除原文件。"""
    with open(source, "rb") as sf, gzip.open(dest, "wb") as df:
        shutil.copyfileobj(sf, df)
    os.remove(source)


def _gz_namer(name: str) -> str:
    return name + ".gz"


def _build() -> logging.Logger:
    """惰性构建审计 logger（进程内单例）。"""
    global _audit, _listener
    if _audit is not None:
        return _audit

    os.makedirs(UPSTREAM_LOG_DIR, exist_ok=True)
    fh = TimedRotatingFileHandler(
        filename=os.path.join(UPSTREAM_LOG_DIR, "messages.jsonl"),
        when="midnight",
        interval=1,
        backupCount=_RETAIN_DAYS,  # 每天一个备份 → 保留 30 天，更旧的自动删
        encoding="utf-8",
    )
    fh.suffix = "%Y-%m-%d"
    fh.rotator = _gzip_rotator
    fh.namer = _gz_namer
    fh.setFormatter(logging.Formatter("%(message)s"))  # message 本身即整行 JSON

    q: "SimpleQueue[logging.LogRecord]" = SimpleQueue()
    qh = QueueHandler(q)
    logger = logging.getLogger("ak.upstream_audit")
    logger.setLevel(logging.INFO)
    logger.addHandler(qh)
    logger.propagate = False

    _listener = QueueListener(q, fh, respect_handler_level=False)
    _listener.start()
    atexit.register(_listener.stop)
    _audit = logger
    return logger


def record_upstream(
    *,
    endpoint: str,
    body: Dict[str, Any],
    uid: str = "-",
    key_id: Any = None,
    slot_id: Any = None,
    oauth: bool = False,
    base: str = "",
) -> None:
    """记录一条「即将发往上游」的完整请求体。endpoint: 'anthropic' | 'openai'。"""
    try:
        from config.settings import settings
        if not getattr(settings, "LOG_UPSTREAM_BODY", True):
            return
        # body 只序列化一次：先得到 body_json 算字节数，再拼到外层元数据后面（body 放最后）。
        body_json = json.dumps(body, ensure_ascii=False, default=str)
        meta = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "endpoint": endpoint,
            "base": base,
            "uid": uid,
            "key_id": key_id,
            "slot_id": slot_id,
            "oauth": oauth,
            "model": body.get("model"),
            "stream": bool(body.get("stream")),
            "byte_size": len(body_json.encode("utf-8")),
        }
        line = json.dumps(meta, ensure_ascii=False, default=str)[:-1] + ',"body":' + body_json + "}"
        _build().info(line)
    except Exception as e:  # noqa: BLE001 — 审计绝不能影响请求
        try:
            log.warning("upstream audit record failed: %s: %s", type(e).__name__, e)
        except Exception:
            pass
