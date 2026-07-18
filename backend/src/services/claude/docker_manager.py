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

import hashlib
import json
import logging
import os
import re
import shlex
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
_CONFIG_LABEL = "substantia.slot_config"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PROMPT_FILE = ".gateway_prompt.txt"


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
    # 容器名 = <prefix><slot_id>；前缀可用 CLAUDE_CONTAINER_PREFIX 改，多套栈同机避免撞名。
    prefix = settings.CLAUDE_CONTAINER_PREFIX or "claude-slot-"
    return f"{prefix}{slot_id}"


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


def write_prompt_file(workdir: Path, prompt: str) -> None:
    """Write prompt to a host-mounted file so docker exec argv stays small (E2BIG fix)."""
    p = workdir / _PROMPT_FILE
    p.write_text(prompt, encoding="utf-8")
    _chown_tree(p)


def container_prompt_path(user_id: str) -> str:
    return f"/workspace/users/{user_id}/{_PROMPT_FILE}"


def shell_exec_claude(user_id: str, *claude_args: str) -> List[str]:
    """Build `cat prompt | claude -p ...` so prompt never lands on argv (E2BIG fix)."""
    path = shlex.quote(container_prompt_path(user_id))
    parts = ["claude", "--dangerously-skip-permissions", "-p", *claude_args]
    shell = f"cat {path} | " + " ".join(shlex.quote(p) for p in parts)
    return ["sh", "-lc", shell]


def _resolve_image(slot: Slot) -> str:
    # subscription：一律用本地 base 镜像（Dockerfile.claude 构建的 claude-runner，只带 claude CLI）。
    # 凭据由 slot.creds_dir 挂载进 /workspace/.claude（见 _build_volumes），绝不烘进镜像、
    # 也不拉远端预登录镜像（qyhdt/private:claude-loggedin-*）。忽略 slot.image 里的历史遗留值。
    if slot.type == SlotType.SUBSCRIPTION:
        return settings.CLAUDE_BASE_IMAGE or "claude-runner"
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


def _slot_config_fingerprint(slot: Slot) -> str:
    """Return a secret-safe hash of everything baked into a slot container.

    API-key credentials live in the container environment.  Merely updating a
    slot in memory is therefore not enough: an already-running container would
    otherwise keep the old endpoint/key/model forever.  Only the digest is put
    in Docker labels; credential values are never exposed through labels or
    logs.
    """
    payload = {
        "type": slot.type.value,
        "image": _resolve_image(slot),
        "environment": _build_env(slot),
        "volumes": _build_volumes(slot),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


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
    """幂等确保容器存在，配置指纹变化时自动重建后再返回。"""
    from docker.errors import APIError, ImageNotFound, NotFound

    assert_safe_id(slot.id, "slot_id")
    name = container_name_for_slot(slot.id)
    _ensure_host_dirs(slot)
    client = _client()
    wanted_fingerprint = _slot_config_fingerprint(slot)

    try:
        c = client.containers.get(name)
        current_fingerprint = (c.labels or {}).get(_CONFIG_LABEL, "")
        if current_fingerprint != wanted_fingerprint:
            # The old container may contain a stale API key/model.  Recreate it
            # rather than logging either the old or new configuration.
            c.remove(force=True)
            log.info("slot 容器 %s 配置已变化，正在安全重建", name)
        else:
            if c.status == "running":
                return {"container_id": c.id, "name": c.name, "status": c.status}
            old_status = c.status
            c.start()
            c.reload()
            log.info("slot 容器 %s 已启动（原 %s）", name, old_status)
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
                _CONFIG_LABEL: wanted_fingerprint,
            },
            restart_policy={"Name": "unless-stopped"},
        )
        c.reload()
        _wait_ready(c, slot)
        log.info("slot 容器 %s 已创建 id=%s image=%s", name, c.id[:12], image)
        return {"container_id": c.id, "name": c.name, "status": c.status}
    except ImageNotFound as e:
        raise DockerManagerError(
            f"镜像不存在：{image}。本地 build base（只带 claude CLI，凭据靠挂载）："
            f"docker build -t {settings.CLAUDE_BASE_IMAGE} "
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
            "config_fingerprint": (c.labels or {}).get(_CONFIG_LABEL),
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
# 执行 + 鉴权失败检测 + 故障转移
# ============================================================================
_AUTH_FAIL_MARKERS = (
    "401",
    "403",
    "429",
    "invalid authentication",
    "authentication_error",
    "unauthorized",
    "failed to authenticate",
    "invalid api key",
    "invalid bearer token",
    "oauth",
    "expired",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota",
    "usage limit",
    "weekly limit",
    "limit reached",
    "access has been disabled",
    "overloaded",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)


def looks_like_auth_failure(text: str) -> bool:
    """粗判需切换上游的鉴权、额度或暂时不可用错误。"""
    t = (text or "").lower()
    return any(m in t for m in _AUTH_FAIL_MARKERS)


class ClaudeExecResult:
    def __init__(self, slot_id: str, exit_code: int, output: str, *, auth_failed: bool = False,
                 attempts: int = 1):
        self.slot_id = slot_id
        self.exit_code = exit_code
        self.output = output
        self.auth_failed = auth_failed
        self.attempts = attempts

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self) -> str:
        return (f"ClaudeExecResult(slot={self.slot_id}, rc={self.exit_code}, "
                f"auth_failed={self.auth_failed}, out={self.output[:60]!r})")


def _exec_in_slot(slot: Slot, user_id: str, prompt: str) -> ClaudeExecResult:
    """在指定 slot 容器里、该用户目录中跑一次 claude。prompt 写文件再 cat，避免 argv 过长。"""
    info = ensure_slot_container(slot)

    wd = user_workdir(slot.id, user_id)
    wd.mkdir(parents=True, exist_ok=True)
    _chown_tree(wd)
    _chown_tree(wd.parent)
    container_wd = f"/workspace/users/{user_id}"
    write_prompt_file(wd, prompt)

    c = _client().containers.get(info["name"])
    res = c.exec_run(
        shell_exec_claude(user_id),
        user="node",
        workdir=container_wd,
        environment={"HOME": "/workspace"},
        demux=False,
    )
    out = res.output.decode("utf-8", "replace") if isinstance(res.output, bytes) else str(res.output)
    # Subscription CLI failures are retried only when they look provider-side;
    # a user/prompt error should not poison the whole subscription slot.  An
    # API-key fallback slot, however, is a provider tier: any non-zero CLI
    # result means this tier did not serve the request and the next tier gets a
    # chance (Gemini -> GLM).
    auth_failed = res.exit_code != 0 and (
        slot.type == SlotType.API_KEY or looks_like_auth_failure(out)
    )
    return ClaudeExecResult(slot.id, res.exit_code, out, auth_failed=auth_failed)


def exec_claude(user_id: str, prompt: str) -> ClaudeExecResult:
    """路由 user → slot → exec。撞鉴权失败时标该 slot 不健康并改路由到其它健康 slot（故障转移）。

    非鉴权类失败（如用户 prompt 报错）直接返回，不重试（避免无意义重跑）。
    """
    assert_safe_id(user_id, "user_id")
    router = get_router()
    # A fixed budget of three can be consumed by a multi-account subscription
    # pool before reaching Gemini/GLM.  Try each configured slot at most once.
    max_attempts = max(1, settings.CLAUDE_EXEC_MAX_ATTEMPTS, len(router.all_slots()))
    last: Optional[ClaudeExecResult] = None

    for attempt in range(1, max_attempts + 1):
        slot = router.route(user_id)              # 无可路由 slot → NoRoutableSlotError
        res = _exec_in_slot(slot, user_id, prompt)
        res.attempts = attempt
        if not res.auth_failed:
            return res                            # 成功 / 非鉴权失败：直接返回
        # 鉴权失败：标该 slot 不健康（从路由剔除），下一轮 route() 自动落到其它健康 slot
        log.warning("slot %s 上游失败，标记不健康并故障转移（第 %d 次）", slot.id, attempt)
        router.mark_unhealthy(slot.id, settings.CLAUDE_UNHEALTHY_COOLDOWN_SECONDS)
        last = res

    return last  # 所有尝试都鉴权失败
