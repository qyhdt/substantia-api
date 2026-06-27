# -*- coding: utf-8 -*-
"""
slot 配置持久化（文件存储）。admin CRUD 写这里，重启后 registry 从这里加载。

存储位置：settings.CLAUDE_SLOTS_FILE，留空则 <CLAUDE_WORKSPACE_ROOT>/slots.json。
只持久化业务配置（id/type/enabled/weight/creds_dir/image/env）；运行时健康态不入文件。
DB 化（provider_slots 表）可后续替换本模块，接口不变。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import List

from config.settings import settings
from services.claude.slots import Slot

_lock = threading.Lock()

# 不持久化运行时健康字段
_RUNTIME_FIELDS = {"health", "cooldown_until"}


def store_path() -> Path:
    p = (settings.CLAUDE_SLOTS_FILE or "").strip()
    if p:
        return Path(p).expanduser()
    return Path(settings.CLAUDE_WORKSPACE_ROOT).expanduser() / "slots.json"


def load() -> List[Slot]:
    """读持久化 slot；文件不存在则回落 CLAUDE_SLOTS_JSON（初始 seed），再无则空。"""
    path = store_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return [Slot.model_validate(x) for x in data]
    except Exception:
        # 文件坏了不致命：回落到 env seed
        pass
    raw = (settings.CLAUDE_SLOTS_JSON or "").strip()
    if raw:
        try:
            return [Slot.model_validate(x) for x in json.loads(raw)]
        except Exception:
            return []
    return []


def save(slots: List[Slot]) -> None:
    """原子写入 slot 列表（剔除运行时健康字段）。"""
    path = store_path()
    payload = [s.model_dump(mode="json", exclude=_RUNTIME_FIELDS) for s in slots]
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
