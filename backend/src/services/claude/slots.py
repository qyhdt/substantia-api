# -*- coding: utf-8 -*-
"""
Slot 模型 —— 一个 slot = 一个「凭据身份」。扩展单位是 slot（sub），不是 user/container。

两种类型：
- subscription：官方订阅账号。**独占**一份 `.claude` 凭据（creds_dir）+ 预登录镜像（image）。
  绝不与别的 slot 共用 HOME —— Anthropic 的 rotating refresh_token 共用即雪崩 401。
- api_key：注入一组 `ANTHROPIC_*`（GLM / ChatGPT-via-LiteLLM / DeepSeek 等）。
  ⚠️ 初期只保留接口能力、不启用（见 plan §3.1）。

健康态由健康探针（M4）维护，不属于业务配置：unhealthy + 未到 cooldown_until → 暂不路由。
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, Field


class SlotType(str, Enum):
    SUBSCRIPTION = "subscription"
    API_KEY = "api_key"


class SlotHealth(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class Slot(BaseModel):
    # ---- 业务配置（持久化）----
    id: str = Field(min_length=1, max_length=64)
    type: SlotType = SlotType.SUBSCRIPTION
    enabled: bool = True
    # 加权 rendezvous：额度大的 sub 给更高 weight，按比例多吃用户。必须 > 0。
    weight: float = Field(default=1.0, gt=0)

    # subscription 专用
    creds_dir: Optional[str] = None   # 该 sub 独占的 .claude 卷 host 路径
    image: Optional[str] = None       # 预登录镜像，如 qyhdt/private:claude-loggedin-sub-a

    # api_key 专用：注入容器的 ANTHROPIC_* / CLAUDE_CODE_*
    env: Dict[str, str] = Field(default_factory=dict)

    # ---- 运行时健康态（不持久化）----
    health: SlotHealth = SlotHealth.HEALTHY
    cooldown_until: float = 0.0       # epoch 秒；> now 表示在冷却，暂不路由

    def is_routable(self, now: Optional[float] = None) -> bool:
        """能否被路由命中：enabled 且（健康，或 unhealthy 但冷却已过 → 乐观放行让健康探针顺带复活）。"""
        if not self.enabled:
            return False
        if self.health == SlotHealth.UNHEALTHY:
            now = time.time() if now is None else now
            if now < self.cooldown_until:
                return False
        return True

    def mark_unhealthy(self, cooldown_seconds: float = 600.0, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.health = SlotHealth.UNHEALTHY
        self.cooldown_until = now + max(0.0, cooldown_seconds)

    def mark_healthy(self) -> None:
        self.health = SlotHealth.HEALTHY
        self.cooldown_until = 0.0
