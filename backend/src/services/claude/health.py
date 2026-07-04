# -*- coding: utf-8 -*-
"""
健康探针 + 订阅保活 + 故障态维护。

- probe_slot：在 slot 容器里真跑一次极简 claude，验活（顺带触发订阅 OAuth 续期 = 保活）。
- probe_and_update：探测结果回写到路由池（healthy / unhealthy+cooldown）。
- probe_loop：后台周期任务，对每个 enabled slot 轮探。exec 撞 401 的即时故障转移在
  docker_manager.exec_claude 里；这里是周期性主动验活 + 让冷却过的 slot 复活。

订阅凭据续期：本设计里 slot 的 .claude 目录**直接**挂成 /workspace/.claude，容器内 claude
续期就地写回该 host 目录（rename 在挂载目录内，安全），无需跨用户 harvest。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from config.settings import settings
from services.claude import docker_manager as dm
from services.claude.registry import get_router
from services.claude.slots import Slot

log = logging.getLogger("claude.health")


@dataclass
class ProbeResult:
    slot_id: str
    healthy: bool
    detail: str


def probe_slot(slot: Slot) -> ProbeResult:
    """真跑一次极简 claude 验活。返回是否健康 + 说明。不抛异常（基础设施错也算 unhealthy）。"""
    try:
        info = dm.ensure_slot_container(slot)
        c = dm._client().containers.get(info["name"])
        res = c.exec_run(
            ["claude", "--dangerously-skip-permissions", "-p", "reply with the single word: pong"],
            user="node",
            workdir="/workspace",
            environment={"HOME": "/workspace"},
            demux=False,
        )
        out = res.output.decode("utf-8", "replace") if isinstance(res.output, bytes) else str(res.output)
        if res.exit_code == 0:
            return ProbeResult(slot.id, True, "ok")
        if dm.looks_like_auth_failure(out):
            return ProbeResult(slot.id, False, "auth failure: " + out[:160])
        return ProbeResult(slot.id, False, f"exit {res.exit_code}: " + out[:160])
    except dm.DockerManagerError as e:
        return ProbeResult(slot.id, False, f"infra error: {e}")
    except Exception as e:  # pragma: no cover - 兜底
        return ProbeResult(slot.id, False, f"probe error: {e}")


def probe_and_update(slot: Slot) -> ProbeResult:
    """探测 + 把结果写回路由池健康态。"""
    r = probe_slot(slot)
    router = get_router()
    if r.healthy:
        router.mark_healthy(slot.id)
    else:
        router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
    log.info("probe slot %s -> %s (%s)", r.slot_id, "healthy" if r.healthy else "UNHEALTHY", r.detail)
    return r


async def probe_loop() -> None:
    """后台周期探针。退出靠 task.cancel()。"""
    interval = max(60, settings.CLAUDE_PROBE_INTERVAL_SECONDS)
    log.info("claude probe_loop 启动，周期 %ds", interval)
    while True:
        try:
            # 先重扫共享账号目录（与小智账号池同源）：新增/删除账号免重启即生效
            try:
                from services.claude.registry import refresh_shared_slots
                await asyncio.to_thread(refresh_shared_slots)
            except Exception as e:  # noqa: BLE001
                log.warning("refresh_shared_slots 失败：%s", e)
            for slot in get_router().all_slots():
                if not slot.enabled:
                    continue
                await asyncio.to_thread(probe_and_update, slot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("probe_loop 本轮异常：%s", e)
        await asyncio.sleep(interval)
