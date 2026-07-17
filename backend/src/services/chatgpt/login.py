# -*- coding: utf-8 -*-
"""Codex（ChatGPT 订阅）网页交互式登录：admin 后台网页终端直连服务器上的 codex 登录容器。

对标 services/claude/login.py，换成 codex 的 device-auth：`codex login --device-auth` 打印
URL + 一次性 code，用户在任意设备打开授权，CLI 把登录态写进挂载的 CODEX_HOME/auth.json。
凭据落地到 <CODEX_ACCOUNTS_DIR>/<acc>/auth.json —— 就是 services/chatgpt/codex.py 的账号池目录，无缝纳管。

传输用 PTY + HTTP 轮询（与 claude 登录同架子）；不落任何凭据日志。
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import pty
import re
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

log = logging.getLogger("chatgpt.codex_login")

_LOGIN_TTL = 20 * 60
_MAX_BUF = 256 * 1024
_NODE_UID = 1000
_NODE_GID = 1000
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class CodexLoginError(RuntimeError):
    """登录编排错误（controller 转 400）。"""


def _assert_safe(acc: str) -> None:
    if not acc or not _SAFE_ID_RE.match(acc):
        raise CodexLoginError(f"非法账号名：{acc!r}（仅字母数字 _ - ，≤64）")


def _accounts_root() -> Path:
    return Path(settings.CODEX_ACCOUNTS_DIR).expanduser()


def _auth_usable(f: Path) -> bool:
    try:
        b = f.read_text(encoding="utf-8")
    except Exception:
        return False
    if not b.strip():
        return False
    try:
        m = json.loads(b)
    except Exception:
        return True
    if not isinstance(m, dict):
        return True
    tokens = m.get("tokens") or {}
    return bool(tokens.get("access_token") or m.get("OPENAI_API_KEY"))


def _next_acc_id() -> str:
    """扫账号池现有 accN，取 max+1。"""
    root = _accounts_root()
    mx = 0
    try:
        for p in root.iterdir():
            if p.is_dir() and p.name.startswith("acc") and p.name[3:].isdigit():
                mx = max(mx, int(p.name[3:]))
    except FileNotFoundError:
        pass
    return f"acc{mx + 1}"


class _Session:
    def __init__(self, sid: str, acc: str, creds_dir: Path, cname: str,
                 proc: subprocess.Popen, master_fd: int) -> None:
        self.id = sid
        self.acc = acc
        self.creds_dir = creds_dir
        self.cname = cname
        self.proc = proc
        self.master_fd = master_fd
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.dropped = 0
        self.exited = False
        self.created = time.time()


_mu = threading.Lock()
_sessions: Dict[str, _Session] = {}


def _get(sid: str) -> Optional[_Session]:
    with _mu:
        return _sessions.get(sid)


def _drop(sid: str) -> None:
    with _mu:
        _sessions.pop(sid, None)


def _chown(p: Path) -> None:
    try:
        os.chown(p, _NODE_UID, _NODE_GID)
    except Exception:
        pass


def _docker_rm_f(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20)
    except Exception:
        pass


def _reader(s: _Session) -> None:
    while True:
        try:
            chunk = os.read(s.master_fd, 4096)
        except OSError:
            chunk = b""
        if chunk:
            with s.lock:
                s.buf += chunk
                if len(s.buf) > _MAX_BUF:
                    cut = len(s.buf) - _MAX_BUF
                    del s.buf[:cut]
                    s.dropped += cut
        else:
            with s.lock:
                s.exited = True
            return


def _watchdog(sid: str) -> None:
    time.sleep(_LOGIN_TTL)
    s = _get(sid)
    if s is not None:
        log.warning("codex 登录会话超时，清理 acc=%s", s.acc)
        _kill(s)
        _drop(sid)


def _is_docker_reachable() -> bool:
    try:
        r = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def start_login(account_id: str = "") -> str:
    """起一个 PTY 登录会话，返回 session_id。account_id 留空则自动分配 accN。"""
    acc = (account_id or "").strip() or _next_acc_id()
    _assert_safe(acc)

    creds_dir = _accounts_root() / acc
    cf = creds_dir / "auth.json"
    if cf.is_file() and _auth_usable(cf):
        raise CodexLoginError(f"账号 {acc} 已存在可用凭据（重登请换个编号或先删）")
    if not _is_docker_reachable():
        raise CodexLoginError("docker 不可达，无法登录")

    creds_dir.mkdir(parents=True, exist_ok=True)
    if cf.is_file():  # 空壳/坏凭据挪走，避免误判"已就绪"
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        try:
            cf.rename(cf.with_name(cf.name + ".wiped-" + stamp))
        except Exception:
            pass
    _chown(creds_dir)

    cname = f"substantia-codex-login-{acc}"
    _docker_rm_f(cname)
    argv = [
        "docker", "run", "-it", "--name", cname,
        "--user", f"{_NODE_UID}:{_NODE_GID}",
        "--add-host=host.docker.internal:host-gateway",
        "-w", "/workspace",
        "-e", f"CODEX_HOME={settings.CODEX_HOME_IN_CONTAINER}",
        "-e", "TERM=xterm-256color",
        "-v", f"{creds_dir}:{settings.CODEX_HOME_IN_CONTAINER}:rw",
        settings.CODEX_IMAGE,
        "codex", "login", "--device-auth",
    ]
    master_fd, slave_fd = pty.openpty()
    try:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True, start_new_session=True,
        )
    except Exception as e:
        os.close(master_fd)
        os.close(slave_fd)
        _docker_rm_f(cname)
        raise CodexLoginError(f"启动登录终端失败：{e}") from e
    os.close(slave_fd)

    sid = secrets.token_hex(12)
    s = _Session(sid, acc, creds_dir, cname, proc, master_fd)
    threading.Thread(target=_reader, args=(s,), daemon=True,
                     name=f"codex-login-{sid}").start()
    with _mu:
        _sessions[sid] = s
    threading.Thread(target=_watchdog, args=(sid,), daemon=True).start()
    return sid


def read_login(sid: str, offset: int = 0) -> Tuple[bytes, int, bool, bool]:
    s = _get(sid)
    if s is None:
        raise CodexLoginError("登录会话不存在或已过期")
    with s.lock:
        total = s.dropped + len(s.buf)
        if offset < s.dropped:
            offset = s.dropped
        start = offset - s.dropped
        if start < 0:
            start = 0
        data = bytes(s.buf[start:]) if start <= len(s.buf) else b""
        exited = s.exited
    creds_ready = False
    try:
        cf = s.creds_dir / "auth.json"
        if cf.is_file() and cf.stat().st_size > 0:
            creds_ready = True
    except Exception:
        pass
    return data, total, exited, creds_ready


def write_login(sid: str, data: bytes) -> None:
    s = _get(sid)
    if s is None:
        raise CodexLoginError("登录会话不存在或已过期")
    try:
        os.write(s.master_fd, data)
    except OSError as e:
        raise CodexLoginError(f"写入登录终端失败：{e}") from e


def finish_login(sid: str) -> dict:
    """凭据已写出则收尾：杀容器、对齐属主。codex 账号是请求时扫描的池子，无需注册 slot。"""
    s = _get(sid)
    if s is None:
        raise CodexLoginError("登录会话不存在或已过期")
    cf = s.creds_dir / "auth.json"
    if not cf.is_file() or cf.stat().st_size == 0:
        raise CodexLoginError("尚未检测到凭据（auth.json 未写出）；请在手机/电脑完成设备码授权后再点完成")
    _kill(s)
    _drop(s.id)
    _chown(s.creds_dir)
    try:
        for p in s.creds_dir.iterdir():
            _chown(p)
    except Exception:
        pass
    log.info("codex 登录成功，新增账号 acc=%s", s.acc)
    return {"account_id": s.acc, "creds_dir": str(s.creds_dir)}


def cancel_login(sid: str) -> None:
    s = _get(sid)
    if s is None:
        return
    _kill(s)
    _drop(sid)
    try:
        cf = s.creds_dir / "auth.json"
        if not cf.is_file() or cf.stat().st_size == 0:
            shutil.rmtree(s.creds_dir, ignore_errors=True)
    except Exception:
        pass


def _kill(s: _Session) -> None:
    try:
        os.close(s.master_fd)
    except Exception:
        pass
    try:
        if s.proc is not None:
            s.proc.kill()
    except Exception:
        pass
    _docker_rm_f(s.cname)
