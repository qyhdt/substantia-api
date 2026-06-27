# -*- coding: utf-8 -*-
"""
计费感知的 claude 执行器（网关用）。

为什么不直接用 services.claude.docker_manager.exec_claude：
- 那个跑 `claude -p <prompt>`（纯文本输出），拿不到 token 用量，无法按量计费。
本模块**复用容器团队的路由 + 容器生命周期**（registry/docker_manager），仅把执行命令
换成 `claude -p ... --output-format json`，从而拿到 usage(input/output tokens) 与结果文本，
并镜像它的「鉴权失败 → 标 slot 不健康 → 故障转移到其它 slot」逻辑（sub 用光自动落到 api_key slot）。

集成缝：本模块依赖 services.claude 的若干内部 helper（_client/_chown_tree）。容器团队若
日后提供「带 usage 的 exec」官方接口，可改为直接调用、删掉这里的重复执行逻辑。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from config.settings import settings
from services.claude import docker_manager as dm
from services.claude.registry import get_router
from services.claude.slots import Slot, SlotType
from utils.pm_logger import get_app_logger

log = get_app_logger()

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


class RunnerResult:
    def __init__(
        self,
        *,
        slot_id: str,
        slot_type: str,
        model: str,
        text: str,
        prompt_tokens: int,
        completion_tokens: int,
        exit_code: int,
        auth_failed: bool,
        attempts: int,
        estimated: bool,
    ):
        self.slot_id = slot_id
        self.slot_type = slot_type
        self.model = model
        self.text = text
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.exit_code = exit_code
        self.auth_failed = auth_failed
        self.attempts = attempts
        self.estimated = estimated  # token 是否为字符估算（解析失败兜底）

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _billed_model(slot: Slot, requested_model: str) -> str:
    """计价用的「实际命中模型」：api_key slot 用其注入的 ANTHROPIC_MODEL；订阅 slot 用请求模型。"""
    if slot.type == SlotType.API_KEY:
        return (slot.env or {}).get("ANTHROPIC_MODEL") or requested_model
    return requested_model


def _parse_usage(output: str) -> Optional[Dict[str, Any]]:
    """从 `--output-format json` 输出里抽 {text, input_tokens, output_tokens}。失败返回 None。"""
    raw = (output or "").strip()
    if not raw:
        return None
    obj = None
    try:
        obj = json.loads(raw)
    except Exception:
        m = _JSON_OBJ_RE.search(raw)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage") or {}
    in_tok = int(usage.get("input_tokens", 0) or 0) + int(usage.get("cache_read_input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    text = obj.get("result")
    if text is None:
        text = obj.get("text") or obj.get("content") or ""
    return {"text": text if isinstance(text, str) else json.dumps(text), "in": in_tok, "out": out_tok}


def _estimate_tokens(s: str) -> int:
    return max(1, len(s or "") // 4)  # 粗估：~4 字符/token


def _exec_json(slot: Slot, user_id: str, prompt: str, model: str) -> RunnerResult:
    """在 slot 容器里、该用户目录跑 `claude -p ... --output-format json`。"""
    info = dm.ensure_slot_container(slot)

    wd = dm.user_workdir(slot.id, user_id)
    wd.mkdir(parents=True, exist_ok=True)
    dm._chown_tree(wd)
    dm._chown_tree(wd.parent)
    container_wd = f"/workspace/users/{user_id}"

    cmd = ["claude", "--dangerously-skip-permissions", "--output-format", "json"]
    if slot.type == SlotType.SUBSCRIPTION and model:
        cmd += ["--model", model]   # 订阅档可指定模型；api_key 档模型由注入的 env 决定
    cmd += ["-p", prompt]

    c = dm._client().containers.get(info["name"])
    res = c.exec_run(cmd, user="node", workdir=container_wd,
                     environment={"HOME": "/workspace"}, demux=False)
    out = res.output.decode("utf-8", "replace") if isinstance(res.output, bytes) else str(res.output)
    auth_failed = res.exit_code != 0 and dm.looks_like_auth_failure(out)

    parsed = _parse_usage(out)
    estimated = parsed is None
    if parsed is not None:
        text, in_tok, out_tok = parsed["text"], parsed["in"], parsed["out"]
        if in_tok == 0 and out_tok == 0:  # 输出是 JSON 但没 usage 字段
            estimated = True
            in_tok, out_tok = _estimate_tokens(prompt), _estimate_tokens(text)
    else:
        text = out
        in_tok, out_tok = _estimate_tokens(prompt), _estimate_tokens(out)

    return RunnerResult(
        slot_id=slot.id, slot_type=slot.type.value,
        model=_billed_model(slot, model), text=text,
        prompt_tokens=in_tok, completion_tokens=out_tok,
        exit_code=res.exit_code, auth_failed=auth_failed, attempts=1, estimated=estimated,
    )


def run(user_id: str, prompt: str, model: str) -> RunnerResult:
    """路由 user → slot → exec(json)。鉴权失败时标该 slot 不健康并转移到其它健康 slot。

    与 docker_manager.exec_claude 同构：sub slot 用光(401/限额) → 被剔除 → HRW 落到
    其它 slot（可能是 api_key slot），即「sub 不够接 apikey」的池级实现。
    """
    dm.assert_safe_id(user_id, "user_id")
    router = get_router()
    max_attempts = max(1, settings.CLAUDE_EXEC_MAX_ATTEMPTS)
    last: Optional[RunnerResult] = None

    for attempt in range(1, max_attempts + 1):
        slot = router.route(user_id)               # 无可路由 slot → NoRoutableSlotError
        result = _exec_json(slot, user_id, prompt, model)
        result.attempts = attempt
        if not result.auth_failed:
            return result
        log.warning("ak_runner slot %s 鉴权失败，转移（第 %d 次）", slot.id, attempt)
        router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
        last = result

    return last
