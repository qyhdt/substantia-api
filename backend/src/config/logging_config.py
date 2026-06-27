# -*- coding: utf-8 -*-
"""日志配置：路径、轮转策略、阈值。"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOG_DIR = os.getenv("LOG_DIR", os.path.join(BASE_DIR, "logs"))

APP_LOG_FILE = "app.log"
ERROR_LOG_FILE = "error.log"
PERF_LOG_FILE = "perf.log"
LATENCY_LOG_FILE = "latency.log"

# 按小时切割：when="H" interval=4 表示每 4 小时轮转一次
LOG_ROTATE_WHEN = "H"
LOG_ROTATE_INTERVAL = 4
# 轮转文件名后缀
LOG_ROTATE_SUFFIX = "%Y-%m-%d_%H"
# 按 4 小时轮转时保留 30 天（30*6 个备份文件）
LOG_BACKUP_DAYS = 30 * 6
LOG_ENCODING = "utf8"

# perf_monitor 装饰器的默认慢函数阈值（秒）
PERF_LATENCY_THRESHOLD = 5

# 研发时可在控制台单独看 message
LOG_CONSOLE_MESSAGE = os.getenv("LOG_CONSOLE_MESSAGE", "false").lower() in ("true", "1", "yes")
