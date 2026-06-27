# -*- coding: utf-8 -*-
"""
集中读取环境变量。业务代码统一从 `settings` 单例取值，禁止再散落 os.getenv。

依赖：pydantic-settings >= 2。
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- HTTP ----------
    HOST: str = "0.0.0.0"
    PORT: int = 9999

    # 慢请求阈值（秒），中间件根据它写 latency.log
    REQUEST_LATENCY_THRESHOLD: float = 2.0

    # 中间件是否把请求参数打到日志（默认关闭，避免泄露）
    LOG_REQUEST_PARAMS: bool = False

    # 业务侧若希望把额外请求头并入 request_context，列在这里（CSV 字符串，取列表用 extra_context_headers_list）
    EXTRA_CONTEXT_HEADERS: str = ""

    # ---------- CORS ----------
    # CSV 字符串；取列表用 settings.cors_origins_list
    CORS_ORIGINS: str = ""
    CORS_ORIGIN_REGEX: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    # ---------- 鉴权 ----------
    JWT_SECRET: str = "CHANGE_ME_TO_ENV"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24 * 60   # 60 天
    AUTH_TOKEN_EXPIRE_HOURS: int = 24 * 60      # 60 天
    # 关闭鉴权（仅本地/测试用），设为 true 后 require_access_token 返回 dummy user
    AUTH_DISABLED: bool = False
    # 前端 cookie 模式下携带 access token 的 cookie 名（浏览器自动带）
    AUTH_COOKIE: str = "substantia_access_token"

    # ---------- Redis ----------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_POOL_MAX_CONNECTIONS: int = 200
    REDIS_BG_WRITE_CONCURRENCY: int = 50

    # ---------- PostgreSQL ----------
    DATABASE_URL: str = ""
    DB_POOL_MIN_SIZE: int = 1
    DB_POOL_MAX_SIZE: int = 20
    DB_COMMAND_TIMEOUT: int = 60

    # ---------- Claude 容器（slot 编排）----------
    # base 镜像（api_key slot 用；订阅 slot 用各自的预登录镜像 slot.image）
    CLAUDE_BASE_IMAGE: str = "claude-runner"
    # workspace 根目录：host 上 <root>/<slot_id>/ 挂成容器 /workspace；用户目录在其下 users/<uid>/
    CLAUDE_WORKSPACE_ROOT: str = "/var/lib/substantia/claude"
    # 每个 slot 容器的资源上限
    CLAUDE_CONTAINER_MEMORY: str = "3g"
    CLAUDE_CONTAINER_CPUS: float = 2.0
    # 单次 claude exec 超时（秒）
    CLAUDE_EXEC_TIMEOUT: int = 600
    # slot 池配置（JSON 数组）；留空 = 空池，需先配 slot 才能路由
    CLAUDE_SLOTS_JSON: str = ""

    # ---------- 权限 ----------
    # 拥有 /api/admin/* 权限的用户邮箱白名单（逗号分隔的 CSV 字符串；空 = 没人能用）
    # 取列表请用 settings.admin_emails_list（List[str]）
    ADMIN_EMAILS: str = ""

    # ---------- 派生属性 ----------
    @property
    def admin_emails_list(self) -> List[str]:
        return self._csv_to_list(self.ADMIN_EMAILS)

    @property
    def cors_origins_list(self) -> List[str]:
        return self._csv_to_list(self.CORS_ORIGINS)

    @property
    def extra_context_headers_list(self) -> List[str]:
        return self._csv_to_list(self.EXTRA_CONTEXT_HEADERS)

    @staticmethod
    def _csv_to_list(raw: str) -> List[str]:
        raw = (raw or "").strip()
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]


settings = Settings()
