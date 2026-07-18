# -*- coding: utf-8 -*-
"""slot registry 的 env fallback 合成与热刷新回归测试（不需要 Docker）。"""
import pytest

from services.claude import registry
from services.claude.slots import Slot, SlotHealth, SlotType


@pytest.fixture(autouse=True)
def _clean_registry_and_fallback_settings(monkeypatch):
    registry.reset_for_test()
    monkeypatch.setattr(registry.settings, "CLAUDE_SLOTS_SOURCE", "dir")
    monkeypatch.setattr(registry.settings, "CLAUDE_ACCOUNTS_DIR", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_SHARED_ACCOUNTS_DIR", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_BASE_URL", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_AUTH_TOKEN", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_MODEL", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_BASE_URL", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_AUTH_TOKEN", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_MODEL", "")
    yield
    registry.reset_for_test()


def _enable_fallbacks(monkeypatch):
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_BASE_URL", "https://www.moxing.pro")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_AUTH_TOKEN", "moxing-secret")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_MODEL", "kimi-k3")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_AUTH_TOKEN", "gemini-secret")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_MODEL", "gemini-3.5-flash")


def test_fallback_slots_require_complete_config_and_have_fixed_priority(monkeypatch):
    # 三件套不齐：不合成。
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_BASE_URL", "https://www.moxing.pro")
    assert registry.fallback_slots_from_settings() == []

    _enable_fallbacks(monkeypatch)
    slots = registry.fallback_slots_from_settings()
    assert [(s.id, s.type, s.priority) for s in slots] == [
        ("fallback-moxing", SlotType.API_KEY, 100),
        ("fallback-gemini", SlotType.API_KEY, 200),
    ]
    moxing, gemini = slots
    assert moxing.env == {
        "ANTHROPIC_BASE_URL": "https://www.moxing.pro",
        "ANTHROPIC_AUTH_TOKEN": "moxing-secret",
        "ANTHROPIC_MODEL": "kimi-k3",
    }
    assert gemini.env == {
        "ANTHROPIC_BASE_URL": "http://litellm:4000",
        "ANTHROPIC_AUTH_TOKEN": "gemini-secret",
        "ANTHROPIC_MODEL": "gemini-3.5-flash",
    }


def test_fallback_chain_skips_unconfigured_tiers(monkeypatch):
    # 只配 gemini：moxing 档被跳过，链里只剩 gemini。
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_AUTH_TOKEN", "gemini-secret")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_MODEL", "gemini-3.5-flash")
    slots = registry.fallback_slots_from_settings()
    assert [(s.id, s.priority) for s in slots] == [("fallback-gemini", 200)]


def test_shared_accounts_and_fallback_slots_are_merged(monkeypatch, tmp_path):
    account = tmp_path / "subscription-a"
    account.mkdir()
    (account / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(registry.settings, "CLAUDE_ACCOUNTS_DIR", str(tmp_path))
    _enable_fallbacks(monkeypatch)

    slots = registry.load_slots_by_source()
    assert slots is not None
    assert [(s.id, s.priority) for s in slots] == [
        ("subscription-a", 0),
        ("fallback-moxing", 100),
        ("fallback-gemini", 200),
    ]
    assert slots[0].creds_dir == str(account)


def test_reserved_fallback_ids_never_revive_stale_persisted_credentials(monkeypatch):
    stale = Slot(
        id="fallback-moxing", type=SlotType.API_KEY, priority=0,
        env={"ANTHROPIC_AUTH_TOKEN": "stale-secret"},
    )
    assert registry.merge_fallback_slots([stale]) == []

    _enable_fallbacks(monkeypatch)
    merged = registry.merge_fallback_slots([stale])
    moxing = next(s for s in merged if s.id == "fallback-moxing")
    assert moxing.priority == 100
    assert moxing.env["ANTHROPIC_AUTH_TOKEN"] == "moxing-secret"


def test_refresh_detects_same_id_config_change_and_preserves_health(monkeypatch, tmp_path):
    account = tmp_path / "subscription-a"
    account.mkdir()
    (account / ".credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(registry.settings, "CLAUDE_ACCOUNTS_DIR", str(tmp_path))
    _enable_fallbacks(monkeypatch)

    original = registry.get_router()
    original.mark_unhealthy("subscription-a", cooldown_seconds=60, now=10)
    before_fingerprint = registry.slots_fingerprint(original.all_slots())

    # ID 集完全不变，仅 token 变化；旧实现只比 IDs，无法发现这一变化。
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_AUTH_TOKEN", "rotated-secret")
    refreshed = registry.refresh_shared_slots()
    assert refreshed is not None and refreshed is not original
    assert {s.id for s in refreshed.all_slots()} == {s.id for s in original.all_slots()}
    assert refreshed.get("fallback-moxing").env["ANTHROPIC_AUTH_TOKEN"] == "rotated-secret"
    assert refreshed.get("subscription-a").health == SlotHealth.UNHEALTHY
    assert registry.slots_fingerprint(refreshed.all_slots()) != before_fingerprint


def test_refresh_removes_fallback_when_env_is_withdrawn(monkeypatch):
    monkeypatch.setattr(registry.store, "load", lambda: [])
    _enable_fallbacks(monkeypatch)
    original = registry.get_router()
    assert {s.id for s in original.all_slots()} == {"fallback-moxing", "fallback-gemini"}

    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_MOXING_AUTH_TOKEN", "")
    monkeypatch.setattr(registry.settings, "CLAUDE_FALLBACK_GEMINI_AUTH_TOKEN", "")
    refreshed = registry.refresh_shared_slots()
    assert refreshed is not None and refreshed is not original
    assert refreshed.all_slots() == []


def test_fingerprint_ignores_runtime_health_but_tracks_priority_and_env():
    base = Slot(id="x", priority=0, env={"ANTHROPIC_MODEL": "one"})
    same_business = Slot(
        id="x", priority=0, env={"ANTHROPIC_MODEL": "one"},
        health=SlotHealth.UNHEALTHY, cooldown_until=123,
    )
    assert registry.slots_fingerprint([base]) == registry.slots_fingerprint([same_business])
    assert registry.slots_fingerprint([base]) != registry.slots_fingerprint([
        Slot(id="x", priority=1, env={"ANTHROPIC_MODEL": "one"}),
    ])
    assert registry.slots_fingerprint([base]) != registry.slots_fingerprint([
        Slot(id="x", priority=0, env={"ANTHROPIC_MODEL": "two"}),
    ])
