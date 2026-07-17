# -*- coding: utf-8 -*-
"""ChatGPT 订阅上游：一次请求 = 一次 `docker run --rm codex-runner codex exec --json`。

对齐 digital-platform--generator 的 codex_runner：挂载账号池里某个 <acc>/auth.json 提供 ChatGPT
订阅登录态，prompt 走 stdin，stdout 是 JSONL 事件流（item.completed=助手文本，turn.completed=token 用量）。
codex exec 单轮无状态，容器 --rm，故不依赖 codex 自身 session；多轮记忆由调用方把历史拼进 prompt。

配置门控：账号池里没有可用 auth.json → configured()=False，网关不会分流到这里。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from config.settings import settings
from services.chatgpt.result import ChatGptResult

log = logging.getLogger("chatgpt.codex")

_NODE_UID = 1000
_NODE_GID = 1000

# 账号轮询游标（进程级，无需持久化）：把请求摊到多个订阅账号上。
_rr = 0


class CodexError(RuntimeError):
    """codex exec 失败（无可用账号 / 退出非零 / turn.failed）。"""


def _accounts_root() -> Path:
    return Path(settings.CODEX_ACCOUNTS_DIR).expanduser()


def _auth_usable(acc_dir: Path) -> bool:
    """<acc>/auth.json 里是否有可用 ChatGPT 登录态（tokens.access_token 或 OPENAI_API_KEY）。"""
    f = acc_dir / "auth.json"
    try:
        b = f.read_text(encoding="utf-8")
    except Exception:
        return False
    if not b.strip():
        return False
    try:
        m = json.loads(b)
    except Exception:
        return True  # 未知格式，保守视为可用
    if not isinstance(m, dict):
        return True
    tokens = m.get("tokens") or {}
    return bool(tokens.get("access_token") or m.get("OPENAI_API_KEY"))


def list_accounts() -> List[str]:
    """账号池里所有含可用 auth.json 的账号名（排序稳定）。"""
    root = _accounts_root()
    out: List[str] = []
    try:
        for p in sorted(root.iterdir()):
            if p.is_dir() and _auth_usable(p):
                out.append(p.name)
    except FileNotFoundError:
        pass
    return out


def configured() -> bool:
    return bool(list_accounts())


def _pick_account() -> str:
    accs = list_accounts()
    if not accs:
        raise CodexError("codex 账号池为空（没有可用的 auth.json）")
    global _rr
    acc = accs[_rr % len(accs)]
    _rr += 1
    return acc


def _chown(p: Path) -> None:
    try:
        os.chown(p, _NODE_UID, _NODE_GID)
    except Exception:
        pass


def _work_dir(uid: str) -> Path:
    """codex exec 的可写工作目录（host 路径，与 backend 同名 bind，兄弟容器能挂）。"""
    safe = "".join(c for c in (uid or "anon") if c.isalnum() or c in "-_")[:40] or "anon"
    d = Path(settings.CLAUDE_WORKSPACE_ROOT).expanduser() / "codex-work" / safe
    d.mkdir(parents=True, exist_ok=True)
    _chown(d.parent)
    _chown(d)
    return d


class _StreamAcc:
    """把 `codex exec --json` 的 JSONL 逐行累积成 (全文, 用量)。移植自 generator 的 _CodexStreamProcessor。"""

    def __init__(self) -> None:
        self._emitted: dict = {}       # 每个 agent_message item 已吸收的文本长度（增量/全量都不重复）
        self.text_parts: List[str] = []
        self.usage: Optional[dict] = None
        self.error: Optional[str] = None

    def _agent_text(self, item: dict) -> None:
        iid = str(item.get("id") or "_")
        text = item.get("text")
        if not isinstance(text, str) or not text:
            return
        sent = self._emitted.get(iid, 0)
        if len(text) <= sent:
            return
        self.text_parts.append(text[sent:])
        self._emitted[iid] = len(text)

    def feed(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        try:
            obj = json.loads(raw)
        except Exception:
            return
        t = obj.get("type") or ""
        if t in ("item.started", "item.updated", "item.completed", "item.delta"):
            item = obj.get("item") or obj.get("delta") or {}
            itype = item.get("type") or item.get("item_type") or ""
            if itype in ("agent_message", "assistant_message", "message"):
                self._agent_text(item)
            return
        if t in ("turn.completed", "thread.completed"):
            u = obj.get("usage") or (obj.get("turn") or {}).get("usage") or {}
            if u:
                self.usage = u
            return
        if t == "turn.failed":
            err = obj.get("error") or {}
            self.error = str(err.get("message") or err or "codex turn failed")
            return
        if t == "error":
            self.error = str(obj.get("message") or "codex error")

    def result(self, model: str, acc: str) -> ChatGptResult:
        inp = int((self.usage or {}).get("input_tokens") or 0)
        cached = int((self.usage or {}).get("cached_input_tokens")
                     or (self.usage or {}).get("cache_read_input_tokens") or 0)
        out = int((self.usage or {}).get("output_tokens") or 0)
        reasoning = int((self.usage or {}).get("reasoning_output_tokens") or 0)
        return ChatGptResult(
            model=model,
            text="".join(self.text_parts),
            prompt_tokens=max(0, inp - cached),   # 与 Anthropic 口径对齐：input 不含缓存读
            completion_tokens=out + reasoning,
            cache_read_tokens=cached,
            cache_write_tokens=0,
            slot_id=f"codex:{acc}",
            provider="codex",
        )


def _build_argv(acc_dir: Path, workdir: Path, model: str) -> Tuple[List[str], str]:
    import secrets
    cname = f"substantia-codex-{secrets.token_hex(4)}"
    argv = [
        "docker", "run", "--rm", "-i", "--name", cname,
        "--user", f"{_NODE_UID}:{_NODE_GID}",
        "--add-host=host.docker.internal:host-gateway",
        "-e", f"CODEX_HOME={settings.CODEX_HOME_IN_CONTAINER}",
        "-e", f"HOME={workdir}",
        "-e", "TERM=dumb",
        "--memory", settings.CODEX_CONTAINER_MEMORY,
        "--cpus", str(settings.CODEX_CONTAINER_CPUS),
        "-v", f"{workdir}:/workspace:rw",
        "-v", f"{acc_dir}:{settings.CODEX_HOME_IN_CONTAINER}:rw",
        "-w", "/workspace",
        settings.CODEX_IMAGE, "codex", "exec", "--json", settings.CODEX_YOLO_FLAG,
    ]
    if model:
        argv += ["-m", model]
    return argv, cname


def run_codex(uid: str, prompt: str, model: str) -> ChatGptResult:
    """在 codex-runner 容器里跑一次 codex exec，返回归一化结果。失败抛 CodexError。"""
    acc = _pick_account()
    acc_dir = _accounts_root() / acc
    workdir = _work_dir(uid)
    argv, cname = _build_argv(acc_dir, workdir, model)

    log.info("codex exec acc=%s model=%s uid=%s", acc, model, uid)
    try:
        proc = subprocess.run(
            argv, input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=settings.CODEX_EXEC_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "rm", "-f", cname],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise CodexError(f"codex exec 超时（{settings.CODEX_EXEC_TIMEOUT}s，账号 {acc}）") from e

    acc_stream = _StreamAcc()
    out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    for line in out.splitlines():
        acc_stream.feed(line)

    if acc_stream.error:
        raise CodexError(f"codex 生成失败：{acc_stream.error}（账号 {acc}）")
    if proc.returncode != 0:
        tail = "\n".join(out.splitlines()[-8:])[:800]
        raise CodexError(f"codex exec 退出码 {proc.returncode}（账号 {acc}）：{tail}")

    res = acc_stream.result(model, acc)
    if not res.text.strip():
        raise CodexError(f"codex 返回空回复（账号 {acc}）")
    return res
