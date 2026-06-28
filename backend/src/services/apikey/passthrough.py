# -*- coding: utf-8 -*-
"""
原生 Messages API 透传（带 tools 的请求走这条，支持 Cursor 等 agent 的工具调用）。

为什么需要：Claude Code CLI 不接收客户端 tools、也不回 tool_use，所以 agent 模式跑不了。
本模块直接拿 slot 的凭据打原生 `api.anthropic.com/v1/messages`：
- subscription slot：用 OAuth access token（Bearer + anthropic-beta: oauth-2025-04-20），
  并在 system 开头注入 Claude Code 身份（否则订阅 OAuth 会被拒）。走订阅、不按 API 官方价。
- api_key slot：转发到 slot 配的 ANTHROPIC_BASE_URL，用其 key（x-api-key）。

token 轮换：每次实时读 slot 的 .credentials.json（probe_loop 保活续期）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from services.claude import docker_manager as dm
from services.claude.slots import Slot, SlotType

log = logging.getLogger("ak.passthrough")

ANTHROPIC_API = "https://api.anthropic.com"
# 订阅 OAuth 必须的身份系统提示（缺它会被 429/拒）
CC_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


def slot_oauth_token(slot: Slot) -> Optional[str]:
    """读 subscription slot 的 OAuth access token（实时，应对轮换）。"""
    if slot.type != SlotType.SUBSCRIPTION:
        return None
    try:
        p = dm.slot_creds_dir(slot) / ".credentials.json"
        o = json.loads(p.read_text(encoding="utf-8"))
        return (o.get("claudeAiOauth") or o).get("accessToken")
    except Exception as e:  # noqa: BLE001
        log.warning("read oauth token failed for slot %s: %s", slot.id, e)
        return None


def inject_identity(body: Dict[str, Any]) -> Dict[str, Any]:
    """把 Claude Code 身份块放到 system 最前（保留客户端原有 system 在其后）。"""
    cc = {"type": "text", "text": CC_IDENTITY}
    sys = body.get("system")
    if sys is None:
        body["system"] = [cc]
    elif isinstance(sys, str):
        body["system"] = [cc, {"type": "text", "text": sys}]
    elif isinstance(sys, list):
        body["system"] = [cc, *sys]
    return body


def upstream_for(slot: Slot, client_beta: Optional[str] = None) -> Tuple[str, Dict[str, str], bool]:
    """返回 (base_url, headers, is_oauth)。is_oauth=True 表示需要注入身份。"""
    if slot.type == SlotType.SUBSCRIPTION:
        tok = slot_oauth_token(slot)
        if not tok:
            raise RuntimeError(f"slot {slot.id} 无 OAuth token（凭据未就绪）")
        betas = "oauth-2025-04-20"
        if client_beta and "oauth-2025-04-20" not in client_beta:
            betas = f"{client_beta},oauth-2025-04-20"
        return ANTHROPIC_API, {
            "authorization": f"Bearer {tok}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": betas,
            "content-type": "application/json",
        }, True
    # api_key slot：转发到其配置的端点，用其 key
    env = slot.env or {}
    base = (env.get("ANTHROPIC_BASE_URL") or ANTHROPIC_API).rstrip("/")
    key = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
    if client_beta:
        headers["anthropic-beta"] = client_beta
    if key:
        headers["x-api-key"] = key
    return base, headers, False
