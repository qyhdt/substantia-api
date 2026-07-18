# -*- coding: utf-8 -*-
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import Request

from controller import gateway
from services.apikey import failover, passthrough as pt
from services.claude.slots import Slot, SlotType


def _request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


def _sub(slot_id="sub-1", priority=0):
    return Slot(id=slot_id, type=SlotType.SUBSCRIPTION, image="repo:test", priority=priority)


def _api(slot_id, model, priority):
    return Slot(
        id=slot_id, type=SlotType.API_KEY, priority=priority,
        env={
            "ANTHROPIC_BASE_URL": f"https://{slot_id}.example.test",
            "ANTHROPIC_AUTH_TOKEN": f"secret-{slot_id}",
            "ANTHROPIC_MODEL": model,
        },
    )


class _Router:
    def __init__(self, slots):
        self.slots = slots
        self.marked = []

    def route_candidates(self, user_id):
        return list(self.slots)

    def mark_unhealthy(self, slot_id, cooldown):
        self.marked.append(slot_id)


class _Response:
    def __init__(self, status, payload=None, *, chunks=None, stream_error=None):
        self.status_code = status
        self.payload = payload
        self.chunks = list(chunks or [])
        self.stream_error = stream_error
        self._raw = (
            payload if isinstance(payload, bytes)
            else json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        )
        self.text = self._raw.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._raw)

    async def aread(self):
        return self._raw

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk
        if self.stream_error is not None:
            raise self.stream_error


class _StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Client:
    def __init__(self, plan, calls):
        self.plan = plan
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _next(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        item = self.plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def post(self, url, *, headers, json):
        return self._next(url, headers, json)

    def stream(self, method, url, *, headers, json):
        return _StreamContext(self._next(url, headers, json))


def _install_http(monkeypatch, plan, calls):
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kwargs: _Client(plan, calls))


def _success(model):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 4},
    }


@pytest.fixture
def common(monkeypatch):
    monkeypatch.setattr(gateway.dm, "ensure_slot_container", lambda slot: None)
    monkeypatch.setattr(gateway.audit, "record_upstream", lambda **kwargs: None)
    billed = AsyncMock(return_value={})
    monkeypatch.setattr(gateway.usage_svc, "record_and_charge", billed)
    return billed


def test_api_key_auth_header_and_body_are_slot_specific():
    token_slot = _api("gemini", "gemini-3.5-flash", 100)
    # 两种值并存时 AUTH_TOKEN(Bearer) 优先，绝不能同时发送两份凭据。
    token_slot.env["ANTHROPIC_API_KEY"] = "api-key-must-not-be-used"
    _, headers, oauth = pt.upstream_for(token_slot, {"authorization": "Bearer client-secret"})
    assert oauth is False
    assert headers["authorization"] == "Bearer secret-gemini"
    assert "x-api-key" not in headers

    key_slot = _api("glm", "glm-5.2[1m]", 200)
    key_slot.env.pop("ANTHROPIC_AUTH_TOKEN")
    key_slot.env["ANTHROPIC_API_KEY"] = "glm-api-key"
    _, headers, _ = pt.upstream_for(key_slot)
    assert headers["x-api-key"] == "glm-api-key"
    assert "authorization" not in headers

    original = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]}
    gemini_body = pt.body_for_slot(token_slot, original, oauth=False)
    glm_body = pt.body_for_slot(key_slot, original, oauth=False)
    assert gemini_body["model"] == "gemini-3.5-flash"
    assert glm_body["model"] == "glm-5.2[1m]"
    assert original["model"] == "claude-sonnet-4-6"  # 每档从原 body 重建，没有串档


def test_retryable_classifier_preserves_normal_client_4xx():
    sub = _sub()
    gemini = _api("gemini", "gemini-3.5-flash", 100)
    assert failover.is_retryable_response(401, {}, slot=sub)
    assert failover.is_retryable_response(403, {}, slot=sub)
    assert failover.is_retryable_response(429, {}, slot=gemini)
    assert failover.is_retryable_response(503, {}, slot=gemini)
    assert failover.is_retryable_response(400, {"error": "weekly usage limit reached"}, slot=sub)
    assert failover.is_retryable_response(400, {"error": {"type": "quota_error"}}, slot=sub)
    assert failover.is_retryable_response(400, {"error": "Invalid model name"}, slot=gemini)
    assert failover.is_retryable_response(400, {"error": "convert_request_failed"}, slot=gemini)
    assert failover.is_retryable_response(404, {"error": "endpoint not found"}, slot=gemini)
    assert not failover.is_retryable_response(404, {"error": "not found"}, slot=sub)
    assert not failover.is_retryable_response(400, {"error": "max_tokens must be positive"}, slot=gemini)
    assert not failover.is_retryable_response(422, {"error": "invalid messages"}, slot=sub)


@pytest.mark.asyncio
async def test_anthropic_nonstream_crosses_all_slots_and_bills_requested_model(monkeypatch, common, caplog):
    slots = [
        _sub(),
        _api("gemini", "gemini-3.5-flash", 100),
        _api("gemini-2", "gemini-3.5-pro", 100),
        _api("glm", "glm-5.2[1m]", 200),
    ]
    slot_router = _Router(slots)
    monkeypatch.setattr(gateway, "get_router", lambda: slot_router)
    monkeypatch.setattr(gateway.settings, "CLAUDE_EXEC_MAX_ATTEMPTS", 1)

    # 避免单测读取 OAuth 文件；同时故意在 headers 放 secret，验证日志不回显。
    def upstream_for(slot, client_headers=None):
        return f"https://{slot.id}.example.test?token=url-secret", {
            "authorization": f"Bearer header-secret-{slot.id}"
        }, slot.type == SlotType.SUBSCRIPTION

    monkeypatch.setattr(gateway.pt, "upstream_for", upstream_for)
    calls = []
    plan = [
        _Response(401, {"error": {"message": "expired"}}),
        _Response(500, {"error": {"message": "down"}}),
        _Response(429, {"error": {"message": "quota"}}),
        _Response(200, _success("glm-5.2[1m]")),
    ]
    _install_http(monkeypatch, plan, calls)

    response = await gateway._passthrough_anthropic(
        {"id": 11}, {"id": 22},
        {"model": "claude-sonnet-4.6", "messages": [{"role": "user", "content": "hi"}]},
        _request(),
    )
    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["model"] == "claude-sonnet-4-6"
    assert [call["body"]["model"] for call in calls] == [
        "claude-sonnet-4-6", "gemini-3.5-flash", "gemini-3.5-pro", "glm-5.2[1m]",
    ]
    assert slot_router.marked == ["sub-1", "gemini", "gemini-2"]
    billed = common.await_args.kwargs
    assert billed["slot_id"] == "glm"
    assert billed["model"] == "claude-sonnet-4-6"
    assert billed["attempts"] == 4  # 不受旧 max_attempts=1 限制
    assert "header-secret" not in caplog.text and "url-secret" not in caplog.text


@pytest.mark.asyncio
async def test_normal_client_400_does_not_fall_back(monkeypatch, common):
    slot_router = _Router([_sub(), _api("gemini", "gemini-3.5-flash", 100)])
    monkeypatch.setattr(gateway, "get_router", lambda: slot_router)
    monkeypatch.setattr(
        gateway.pt, "upstream_for",
        lambda slot, headers=None: ("https://provider.test", {}, slot.type == SlotType.SUBSCRIPTION),
    )
    calls = []
    _install_http(monkeypatch, [
        _Response(400, {"error": {"type": "invalid_request_error", "message": "max_tokens invalid"}}),
    ], calls)

    response = await gateway._passthrough_anthropic(
        {"id": 1}, {"id": 2},
        {"model": "sonnet", "max_tokens": -1, "messages": []}, _request(),
    )
    assert response.status_code == 400
    assert len(calls) == 1
    assert slot_router.marked == []
    common.assert_not_awaited()


@pytest.mark.asyncio
async def test_config_and_network_exceptions_fall_through_without_logging_secret(monkeypatch, common, caplog):
    bad_config = Slot(
        id="bad-config", type=SlotType.API_KEY, priority=100,
        env={"ANTHROPIC_BASE_URL": "https://bad.example.test"},
    )
    gemini = _api("gemini", "gemini-3.5-flash", 100)
    glm = _api("glm", "glm-5.2[1m]", 200)
    slot_router = _Router([bad_config, gemini, glm])
    monkeypatch.setattr(gateway, "get_router", lambda: slot_router)
    calls = []
    _install_http(monkeypatch, [
        OSError("network failed with secret-network-token"),
        _Response(200, _success("glm-5.2[1m]")),
    ], calls)

    response = await gateway._passthrough_anthropic(
        {"id": 1}, {"id": 2},
        {"model": "sonnet", "messages": [{"role": "user", "content": "hi"}]}, _request(),
    )
    assert response.status_code == 200
    assert slot_router.marked == ["bad-config", "gemini"]
    assert [call["body"]["model"] for call in calls] == ["gemini-3.5-flash", "glm-5.2[1m]"]
    assert common.await_args.kwargs["attempts"] == 3
    assert "secret-network-token" not in caplog.text


@pytest.mark.asyncio
async def test_openai_translation_gemini_model_error_falls_to_glm(monkeypatch, common):
    gemini = _api("gemini", "gemini-3.5-flash", 100)
    glm = _api("glm", "glm-5.2[1m]", 200)
    slot_router = _Router([gemini, glm])
    monkeypatch.setattr(gateway, "get_router", lambda: slot_router)
    calls = []
    _install_http(monkeypatch, [
        _Response(400, {"error": {"message": "Invalid model name"}}),
        _Response(200, _success("glm-5.2[1m]")),
    ], calls)

    response = await gateway._passthrough_openai(
        {"id": 3}, {"id": 4},
        {"model": "sonnet", "messages": [{"role": "user", "content": "hi"}]},
        _request(),
    )
    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["model"] == "claude-sonnet-5"
    assert [call["body"]["model"] for call in calls] == ["gemini-3.5-flash", "glm-5.2[1m]"]
    assert slot_router.marked == ["gemini"]
    assert common.await_args.kwargs["model"] == "claude-sonnet-5"
    assert common.await_args.kwargs["slot_id"] == "glm"
    assert common.await_args.kwargs["attempts"] == 2


@pytest.mark.asyncio
async def test_anthropic_stream_retries_before_success_status(monkeypatch, common):
    slots = [_sub(), _api("gemini", "gemini-3.5-flash", 100), _api("glm", "glm-5.2[1m]", 200)]
    slot_router = _Router(slots)
    monkeypatch.setattr(gateway, "get_router", lambda: slot_router)
    monkeypatch.setattr(
        gateway.pt, "upstream_for",
        lambda slot, headers=None: (f"https://{slot.id}.test", {}, slot.type == SlotType.SUBSCRIPTION),
    )
    calls = []
    chunks = [
        b'event: message_start\ndata: {"type":"message_start","message":{"model":"glm-5.2[1m]",',
        b'"usage":{"input_tokens":7,"output_tokens":0}}}\n\n',
        b'event: message_delta\ndata: {"usage":{"output_tokens":4}}\n\n',
    ]
    _install_http(monkeypatch, [
        _Response(401, {"error": "expired"}),
        _Response(503, {"error": "unavailable"}),
        _Response(200, chunks=chunks),
    ], calls)

    response = await gateway._passthrough_anthropic(
        {"id": 7}, {"id": 8},
        {"model": "claude-sonnet-4.6", "stream": True,
         "messages": [{"role": "user", "content": "hi"}]},
        _request(),
    )
    sent = []
    async for part in response.body_iterator:
        sent.append(part if isinstance(part, bytes) else part.encode("utf-8"))
    output = b"".join(sent).decode("utf-8")

    assert len(calls) == 3
    assert [call["body"]["model"] for call in calls] == [
        "claude-sonnet-4-6", "gemini-3.5-flash", "glm-5.2[1m]",
    ]
    assert slot_router.marked == ["sub-1", "gemini"]
    assert '"model":"claude-sonnet-4-6"' in output
    assert "glm-5.2" not in output
    billed = common.await_args.kwargs
    assert billed["model"] == "claude-sonnet-4-6"
    assert billed["slot_id"] == "glm"
    assert billed["attempts"] == 3
    assert billed["prompt_tokens"] == 7 and billed["completion_tokens"] == 4


@pytest.mark.asyncio
async def test_anthropic_stream_does_not_switch_after_2xx(monkeypatch, common):
    gemini = _api("gemini", "gemini-3.5-flash", 100)
    glm = _api("glm", "glm-5.2[1m]", 200)
    slot_router = _Router([gemini, glm])
    monkeypatch.setattr(gateway, "get_router", lambda: slot_router)
    calls = []
    _install_http(monkeypatch, [
        _Response(
            200,
            chunks=[b'event: message_start\ndata: {"message":{"model":"gemini-3.5-flash"}}\n\n'],
            stream_error=OSError("connection lost"),
        ),
        _Response(200, chunks=[b'data: {"model":"glm-5.2[1m]"}\n\n']),
    ], calls)

    response = await gateway._passthrough_anthropic(
        {"id": 5}, {"id": 6},
        {"model": "sonnet", "stream": True, "messages": []}, _request(),
    )
    output = []
    async for part in response.body_iterator:
        output.append(part if isinstance(part, bytes) else part.encode())
    text = b"".join(output).decode()

    assert len(calls) == 1  # 2xx 已接受，断流也不能重放到 GLM
    assert slot_router.marked == []
    assert "upstream stream interrupted" in text
    assert "gemini-3.5-flash" not in text
    assert "claude-sonnet-5" in text
    assert common.await_args.kwargs["slot_id"] == "gemini"
    assert common.await_args.kwargs["attempts"] == 1
