# -*- coding: utf-8 -*-
"""
docker_manager 纯逻辑测试（不需要 docker 守护进程）：命名 / 镜像解析 / 环境构建 / 卷映射 / 安全 id。
docker SDK 在 _client() 内惰性 import，所以这些不碰容器的函数可独立测。

跑法（backend/src 下）：python -m pytest tests/test_claude_docker_manager.py -q
"""
import pytest

from services.claude import docker_manager as dm
from services.claude.slots import Slot, SlotType


def _sub(slot_id="sub-a", **kw):
    return Slot(id=slot_id, type=SlotType.SUBSCRIPTION, **kw)


def _api(slot_id="glm-1", **kw):
    return Slot(id=slot_id, type=SlotType.API_KEY, **kw)


def test_assert_safe_id():
    dm.assert_safe_id("sub-a")
    dm.assert_safe_id("Slot_1")
    for bad in ("", "a b", "a/b", "a;rm", "x" * 65, "../etc"):
        with pytest.raises(dm.DockerManagerError):
            dm.assert_safe_id(bad)


def test_container_name():
    assert dm.container_name_for_slot("sub-a") == "claude-slot-sub-a"
    with pytest.raises(dm.DockerManagerError):
        dm.container_name_for_slot("bad id")


def test_resolve_image():
    # 订阅必须有 image
    with pytest.raises(dm.DockerManagerError):
        dm._resolve_image(_sub(image=None))
    assert dm._resolve_image(_sub(image="repo:claude-loggedin-sub-a")) == "repo:claude-loggedin-sub-a"
    # api_key 回落 base 镜像
    assert dm._resolve_image(_api(image=None)) == dm.settings.CLAUDE_BASE_IMAGE
    assert dm._resolve_image(_api(image="custom:tag")) == "custom:tag"


def test_build_env_subscription_drops_anthropic():
    slot = _sub(env={
        "ANTHROPIC_BASE_URL": "https://evil",      # 必须被丢弃（订阅不可被端点覆盖）
        "ANTHROPIC_AUTH_TOKEN": "sk-x",            # 丢弃
        "CLAUDE_CODE_EFFORT_LEVEL": "max",         # 保留（无害行为开关）
    })
    env = dm._build_env(slot)
    assert env["HOME"] == "/workspace"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"


def test_build_env_api_key_injects_all():
    slot = _api(env={
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "sk-x",
        "ANTHROPIC_MODEL": "deepseek-v4",
    })
    env = dm._build_env(slot)
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-x"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4"
    assert env["HOME"] == "/workspace"


def test_build_volumes():
    # 订阅：挂 workspace + 独占凭据目录到 /workspace/.claude
    sub = _sub(creds_dir="/data/creds/sub-a")
    vols = dm._build_volumes(sub)
    binds = {v["bind"] for v in vols.values()}
    assert "/workspace" in binds
    assert "/workspace/.claude" in binds
    assert "/data/creds/sub-a" in vols and vols["/data/creds/sub-a"]["bind"] == "/workspace/.claude"
    # api_key：只挂 workspace，不挂凭据
    vols2 = dm._build_volumes(_api())
    binds2 = {v["bind"] for v in vols2.values()}
    assert binds2 == {"/workspace"}


def test_path_helpers(monkeypatch):
    monkeypatch.setattr(dm.settings, "CLAUDE_WORKSPACE_ROOT", "/var/lib/substantia/claude")
    assert str(dm.slot_workspace_dir("sub-a")) == "/var/lib/substantia/claude/sub-a"
    assert str(dm.user_workdir("sub-a", "u1")) == "/var/lib/substantia/claude/sub-a/users/u1"
    # 默认凭据目录在 workspace 下
    assert str(dm.slot_creds_dir(_sub(creds_dir=None))).endswith("/sub-a/.claude-creds")
    # 显式 creds_dir 优先
    assert str(dm.slot_creds_dir(_sub(creds_dir="/x/y"))) == "/x/y"
