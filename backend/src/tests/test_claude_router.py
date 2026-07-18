# -*- coding: utf-8 -*-
"""
HRW 路由性质测试：
1. sticky / 确定性：同一 user 多次路由结果不变。
2. 均匀分布（等权）。
3. 加权分布（按 weight 比例）。
4. 删除 slot：仅被删 slot 的用户被搬动（~1/N），其余用户原样不动。
5. 新增 slot：仅 ~1/(N+1) 用户迁到新 slot，其余不动。
6. 健康态：unhealthy 持续剔除，直到探针显式 mark_healthy 后回流。

跑法（在 backend/src 下）：  python -m pytest tests/test_claude_router.py -q
"""
import pytest

from services.claude import router as router_mod
from services.claude.router import NoRoutableSlotError, SlotRouter
from services.claude.slots import Slot, SlotHealth


def _sub(slot_id: str, weight: float = 1.0) -> Slot:
    return Slot(id=slot_id, weight=weight)


def _users(n: int) -> list[str]:
    return [f"user-{i}" for i in range(n)]


def _assign(router: SlotRouter, users, now=None) -> dict[str, str]:
    return {u: router.route(u, now=now).id for u in users}


@pytest.fixture(autouse=True)
def _hrw_policy(monkeypatch):
    """本文件的历史性质测试针对 HRW；RR 行为在专门用例里显式开启。"""
    monkeypatch.setattr(router_mod.settings, "CLAUDE_ROUTE_POLICY", "hrw")


def test_sticky_deterministic():
    r = SlotRouter([_sub("a"), _sub("b"), _sub("c")])
    for u in _users(200):
        first = r.route(u).id
        for _ in range(5):
            assert r.route(u).id == first


def test_even_distribution_equal_weight():
    slots = [_sub(x) for x in ("a", "b", "c", "d", "e")]
    r = SlotRouter(slots)
    assign = _assign(r, _users(10000))
    counts = {s.id: 0 for s in slots}
    for sid in assign.values():
        counts[sid] += 1
    expected = 10000 / len(slots)
    for sid, c in counts.items():
        # 等权 5 slot，每个约 2000，允许 ±15% 抖动
        assert abs(c - expected) < expected * 0.15, (sid, c, counts)


def test_weighted_distribution():
    # a 权重 3，b/c 权重 1 → a 约占 3/5
    r = SlotRouter([_sub("a", weight=3.0), _sub("b", weight=1.0), _sub("c", weight=1.0)])
    assign = _assign(r, _users(12000))
    counts = {"a": 0, "b": 0, "c": 0}
    for sid in assign.values():
        counts[sid] += 1
    frac_a = counts["a"] / 12000
    assert 0.55 < frac_a < 0.65, counts  # 期望 0.6


def test_remove_slot_only_moves_its_own_users():
    slots = [_sub(x) for x in ("a", "b", "c", "d", "e")]
    r = SlotRouter(slots)
    users = _users(10000)
    before = _assign(r, users)

    r.remove("c")
    after = _assign(r, users)

    moved = [u for u in users if before[u] != after[u]]
    # 只有原本在 c 上的用户被搬动；其余完全不动
    assert all(before[u] == "c" for u in moved)
    assert {u for u in users if before[u] == "c"} == set(moved)
    # 搬动比例 ≈ 1/N
    assert 0.15 < len(moved) / len(users) < 0.25, len(moved)
    # 被搬动的用户散到其余 4 个 slot（不是全堆一个）
    redest = {after[u] for u in moved}
    assert redest == {"a", "b", "d", "e"}


def test_add_slot_minimal_reshuffle():
    r = SlotRouter([_sub(x) for x in ("a", "b", "c", "d")])
    users = _users(10000)
    before = _assign(r, users)

    r.upsert(_sub("e"))
    after = _assign(r, users)

    moved = [u for u in users if before[u] != after[u]]
    # 新增后只有迁到新 slot 的用户变化，且都迁到了 e；老 slot 之间不互相搬
    assert all(after[u] == "e" for u in moved)
    # ~1/(N+1) ≈ 1/5 = 0.2
    assert 0.15 < len(moved) / len(users) < 0.25, len(moved)


def test_unhealthy_excluded_until_probe_marks_healthy():
    r = SlotRouter([_sub("a"), _sub("b"), _sub("c")])
    users = _users(3000)
    base = _assign(r, users)
    on_a = [u for u in users if base[u] == "a"]
    assert on_a  # 确实有人落在 a

    # a 标 unhealthy + 冷却到 t=1000
    r.mark_unhealthy("a", cooldown_seconds=600, now=400.0)  # cooldown_until=1000
    during = _assign(r, users, now=500.0)
    assert all(during[u] != "a" for u in users)            # 冷却期内无人命中 a
    # 未受影响的用户（原本不在 a）保持不动
    for u in users:
        if base[u] != "a":
            assert during[u] == base[u]

    # 冷却已过仍不可路由；只有健康探针显式 mark_healthy 才恢复。
    after = _assign(r, users, now=2000.0)
    assert after == during
    assert r.get("a").health == SlotHealth.UNHEALTHY

    r.mark_healthy("a")
    recovered = _assign(r, users, now=2001.0)
    assert recovered == base


def test_no_routable_slot_raises():
    r = SlotRouter([_sub("a")])
    r.mark_unhealthy("a", cooldown_seconds=600, now=0.0)
    with pytest.raises(NoRoutableSlotError):
        r.route("user-1", now=1.0)


def test_strict_priority_preempts_weight_and_falls_through():
    primary = Slot(id="subscription", priority=0, weight=0.01)
    moxing = Slot(id="fallback-moxing", priority=100, weight=1000)
    gemini = Slot(id="fallback-gemini", priority=200, weight=1000)
    r = SlotRouter([gemini, moxing, primary])

    # 不同 priority 绝不混权重：只要订阅可用，再大的 fallback weight 也抢不到流量。
    assert {r.route(u).id for u in _users(1000)} == {"subscription"}

    r.mark_unhealthy("subscription", cooldown_seconds=1, now=0)
    assert {r.route(u, now=9999).id for u in _users(100)} == {"fallback-moxing"}
    r.mark_unhealthy("fallback-moxing", cooldown_seconds=1, now=0)
    assert r.route("user-1", now=9999).id == "fallback-gemini"


def test_route_candidates_are_priority_ordered_and_excludable():
    r = SlotRouter([
        Slot(id="sub-2", priority=0),
        Slot(id="fallback-gemini", priority=200),
        Slot(id="sub-1", priority=0),
        Slot(id="fallback-moxing", priority=100),
    ])
    candidates = r.route_candidates("sticky-user")
    assert {s.id for s in candidates[:2]} == {"sub-1", "sub-2"}
    assert [s.id for s in candidates[2:]] == ["fallback-moxing", "fallback-gemini"]
    assert len({s.id for s in candidates}) == len(candidates)

    remaining = r.route_candidates(
        "sticky-user", exclude_slot_ids={"sub-1", "sub-2", "fallback-moxing"},
    )
    assert [s.id for s in remaining] == ["fallback-gemini"]


def test_round_robin_is_preserved_within_priority(monkeypatch):
    monkeypatch.setattr(router_mod.settings, "CLAUDE_ROUTE_POLICY", "round_robin")
    r = SlotRouter([
        Slot(id="acc10", priority=0),
        Slot(id="fallback-moxing", priority=100),
        Slot(id="acc2", priority=0),
        Slot(id="acc1", priority=0),
    ])
    assert [r.route("ignored").id for _ in range(6)] == [
        "acc1", "acc2", "acc10", "acc1", "acc2", "acc10",
    ]

    for sid in ("acc1", "acc2", "acc10"):
        r.mark_unhealthy(sid)
    assert r.route("ignored").id == "fallback-moxing"
