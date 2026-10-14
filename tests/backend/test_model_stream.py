"""Exercise the real SDK stream parser without making billable requests."""

import json

import httpx
import pytest
from forgeagent import billing, model_protocol, models, service
from forgeagent.config import settings
from forgeagent.domain import Fault, now
from forgeagent.model_retry import classify
from openai import AsyncOpenAI


@pytest.fixture
def profile(monkeypatch):
    for key, value in {"model_provider": "openai", "model_protocol": "responses", "model_id": "fixed-model",
                       "model_api_key": "test-only", "model_reasoning_effort": "high", "model_stream": True,
                       "input_price": .1, "output_price": .5, "cached_input_price": .01,
                       "cache_write_price": None, "model_output_mode": "native"}.items():
        monkeypatch.setattr(settings, key, value)
    return model_protocol.build_request([{"role": "system", "content": "Use scoped tools"}],
        {"capabilities": ["repo.read"], "semantic": {"model_id": "fixed-model"}}, service.TOOLS)


def test_reasoning_and_transport_are_pinned(profile):
    assert profile["payload"]["reasoning"] == {"effort": "high"}
    assert profile["payload"]["stream"] is True
    assert service.model_profile()["reasoning_effort"] == "high"
    assert service.model_profile()["stream"] is True


@pytest.mark.parametrize("provider,protocol", [("anthropic", "responses"), ("openai", "chat")])
def test_unsupported_stream_profile_fails_before_dispatch(profile, monkeypatch, provider, protocol):
    monkeypatch.setattr(settings, "model_provider", provider)
    monkeypatch.setattr(settings, "model_protocol", protocol)
    with pytest.raises(Fault, match="requires|currently"):
        model_protocol.build_request([{"role": "system", "content": "Use tools"}],
            {"semantic": {"model_id": "fixed-model"}}, {})


def test_chat_uses_reasoning_effort_parameter(profile, monkeypatch):
    monkeypatch.setattr(settings, "model_stream", False)
    monkeypatch.setattr(settings, "model_protocol", "chat")
    request = model_protocol.build_request([{"role": "system", "content": "Use tools"}],
        {"semantic": {"model_id": "fixed-model"}}, {})
    assert request["payload"]["reasoning_effort"] == "high"
    assert "reasoning" not in request["payload"] and "stream" not in request["payload"]


def test_sdk_null_cache_write_needs_explicit_count_when_write_tariff_is_configured(profile, monkeypatch):
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
             "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": None}}
    assert billing.normalize(usage, "openai")["cache_write_tokens"] == 0
    monkeypatch.setattr(settings, "cache_write_price", .125)
    assert billing.normalize(usage, "openai") is None


@pytest.mark.parametrize("terminal", ["completed", "failed", "incomplete", "missing", "duplicate", "unknown_usage"])
async def test_sdk_stream_preserves_terminal_usage_and_unknown_dispatch(profile, monkeypatch, terminal):
    sent = []
    response = {"id": "resp_stream", "model": "fixed-model", "status": terminal,
                "service_tier": "default", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                "input_tokens_details": {"cached_tokens": 2}, "output_tokens_details": {"reasoning_tokens": 1}},
                "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                            "name": "forge_propose_completion", "arguments": '{"summary":"Done"}'}]}
    if terminal == "unknown_usage":
        response.update(status="completed", usage=None)
    if terminal == "duplicate":
        response["status"] = "completed"
    event_type = "response." + response["status"]
    event = f"event: {event_type}\ndata: " + json.dumps({"type": event_type, "response": response, "sequence_number": 1}) + "\n\n"
    body = "data: [DONE]\n\n" if terminal == "missing" else event * (2 if terminal == "duplicate" else 1)

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    monkeypatch.setattr(models, "openai_client", lambda: AsyncOpenAI(api_key="test-only", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    if terminal in {"missing", "duplicate"}:
        with pytest.raises(Fault, match="terminal|uncertain") as caught:
            await models.generate([], "fixed-model", profile)
        disposition = classify(caught.value, now(), 1)
        assert disposition["requires_query"] and not disposition["retryable"]
    else:
        content, usage, raw, request_id = await models.generate([], "fixed-model", profile)
        assert request_id == "resp_stream"
        assert usage is None if terminal == "unknown_usage" else usage["input_tokens"] == 10
        assert raw["_forge"]["end_status"] == response["status"]
        if response["status"] == "completed":
            assert json.loads(content)["kind"] == "propose_completion"
            assert raw["_forge"]["calls"][0]["call_id"] == "call_1"
    assert len(sent) == 1 and sent[0]["reasoning"] == {"effort": "high"}


async def test_token_count_excludes_stream_transport(profile, monkeypatch):
    counted = []
    monkeypatch.setattr(settings, "model_token_count", True)

    def handler(request):
        counted.append(json.loads(request.content))
        return httpx.Response(200, json={"input_tokens": 100, "object": "response.input_tokens"})

    monkeypatch.setattr(models, "openai_client", lambda: AsyncOpenAI(api_key="test-only", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    # count_request uses the same SDK and pinned reasoning payload as generation.
    result = await models.count_request(profile)
    assert result == (100, "provider_count")
    assert "stream" not in counted[0] and counted[0]["reasoning"] == {"effort": "high"}
