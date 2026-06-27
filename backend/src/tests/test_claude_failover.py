# -*- coding: utf-8 -*-
"""
故障转移 + 鉴权检测 + slot 持久化测试（不需要 docker）。
通过 monkeypatch `_exec_in_slot` 模拟容器执行，验证 exec_claude 的故障转移逻辑。
"""
import pytest

from services.claude import docker_manager as dm
from services.claude import registry, store
from services.claude.slots import Slot, SlotHealth, SlotType


@pytest.fixture(autouse=True)
def _reset():
    registry.reset_for_test()
    yield
    registry.reset_for_test()


def _sub(slot_id, **kw):
    return Slot(id=slot_id, type=SlotType.SUBSCRIPTION, image=f"repo:{slot_id}", **kw)


def test_looks_like_auth_failure():
    assert dm.looks_like_auth_failure("API Error: 401 Invalid authentication credentials")
    assert dm.looks_like_auth_failure("OAuth token expired")
    assert dm.looks_like_auth_failure("Unauthorized")
    assert not dm.looks_like_auth_failure("hello world, here is your answer")
    assert not dm.looks_like_auth_failure("")


def test_store_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(store.settings, "CLAUDE_SLOTS_FILE", str(tmp_path / "slots.json"))
    slots = [_sub("sub-a", weight=2), Slot(id="glm-1", type=SlotType.API_KEY, env={"ANTHROPIC_MODEL": "glm-4"})]
    store.save(slots)
    loaded = store.load()
    assert sorted(s.id for s in loaded) == ["glm-1", "sub-a"]
    a = next(s for s in loaded if s.id == "sub-a")
    assert a.weight == 2 and a.type == SlotType.SUBSCRIPTION
    # 运行时健康字段不入文件
    text = (tmp_path / "slots.json").read_text()
    assert "cooldown_until" not in text and "health" not in text


def test_exec_failover_marks_unhealthy_and_reroutes(monkeypatch):
    r = registry.configure([_sub("sub-a"), _sub("sub-b")])
    uid = "u-tester"
    first = r.route(uid).id                     # 用户首选的 slot
    other = "sub-b" if first == "sub-a" else "sub-a"

    calls = []

    def fake_exec(slot, user_id, prompt):
        calls.append(slot.id)
        if slot.id == first:
            return dm.ClaudeExecResult(slot.id, 1, "API Error: 401 Invalid authentication", auth_failed=True)
        return dm.ClaudeExecResult(slot.id, 0, "pong")

    monkeypatch.setattr(dm, "_exec_in_slot", fake_exec)

    res = dm.exec_claude(uid, "hi")
    assert res.ok and res.slot_id == other         # 故障转移到健康 slot
    assert res.attempts == 2
    assert calls == [first, other]                  # 先试首选、失败后转移
    assert r.get(first).health == SlotHealth.UNHEALTHY   # 坏 slot 被标记
    assert not r.get(first).is_routable()                # 冷却期内不再被路由


def test_exec_non_auth_failure_no_retry(monkeypatch):
    r = registry.configure([_sub("sub-a"), _sub("sub-b")])
    calls = []

    def fake_exec(slot, user_id, prompt):
        calls.append(slot.id)
        return dm.ClaudeExecResult(slot.id, 2, "your prompt errored", auth_failed=False)

    monkeypatch.setattr(dm, "_exec_in_slot", fake_exec)
    res = dm.exec_claude("u-x", "hi")
    assert not res.ok and res.attempts == 1         # 非鉴权失败不重试
    assert len(calls) == 1
    # slot 未被标记不健康（不是凭据问题）
    assert all(s.health == SlotHealth.HEALTHY for s in r.all_slots())


def test_exec_all_slots_auth_fail_returns_last(monkeypatch):
    registry.configure([_sub("sub-a"), _sub("sub-b")])

    def fake_exec(slot, user_id, prompt):
        return dm.ClaudeExecResult(slot.id, 1, "401 unauthorized", auth_failed=True)

    monkeypatch.setattr(dm, "_exec_in_slot", fake_exec)
    # CLAUDE_EXEC_MAX_ATTEMPTS 默认 3，但只有 2 个 slot：第 3 次 route 会因全不健康抛 NoRoutableSlotError
    from services.claude.router import NoRoutableSlotError
    with pytest.raises(NoRoutableSlotError):
        dm.exec_claude("u-x", "hi")
