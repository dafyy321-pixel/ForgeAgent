import json
from types import SimpleNamespace

import pytest
from forgeagent import billing, db, model_protocol, models, service
from forgeagent.config import settings
from forgeagent.context import compile_context
from forgeagent.domain import Fault, digest
from forgeagent.tokenization import count
from forgeagent.worker import Worker


def configured(monkeypatch):
    for key, value in {"model_provider": "openai", "model_id": "fixed-test-model", "model_api_key": "test-only",
                       "input_price": 2, "output_price": 10, "cached_input_price": 1, "cache_write_price": 3}.items():
        monkeypatch.setattr(settings, key, value)


def state(capabilities=None):
    return {"capabilities": capabilities or ["repo.read"], "semantic": {"model_id": "fixed-test-model"}}


def usage():
    return {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 8}}


def test_full_cost_categories_do_not_double_count_reasoning(monkeypatch):
    configured(monkeypatch)
    normalized = billing.normalize(usage(), "openai")
    assert billing.cost(normalized) == 221
    assert billing.tokens(normalized) == 30
    assert billing.cost({"input_tokens": 10, "output_tokens": 20}, upper=True) == 300
    anthropic = billing.normalize({"input_tokens": 5, "output_tokens": 20, "cache_read_input_tokens": 2,
                                   "cache_creation_input_tokens": 3}, "anthropic")
    assert anthropic["input_tokens"] == 10 and billing.cost(anthropic) == 221


@pytest.mark.parametrize("broken", [None, {}, {"input_tokens": -1}, {"input_tokens": True},
                                   {"input_tokens_details": {"cached_tokens": 99, "cache_write_tokens": 0}},
                                   {"total_tokens": 999}, {"input_tokens_details": {"cached_tokens": 2}}])
def test_missing_or_inconsistent_billing_stays_unknown(monkeypatch, broken):
    configured(monkeypatch)
    observed = broken if broken is None else {**usage(), **broken}
    if broken == {}:
        observed = {}
    assert billing.normalize(observed, "openai") is None


def test_unknown_positive_cache_tariff_cannot_settle(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr(settings, "cached_input_price", None)
    with pytest.raises(Fault, match="cache usage"):
        billing.cost({"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 2})


def test_real_tokenizer_and_context_retain_critical_errors():
    assert count("你好，ForgeAgent") < len("你好，ForgeAgent".encode())
    task = {"goal": "Fix src/calculator.py", "allowed_paths": ["src"]}
    observations = [{"status": "FAILED", "action_id": "important", "error": "unexpected return", "ref": {"digest": digest("log")}},
                    {"status": "SUCCEEDED", "output": "noise " * 50000}]
    _, manifest = compile_context(task, {"last_failure": {"code": "TEST_FAIL"}}, observations, [], [], 8000, 1000)
    assert manifest["count_kind"] == "local_estimate_with_framing_reserve"
    assert any(i["type"] == "unresolved_error" and i["content"]["action_id"] == "important" for i in manifest["items"])
    assert manifest["estimated_tokens_upper_bound"] <= manifest["input_budget"]


def test_wire_tool_catalog_is_capability_scoped_and_strict(monkeypatch):
    configured(monkeypatch)
    request = model_protocol.build_request([{"role": "system", "content": "JSON"}, {"role": "user", "content": "read"}], state(), service.TOOLS)
    names = set(request["names"].values())
    assert "repo.read" in names and "repo.write" not in names and "delegate" not in names
    for tool in request["payload"]["tools"]:
        schema = tool["parameters"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "queued"])
def test_unfinished_response_never_becomes_a_decision(monkeypatch, status):
    configured(monkeypatch)
    request = model_protocol.build_request([{"role": "system", "content": "JSON"}], state(), service.TOOLS)
    _, receipt = model_protocol.normalize_response({"status": status, "output": []}, request)
    assert receipt["end_status"] == status


def test_native_and_structured_schema_reject_open_authority(monkeypatch):
    configured(monkeypatch)
    schema = model_protocol.structured_schema(state(), service.TOOLS)
    assert schema["additionalProperties"] is False
    with pytest.raises(Fault, match="Open-ended"):
        model_protocol.strict_schema({"type": "object", "additionalProperties": True})
    with pytest.raises(ValueError, match="advertised"):
        model_protocol.decode_calls([{"name": "unknown", "call_id": "x", "arguments": "{}"}], {})


async def test_native_call_id_and_encrypted_reasoning_survive_runtime_resume(tenant, make_run, monkeypatch):
    configured(monkeypatch)
    requests, counts = [], []
    class Client:
        def __init__(self):
            self.responses = SimpleNamespace(create=self.create, input_tokens=SimpleNamespace(count=self.count))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def count(self, **payload):
            counts.append(payload)
            return SimpleNamespace(input_tokens=1000)
        async def create(self, **payload):
            requests.append(payload)
            tool = next(t for t in payload["tools"] if t["description"] == ("repo.read" if len(requests) == 1 else "propose_completion"))
            args = {"path": "src/calculator.py"} if len(requests) == 1 else {"summary": "proposed"}
            raw = {"id": "resp_" + str(len(requests)), "model": "fixed-test-model", "service_tier": "default", "status": "completed", "usage": usage(),
                   "output": [{"type": "reasoning", "id": "rs_opaque", "encrypted_content": "encrypted-only", "summary": []},
                              {"type": "function_call", "name": tool["name"], "call_id": "native_" + str(len(requests)), "arguments": json.dumps(args)}]}
            return SimpleNamespace(model_dump=lambda **kwargs: raw, _request_id="request-id")
    monkeypatch.setattr(models, "openai_client", Client)
    run_id = make_run(model="configured")
    worker = Worker(target_run=run_id)
    await worker.once(tenant)
    await worker.once(tenant)
    await worker.once(tenant)
    assert len(requests) == 2 and len(counts) == 2
    outputs = [i for i in requests[1]["input"] if i.get("type") == "function_call_output"]
    assert outputs[0]["call_id"] == "native_1" and "src/calculator.py" in outputs[0]["output"]
    assert any(i.get("encrypted_content") == "encrypted-only" for i in requests[1]["input"])
    assert counts[1]["tools"] == requests[1]["tools"] and counts[1]["input"] == requests[1]["input"]
    assert requests[0]["prompt_cache_key"] == requests[1]["prompt_cache_key"]
    assert requests[0]["extra_headers"]["X-Client-Request-Id"] != requests[1]["extra_headers"]["X-Client-Request-Id"]
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.state["tokens"] == 60
        assert db.get(s, db.BudgetAccount, tenant, run_id).spent == 442
        assert len(db.rows(s, db.Action, tenant, run_id=run_id)) == 1


async def test_worker_pins_real_failed_receipt_and_summarizes_success(tenant, make_run, monkeypatch):
    from forgeagent import observation
    from forgeagent.context_summary import summarize
    from forgeagent.domain import uid
    captured = []
    async def model(messages, model_id):
        captured.extend(json.loads(messages[1]["content"]))
        return '{"kind":"request_input","summary":"Review failure"}', {"input_tokens": 1, "output_tokens": 1}, {}, "test"
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        for status in ["FAILED", "SUCCEEDED"]:
            s.add(db.Action(tenant_id=tenant, id=uid(), run_id=run_id, tool="repo.read", status=status,
                            logical_key=status, effect_class="read", effect_digest=digest(status),
                            args={"path": "src/calculator.py"}, receipt={"ref": {"digest": digest(status)},
                            "preview": "real receipt failure", "exit_code": int(status == "FAILED")}))
        run.state = {**run.state, "fixture": False}
    await Worker(target_run=run_id, model=model).once(tenant)
    assert any(i["type"] == "unresolved_error" and i["content"]["result"]["preview"] == "real receipt failure" for i in captured)
    with db.transaction(tenant) as s:
        action = next(a for a in db.rows(s, db.Action, tenant, run_id=run_id) if a.status == "SUCCEEDED")
        summary = summarize({"content": observation.project(action), "trust": "untrusted_tool_output"})
        assert summary["content"]["source_ref"] == action.receipt["ref"]


def test_summary_tampering_and_bounded_log_ranges():
    from forgeagent import context_summary, observation
    task, runtime = {"goal": "keep constraints"}, {"unresolved": ["billing"], "input_revision": 2}
    summary = context_summary.phase_summary(task, runtime, [])
    with pytest.raises(Fault, match="constraint"):
        context_summary.validate({**summary, "unresolved": []}, task, runtime, [])
    text = "α\nβ\n" + "长" * 50000
    value = observation.envelope({"output": text}, "tests.run", {"workspace_digest": "w", "artifact_version": 1})
    result = observation.read(value, {"digest": digest(value)}, 1, 2)
    assert result["content"].startswith("β\n") and len(result["content"].encode()) <= 65536
    assert result["truncated"] and result["total_lines"] == 3
    assert result["text_digest"] == value["_observation"]["text_digest"]


def test_retry_only_known_rejection_and_honor_retry_after():
    from datetime import UTC, datetime

    from forgeagent.model_retry import classify
    time = datetime(2026, 1, 1, tzinfo=UTC)
    for header in ["60", "Thu, 01 Jan 2026 00:01:00 GMT"]:
        exc = SimpleNamespace(status_code=429, response=SimpleNamespace(headers={"retry-after": header}))
        assert classify(exc, time, 1)["delay_seconds"] == 60
        assert classify(exc, time, 1)["retryable"]
    for status in [500, 503, None]:
        result = classify(SimpleNamespace(status_code=status), time, 1)
        assert result["requires_query"] and not result["known_unbilled"] and not result["retryable"]


def test_cache_economics_do_not_claim_estimates_as_real_savings(monkeypatch):
    from forgeagent.context_cost import report
    configured(monkeypatch)
    rates = service.model_profile()
    result = report([{"usage": {"input_tokens": 10, "output_tokens": 0, "cache_write_tokens": 10}, "rate_card": rates}], [])
    assert result["observed_cache_savings_micros"] == -10
    assert report([{"fixture": True}], [{"full_local_tokens": 20, "estimated_tokens_upper_bound": 10}])["observed_samples"] == 0


async def test_original_response_query_only_retrieves_and_requires_review(monkeypatch):
    configured(monkeypatch)
    retrieved = []
    class Client:
        def __init__(self):
            self.responses = SimpleNamespace(retrieve=self.retrieve)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def retrieve(self, response_id):
            retrieved.append(response_id)
            return SimpleNamespace(model_dump=lambda **kw: {"id": response_id, "model": "fixed-test-model", "usage": usage()})
    monkeypatch.setattr(models, "openai_client", Client)
    request = model_protocol.build_request([{"role": "system", "content": "JSON"}], state(), service.TOOLS)
    request["rate_card"] = service.model_profile()
    result = await models.query_billing(request, "resp_original")
    assert retrieved == ["resp_original"] and result["actual_micros"] == 221 and result["requires_review"]


@pytest.mark.parametrize("end_status", ["refused", "incomplete"])
async def test_paid_unfinished_response_applies_no_effect(tenant, make_run, end_status, monkeypatch):
    monkeypatch.setattr(settings, "input_price", 1)
    monkeypatch.setattr(settings, "output_price", 2)
    async def model(messages, model_id):
        return json.dumps({"kind": "tool_calls", "summary": "must not run", "calls": [
            {"tool": "repo.read", "args": {"path": "src/calculator.py"}}]}), {"input_tokens": 1, "output_tokens": 2}, {"_forge": {"end_status": end_status}}, "response"
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "fixture": False}
    await Worker(target_run=run_id, model=model).once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "PAUSED" and run.state["tokens"] == 3
        assert db.get(s, db.BudgetAccount, tenant, run_id).spent == 5
        assert not db.rows(s, db.Action, tenant, run_id=run_id)
