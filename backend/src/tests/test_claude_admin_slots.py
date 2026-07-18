# -*- coding: utf-8 -*-
"""admin slot 视图与 env 托管 fallback 的服务端保护。"""
import pytest
from fastapi import HTTPException

from controller import claude as controller
from services.claude import registry
from services.claude.slots import Slot, SlotType


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset_for_test()
    yield
    registry.reset_for_test()


def test_slot_view_exposes_priority_and_keys_but_never_secret_values():
    slot = Slot(
        id="fallback-gemini", type=SlotType.API_KEY, priority=100,
        env={"ANTHROPIC_AUTH_TOKEN": "top-secret", "ANTHROPIC_MODEL": "gemini"},
    )
    view = controller._slot_view(slot)
    assert view["priority"] == 100
    assert view["managed"] is True
    assert view["env_keys"] == ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"]
    assert "top-secret" not in repr(view)


@pytest.mark.asyncio
async def test_db_admin_list_adds_priority_and_runtime_fallback_without_secrets(monkeypatch):
    monkeypatch.setattr(controller.db_source, "slots_source_is_db", lambda: True)
    monkeypatch.setattr(controller.db_source, "node_ip", lambda: "10.0.0.1")
    monkeypatch.setattr(controller.db_source, "db_list_slots", lambda: [
        {"id": "subscription-a", "server_ip": "10.0.0.1", "weight": 1.0},
        # 历史 DB 中的保留 id 不得覆盖当前 settings 合成项。
        {"id": "fallback-gemini", "server_ip": "old-node", "weight": 9.0},
    ])
    registry.configure([
        Slot(id="subscription-a"),
        Slot(
            id="fallback-gemini", type=SlotType.API_KEY, priority=100,
            env={"ANTHROPIC_AUTH_TOKEN": "top-secret", "ANTHROPIC_MODEL": "gemini"},
        ),
    ])

    result = await controller.list_slots()
    rows = {row["id"]: row for row in result["slots"]}
    assert rows["subscription-a"]["priority"] == 0
    assert rows["subscription-a"]["managed"] is False
    fallback = rows["fallback-gemini"]
    assert fallback["priority"] == 100 and fallback["managed"] is True
    assert fallback["server_ip"] == "10.0.0.1"
    assert fallback["env_keys"] == ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"]
    assert "top-secret" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("slot_id", ["fallback-gemini", "fallback-glm"])
async def test_managed_fallback_rejects_all_slot_mutations(slot_id):
    calls = [
        lambda: controller.create_slot(controller.CreateSlotIn(slot_id=slot_id)),
        lambda: controller.set_slot_enabled(slot_id),
        lambda: controller.reassign_slot(slot_id, to="10.0.0.2"),
        lambda: controller.upsert_slot(
            slot_id, controller.SlotIn(id=slot_id, type=SlotType.API_KEY, priority=0),
        ),
        lambda: controller.delete_slot(slot_id),
        lambda: controller.login_start(controller.LoginStartIn(account_id=slot_id)),
    ]
    for make_call in calls:
        with pytest.raises(HTTPException) as exc:
            await make_call()
        assert exc.value.status_code == 400
        assert "环境变量托管" in str(exc.value.detail)
