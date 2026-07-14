# -*- coding: utf-8 -*-
"""
交互式 Claude 订阅登录：admin 后台一个网页终端（xterm.js）直连服务器上的登录容器。

`claude auth login` 的 code 输入 / 组织选择走交互式 TUI，需要真 TTY——所以这里用 PTY 起
`docker run -it ... claude auth login --claudeai`，把 PTY 的原始字节（含 ANSI）经 HTTP 轮询
透传给前端 xterm，前端按键也原样写回 PTY。用户自己在终端里跑完登录（开 URL、贴 code、
必要时选组织），claude 把 .credentials.json 写进挂载的账号目录 → 收编成一个 subscription slot。

传输用轮询而非 websocket，避免动共享反代（改动影响全站）。
会话状态在内存里（进程级），用锁保护；每个会话有 TTL 看门狗，起了不完成的到点自动清理容器。

绝不记录凭据 / OAuth code：只透传字节给前端，不落日志。
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import pty
import secrets
import shutil
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from config.settings import settings
from services.claude.docker_manager import (
    DockerManagerError,
    assert_safe_id,
    container_name_for_slot,
    is_docker_reachable,
    slot_creds_dir,
    slot_workspace_dir,
)
from services.claude.registry import accounts_dir

log = logging.getLogger("claude.login")

# 登录会话生命周期上限：起了不完成的，到点自动清理容器。
_LOGIN_SESSION_TTL = 20 * 60      # 秒
_LOGIN_MAX_BUF = 256 * 1024       # 输出环形上限，超出丢弃最早的

_NODE_UID = 1000
_NODE_GID = 1000


def _login_image() -> str:
    """登录容器用的镜像：带 claude CLI 的 base。"""
    return (settings.CLAUDE_BASE_IMAGE or "").strip() or "claude-runner"


def _login_container_name(acc_id: str) -> str:
    """登录容器名（临时，非 slot）：<prefix>login-<acc>。与 slot 容器前缀一致，避免撞名。"""
    prefix = settings.CLAUDE_CONTAINER_PREFIX or "claude-slot-"
    return f"{prefix}login-{acc_id}"


def _chown(p: Path) -> None:
    try:
        os.chown(p, _NODE_UID, _NODE_GID)
    except Exception:  # noqa: BLE001
        pass


def _creds_file_usable(path: Path) -> bool:
    """判断凭据文件里是否还有可用凭据。
    claude CLI 登出/刷新失败时会把 accessToken/refreshToken 清成空串但保留文件，
    这种空壳不算可用，不应挡住重新登录。文件不存在/空文件 → 不可用；
    解析不了或没有 claudeAiOauth 的未知格式 → 保守视为可用（不许覆盖）。"""
    try:
        b = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False
    if not b.strip():
        return False
    try:
        m = json.loads(b)
    except Exception:  # noqa: BLE001
        return True
    if not isinstance(m, dict):
        return True
    oauth = m.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return True
    access = oauth.get("accessToken") or ""
    refresh = oauth.get("refreshToken") or ""
    return bool(access) or bool(refresh)


class _LoginSession:
    def __init__(self, sid: str, acc_id: str, creds_dir: Path, cname: str,
                 proc: subprocess.Popen, master_fd: int) -> None:
        self.id = sid
        self.acc_id = acc_id
        self.creds_dir = creds_dir
        self.cname = cname
        self.proc = proc
        self.master_fd = master_fd
        self.lock = threading.Lock()
        self.buf = bytearray()       # PTY 累积输出（含 ANSI 原始字节）
        self.dropped = 0             # 因超上限被丢弃的前缀字节数（offset 基准）
        self.exited = False
        self.created = time.time()


_login_mu = threading.Lock()
_login_sessions: Dict[str, _LoginSession] = {}


def _get_login(sid: str) -> Optional[_LoginSession]:
    with _login_mu:
        return _login_sessions.get(sid)


def _drop_login(sid: str) -> None:
    with _login_mu:
        _login_sessions.pop(sid, None)


def _docker_rm_f(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def _reader(s: _LoginSession) -> None:
    """后台读 PTY master fd 到 buf（环形上限）。fd 关闭 / EOF → 标记 exited。"""
    while True:
        try:
            chunk = os.read(s.master_fd, 4096)
        except OSError:
            chunk = b""
        if chunk:
            with s.lock:
                s.buf += chunk
                if len(s.buf) > _LOGIN_MAX_BUF:
                    cut = len(s.buf) - _LOGIN_MAX_BUF
                    del s.buf[:cut]
                    s.dropped += cut
        else:
            with s.lock:
                s.exited = True
            return


def _watchdog(sid: str) -> None:
    time.sleep(_LOGIN_SESSION_TTL)
    s = _get_login(sid)
    if s is not None:
        log.warning("登录会话超时，清理 acc=%s", s.acc_id)
        _kill_login(s)
        _drop_login(sid)


def start_login(acc_id: str) -> str:
    """起一个 PTY 登录会话，返回 session_id。前端随后用 read_login 轮询输出、write_login 送按键。"""
    assert_safe_id(acc_id, "account_id")
    base = accounts_dir()
    if not base:
        raise DockerManagerError("未配置账号目录（CLAUDE_ACCOUNTS_DIR）")
    base_p = Path(base).expanduser()
    creds_dir = base_p / acc_id
    creds_file = creds_dir / ".credentials.json"
    if creds_file.is_file() and _creds_file_usable(creds_file):
        raise DockerManagerError(f"账号 {acc_id} 已存在凭据")
    from services.claude.registry import get_router
    for sl in get_router().all_slots():
        if sl.id == acc_id and _creds_file_usable(slot_creds_dir(sl) / ".credentials.json"):
            raise DockerManagerError(f"slot {acc_id} 已存在")
    if not is_docker_reachable():
        raise DockerManagerError("docker 不可达，无法登录")

    creds_dir.mkdir(parents=True, exist_ok=True)
    # CLI 登出残留的空壳凭据挪到一边，避免 read_login 把它误判成"凭据已就绪"。
    if creds_file.is_file():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        try:
            creds_file.rename(creds_file.with_name(creds_file.name + ".wiped-" + stamp))
        except Exception:  # noqa: BLE001
            pass
    _chown(creds_dir)
    ws = slot_workspace_dir(acc_id)
    ws.mkdir(parents=True, exist_ok=True)
    _chown(ws)

    cname = _login_container_name(acc_id)
    _docker_rm_f(cname)

    argv = [
        "docker", "run", "-it", "--name", cname,
        "--user", "node", "-w", "/workspace",
        "-e", "HOME=/workspace", "-e", "TERM=xterm-256color",
        "-v", f"{ws}:/workspace:rw",
        "-v", f"{creds_dir}:/workspace/.claude:rw",
        _login_image(),
        "claude", "auth", "login", "--claudeai",
    ]
    master_fd, slave_fd = pty.openpty()
    # 设终端窗口大小（40x120），对齐前端 xterm 默认。
    try:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    except Exception:  # noqa: BLE001
        pass
    try:
        proc = subprocess.Popen(
            argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True, start_new_session=True,
        )
    except Exception as e:  # noqa: BLE001
        os.close(master_fd)
        os.close(slave_fd)
        _docker_rm_f(cname)
        raise DockerManagerError(f"启动登录终端失败：{e}") from e
    os.close(slave_fd)

    sid = secrets.token_hex(12)
    s = _LoginSession(sid, acc_id, creds_dir, cname, proc, master_fd)
    threading.Thread(target=_reader, args=(s,), daemon=True,
                     name=f"claude-login-{sid}").start()
    with _login_mu:
        _login_sessions[sid] = s
    threading.Thread(target=_watchdog, args=(sid,), daemon=True).start()
    return sid


def read_login(sid: str, offset: int = 0) -> Tuple[bytes, int, bool, bool]:
    """从 offset(总字节序号) 起返回新输出。返回 (新字节, 新 offset, 是否退出, 凭据是否已就绪)。"""
    s = _get_login(sid)
    if s is None:
        raise DockerManagerError("登录会话不存在或已过期")
    with s.lock:
        total = s.dropped + len(s.buf)
        if offset < s.dropped:
            offset = s.dropped  # 客户端落后被丢弃的部分，跳到当前窗口起点
        start = offset - s.dropped
        if start < 0:
            start = 0
        data = bytes(s.buf[start:]) if start <= len(s.buf) else b""
        exited = s.exited
    creds_ready = False
    try:
        cf = s.creds_dir / ".credentials.json"
        if cf.is_file() and cf.stat().st_size > 0:
            creds_ready = True
    except Exception:  # noqa: BLE001
        pass
    return data, total, exited, creds_ready


def write_login(sid: str, data: bytes) -> None:
    """把前端按键原样写进 PTY。"""
    s = _get_login(sid)
    if s is None:
        raise DockerManagerError("登录会话不存在或已过期")
    try:
        os.write(s.master_fd, data)
    except OSError as e:
        raise DockerManagerError(f"写入登录终端失败：{e}") from e


def finish_login(sid: str) -> dict:
    """收尾：凭据已写出则收编成 slot + 验活；否则报错。"""
    s = _get_login(sid)
    if s is None:
        raise DockerManagerError("登录会话不存在或已过期")
    creds_file = s.creds_dir / ".credentials.json"
    if not creds_file.is_file() or creds_file.stat().st_size == 0:
        raise DockerManagerError(
            "尚未检测到凭据（.credentials.json 未写出）；请在终端里完成登录后再点完成")
    _kill_login(s)
    _drop_login(s.id)

    # db 源：先把新账号写入 claude_slots（否则 refresh_shared_slots 查库看不到它）。
    from services.claude import db_source
    if db_source.slots_source_is_db():
        try:
            db_source.db_upsert_login_slot(s.acc_id, str(s.creds_dir))
        except Exception as e:  # noqa: BLE001
            log.warning("登录成功但写入 claude_slots 失败 acc=%s: %s", s.acc_id, e)

    from services.claude.registry import refresh_shared_slots, get_router
    refresh_shared_slots()
    slot = None
    for sl in get_router().all_slots():
        if sl.id == s.acc_id:
            slot = sl
            break
    health = "unknown"
    if slot is not None:
        from services.claude import health as health_mod
        r = health_mod.probe_and_update(slot)
        health = "healthy" if r.healthy else "unhealthy"
    log.info("claude 登录成功，新增 slot acc=%s health=%s", s.acc_id, health)
    return {"account_id": s.acc_id, "creds_dir": str(s.creds_dir), "health": health}


def cancel_login(sid: str) -> None:
    """放弃一个登录会话（清容器 + 若无凭据则删空目录）。"""
    s = _get_login(sid)
    if s is None:
        return
    _kill_login(s)
    _drop_login(sid)
    # 没登录成功的空目录清掉，避免残留。
    try:
        cf = s.creds_dir / ".credentials.json"
        if not cf.is_file() or cf.stat().st_size == 0:
            shutil.rmtree(s.creds_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _kill_login(s: _LoginSession) -> None:
    try:
        os.close(s.master_fd)
    except Exception:  # noqa: BLE001
        pass
    try:
        if s.proc is not None:
            s.proc.kill()
    except Exception:  # noqa: BLE001
        pass
    _docker_rm_f(s.cname)
