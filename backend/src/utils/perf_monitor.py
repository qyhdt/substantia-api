# -*- coding: utf-8 -*-
"""
perf_monitor 装饰器：埋点函数耗时；超阈值时写 latency.log。

支持：
- 同步函数
- 异步函数
- 异步生成器（流式输出）
用法：
    @perf_monitor
    @perf_monitor()
    @perf_monitor(threshold=3.0)
"""
import functools
import inspect
import time
from typing import Any, AsyncGenerator, Callable, Optional

from config.logging_config import PERF_LATENCY_THRESHOLD
from utils.pm_logger import get_app_logger, get_latency_logger, get_perf_logger

app_logger = get_app_logger()
perf_logger = get_perf_logger()
latency_logger = get_latency_logger()


def perf_monitor(func: Optional[Callable] = None, *, threshold: Optional[float] = None):
    def decorator(fn: Callable):
        final_threshold = threshold if threshold is not None else PERF_LATENCY_THRESHOLD

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def asyncgen_wrapper(*args: Any, **kwargs: Any) -> AsyncGenerator:
                start = time.perf_counter()
                agen = fn(*args, **kwargs)
                yielded = 0
                try:
                    async for item in agen:
                        yielded += 1
                        yield item
                finally:
                    cost = time.perf_counter() - start
                    msg = f"function={fn.__qualname__} cost={cost:.4f}s yielded={yielded}"
                    app_logger.info(msg)
                    perf_logger.info(msg)
                    if cost >= final_threshold:
                        latency_logger.warning("%s threshold=%.2fs", msg, final_threshold)

            return asyncgen_wrapper

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any):
                start = time.perf_counter()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    cost = time.perf_counter() - start
                    msg = f"function={fn.__qualname__} cost={cost:.4f}s"
                    app_logger.info(msg)
                    perf_logger.info(msg)
                    if cost >= final_threshold:
                        latency_logger.warning("%s threshold=%.2fs", msg, final_threshold)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                cost = time.perf_counter() - start
                msg = f"function={fn.__qualname__} cost={cost:.4f}s"
                app_logger.info(msg)
                perf_logger.info(msg)
                if cost >= final_threshold:
                    latency_logger.warning("%s threshold=%.2fs", msg, final_threshold)

        return sync_wrapper

    if callable(func):
        return decorator(func)
    return decorator
