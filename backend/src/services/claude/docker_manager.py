# -*- coding: utf-8 -*-
"""
容器编排（方案 A：每个 slot 一个常驻容器）。

一个 slot 容器服务它名下所有用户（用户经 HRW 路由固定落到该 slot）：
- subscription slot：挂该 slot **独占**的 `.claude` 凭据目录到 /workspace/.claude，
  容器内所有用户**共用这一份**凭据（单一来源续期，绝不 per-user 拷贝 → 避开 rotating
  refresh_token 雪崩）。每用户工作目录隔离在 /workspace/users/<uid>/。
- api_key slot：不挂凭据，凭据走注入的 ANTHROPIC_* 环境变量。

一次 claude 调用 = 一次 `docker exec`，cwd 设到该用户目录，HOME=/workspace。
claude 的会话转录按 cwd 路径分目录（projects/<slug-of-cwd>），天然按用户路径分开。

docker SDK 在 `_client()` 内惰性 import，使纯逻辑可在无 docker 包/守护进程时被测试。
依赖 / 健康保活见 doc/claude-docker-plan.md（M4）。
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional

from config.settings import settings
from services.claude.registry import get_router
from services.claude.slots import Slot, SlotType

log = logging.getLogger("claude.docker")

# node:20 镜像自带 node 用户 uid/gid = 1000；claude 拒绝在 root 下跑 --dangerously-skip-permissions
_NODE_UID = 1000
_NODE_GID = 1000

_LABEL_KEY = "substantia.claude"
_LABEL_VALUE = "slot-container"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DockerManagerError(RuntimeError):
    pass


def assert_safe_id(value: str, field: str = "id") -> None:
    if not value or not _SAFE_ID_RE.match(value):
        raise DockerManagerError(f"unsafe {field}: {value!r}（仅允许字母数字 _ - ，≤64）")


# ============================================================================
# 命名 / 路径 / 镜像 / 环境
# ============================================================================
def container_name_for_slot(slot_id: str) -> str:
    assert_safe_id(slot_id, "slot_id")
    return f"claude-slot-{slot_id}"


def _workspace_root() -> Path:
    return Path(settings.CLAUDE_WORKSPACE_ROOT).expanduser()


def slot_workspace_dir(slot_id: str) -> Path:
    """该 slot 容器的 /workspace 对应的 host 目录。"""
    assert_safe_id(slot_id, "slot_id")
    return _workspace_root() / slot_id


def slot_creds_dir(slot: Slot) -> Path:
    """subscription slot 独占的 .claude 凭据目录（host）。slot.creds_dir 优先，否则默认在 workspace 下。"""
    if slot.creds_dir:
        return Path(slot.creds_dir).expanduser()
    return slot_workspace_dir(slot.id) / ".claude-creds"


def user_workdir(slot_id: str, user_id: str) -> Path:
    assert_safe_id(user_id, "user_id")
    return slot_workspace_dir(slot_id) / "users" / user_id


def _resolve_image(slot: Slot) -> str:
    if slot.type == SlotType.SUBSCRIPTION:
        if not slot.image:
            raise DockerManagerError(
                f"subscription slot {slot.id} 缺 image（需预登录镜像，如 qyhdt/private:claude-loggedin-{slot.id}）"
            )
        return slot.image
    # api_key：slot 指定镜像优先，否则用 base 镜像
    return slot.image or settings.CLAUDE_BASE_IMAGE


def _build_env(slot: Slot) -> dict:
    """注入容器的环境变量。subscription 绝不注入 ANTHROPIC_*（会盖掉 OAuth）；api_key 注入 slot.env。"""
    env = {"HOME": "/workspace"}
    if slot.type == SlotType.SUBSCRIPTION:
        # 仅透传无害的 claude-code 行为开关（如 CLAUDE_CODE_*）
        for k, v in (slot.env or {}).items():
            if k.upper().startswith("CLAUDE_CODE_"):
                env[k] = v
    else:
        env.update(slot.env or {})
    return {k: v for k, v in env.items() if v}


def _chown_tree(p: Path) -> None:
    try:
        os.chown(p, _NODE_UID, _NODE_GID)
    except Exception:
        pass


def _ensure_host_dirs(slot: Slot) -> None:
    """准备挂载源目录（mount 来源必须先存在）+ chown 给 node。"""
    ws = slot_workspace_dir(slot.id)
    ws.mkdir(parents=True, exist_ok=True)
    _chown_tree(ws)
    if slot.type == SlotType.SUBSCRIPTION:
        creds = slot_creds_dir(slot)
        creds.mkdir(parents=True, exist_ok=True)
        _chown_tree(creds)


def _build_volumes(slot: Slot) -> dict:
    volumes = {str(slot_workspace_dir(slot.id)): {"bind": "/workspace", "mode": "rw"}}
    if slot.type == SlotType.SUBSCRIPTION:
        # 嵌套挂到 /workspace/.claude（Docker 按挂载点深度排序覆盖该子目录）
        volumes[str(slot_creds_dir(slot))] = {"bind": "/workspace/.claude", "mode": "rw"}
    return volumes


# ============================================================================
# docker 客户端
# ============================================================================
def _client():
    try:
        import docker  # 惰性 import：纯逻辑测试无需装 docker 包
    except Exception as e:  # pragma: no cover
        raise DockerManagerError(f"docker SDK 未安装：{e}") from e
    try:
        return docker.from_env()
    except Exception as e:  # pragma: no cover
        raise DockerManagerError(f"连接 docker 守护进程失败：{e}") from e


def is_docker_reachable() -> bool:
    try:
        _client().ping()
        return True
    except Exception:
        return False


# ============================================================================
# 容器生命周期
# ============================================================================
def _wait_ready(c, slot: Slot, timeout: float = 8.0) -> None:
    """等容器就绪：claude 可用 +（订阅档）凭据就位，避免首条 exec 抢跑。best-effort。"""
    sub = slot.type == SlotType.SUBSCRIPTION
    check = "claude --version >/dev/null 2>&1" + (
        " && test -s /workspace/.claude/.credentials.json" if sub else ""
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if c.exec_run(["sh", "-c", check], user="node").exit_code == 0:
                return
        except Exception:
            pass
        time.sleep(0.3)
    log.warning("slot 容器 %s 未在 %.0fs 内就绪（仍继续）", c.name, timeout)


def ensure_slot_container(slot: Slot) -> dict:
    """幂等：无→建并启；停了→start；在跑→直接返回。返回 {container_id, name, status}。"""
    from docker.errors import APIError, ImageNotFound, NotFound

    assert_safe_id(slot.id, "slot_id")
    name = container_name_for_slot(slot.id)
    _ensure_host_dirs(slot)
    client = _client()

    try:
        c = client.containers.get(name)
        if c.status == "running":
            return {"container_id": c.id, "name": c.name, "status": c.status}
        c.start()
        c.reload()
        log.info("slot 容器 %s 已启动（原 %s）", name, c.status)
        return {"container_id": c.id, "name": c.name, "status": c.status}
    except NotFound:
        pass

    image = _resolve_image(slot)
    try:
        c = client.containers.run(
            image=image,
            name=name,
            command=["sleep", "infinity"],
            user="node",
            detach=True,
            environment=_build_env(slot),
            volumes=_build_volumes(slot),
            working_dir="/workspace",
            mem_limit=settings.CLAUDE_CONTAINER_MEMORY,
            nano_cpus=int(settings.CLAUDE_CONTAINER_CPUS * 1_000_000_000),
            oom_score_adj=800,  # 宿主 OOM 时优先牺牲 claude 容器（可重试），别杀 backend/db
            labels={
                _LABEL_KEY: _LABEL_VALUE,
                "substantia.slot_id": slot.id,
                "substantia.slot_type": slot.type.value,
            },
            restart_policy={"Name": "unless-stopped"},
        )
        c.reload()
        _wait_ready(c, slot)
        log.info("slot 容器 %s 已创建 id=%s image=%s", name, c.id[:12], image)
        return {"container_id": c.id, "name": c.name, "status": c.status}
    except ImageNotFound as e:
        raise DockerManagerError(
            f"镜像不存在：{image}。subscription 需先 pull 预登录镜像；"
            f"api_key 需 build base：docker build -t {settings.CLAUDE_BASE_IMAGE} "
            f"-f devops/claude_docker/Dockerfile.claude devops/claude_docker"
        ) from e
    except APIError as e:
        raise DockerManagerError(f"创建 slot 容器失败：{e}") from e


def stop_slot_container(slot_id: str, *, remove: bool = False) -> Optional[str]:
    from docker.errors import NotFound

    name = container_name_for_slot(slot_id)
    try:
        c = _client().containers.get(name)
    except NotFound:
        return None
    cid = c.id
    try:
        if remove:
            c.remove(force=True)
        else:
            c.stop()
    except Exception as e:
        raise DockerManagerError(f"停止 slot 容器 {name} 失败：{e}") from e
    return cid


def slot_container_status(slot_id: str) -> Optional[str]:
    from docker.errors import NotFound

    try:
        return _client().containers.get(container_name_for_slot(slot_id)).status
    except NotFound:
        return None


def list_slot_containers() -> List[dict]:
    cs = _client().containers.list(all=True, filters={"label": f"{_LABEL_KEY}={_LABEL_VALUE}"})
    return [
        {
            "name": c.name,
            "status": c.status,
            "slot_id": (c.labels or {}).get("substantia.slot_id"),
            "slot_type": (c.labels or {}).get("substantia.slot_type"),
        }
        for c in cs
    ]


def ensure_all_enabled() -> List[dict]:
    """把池里所有 enabled slot 的容器都拉起来。部署/启动时调一次 → 所有容器可访问。"""
    out = []
    for slot in get_router().all_slots():
        if not slot.enabled:
            continue
        try:
            out.append({"slot_id": slot.id, **ensure_slot_container(slot)})
        except DockerManagerError as e:
            log.error("ensure slot %s 失败：%s", slot.id, e)
            out.append({"slot_id": slot.id, "error": str(e)})
    return out


# ============================================================================
# 执行
# ============================================================================
class ClaudeExecResult:
    def __init__(self, slot_id: str, exit_code: int, output: str):
        self.slot_id = slot_id
        self.exit_code = exit_code
        self.output = output

    def __repr__(self) -> str:
        return f"ClaudeExecResult(slot={self.slot_id}, rc={self.exit_code}, out={self.output[:60]!r})"


def exec_claude(user_id: str, prompt: str) -> ClaudeExecResult:
    """路由 user → slot → ensure 容器 → 在该用户目录里跑 `claude -p <prompt>`。

    prompt 以 argv 形式传入（非 shell 拼接），无注入风险。
    """
    assert_safe_id(user_id, "user_id")
    slot = get_router().route(user_id)            # 无可路由 slot 会抛 NoRoutableSlotError
    info = ensure_slot_container(slot)

    # 准备该用户的工作目录（host 侧 mkdir，容器内即 /workspace/users/<uid>）
    wd = user_workdir(slot.id, user_id)
    wd.mkdir(parents=True, exist_ok=True)
    _chown_tree(wd)
    _chown_tree(wd.parent)
    container_wd = f"/workspace/users/{user_id}"

    c = _client().containers.get(info["name"])
    res = c.exec_run(
        ["claude", "--dangerously-skip-permissions", "-p", prompt],
        user="node",
        workdir=container_wd,
        environment={"HOME": "/workspace"},
        demux=False,
    )
    output = res.output.decode("utf-8", "replace") if isinstance(res.output, bytes) else str(res.output)
    return ClaudeExecResult(slot.id, res.exit_code, output)
