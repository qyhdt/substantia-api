# -*- coding: utf-8 -*-
"""
HRW 路由性质测试：
1. sticky / 确定性：同一 user 多次路由结果不变。
2. 均匀分布（等权）。
3. 加权分布（按 weight 比例）。
4. 删除 slot：仅被删 slot 的用户被搬动（~1/N），其余用户原样不动。
5. 新增 slot：仅 ~1/(N+1) 用户迁到新 slot，其余不动。
6. 健康态：unhealthy 在冷却期被剔除并改路由；冷却过/复活后回流。

跑法（在 backend/src 下）：  python -m pytest tests/test_claude_router.py -q
"""
import pytest

from services.claude.router import NoRoutableSlotError, SlotRouter
from services.claude.slots import Slot, SlotHealth


def _sub(slot_id: str, weight: float = 1.0) -> Slot:
    return Slot(id=slot_id, weight=weight)


def _users(n: int) -> list[str]:
    return [f"user-{i}" for i in range(n)]


def _assign(router: SlotRouter, users, now=None) -> dict[str, str]:
    return {u: router.route(u, now=now).id for u in users}


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


def test_unhealthy_excluded_and_recovers():
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

    # 冷却已过 → 乐观放行，a 重新可路由，其用户回流（HRW 确定性）
    after = _assign(r, users, now=2000.0)
    assert after == base


def test_no_routable_slot_raises():
    r = SlotRouter([_sub("a")])
    r.mark_unhealthy("a", cooldown_seconds=600, now=0.0)
    with pytest.raises(NoRoutableSlotError):
        r.route("user-1", now=1.0)
