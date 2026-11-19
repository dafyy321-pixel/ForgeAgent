import json

import pytest
from forgeagent import db, model_protocol, remote_contracts
from forgeagent.config import settings
from forgeagent.context import compile_context
from forgeagent.tokenization import count
from forgeagent.worker import Worker
from test_remote_contracts import registered


def model_state(mode, lazy=True):
    return {"task": {"mode": "discuss", "goal": "inspect", "allowed_paths": ["src"]},
            "capabilities": ["repo.read", "external.read"],
            "semantic": {"model_id": "test", "harness": {"tool_form": mode, "defer_remote_tools": lazy}},
            "remote_connections": [{"id": "docs", "data": {"tools": [
                {"operation": op, "effect": effect, "description": op, "input_schema":
                 {"type": "object", "properties": {"needle": {"type": "string"}}, "required": ["needle"]}}
                for op, effect in [("lookup", "read"), ("publish", "write")]]}}]}


@pytest.mark.parametrize("mode", ["native", "structured", "json"])
def test_discussion_in_actual_payload_and_lazy_catalog(monkeypatch, mode):
    monkeypatch.setattr(settings, "model_provider", "openai")
    st = model_state(mode)
    messages, _ = compile_context(st["task"], st, [], [], [], 32768, 1024)
    request = model_protocol.build_request(messages, st, remote_contracts.catalog(st))
    assert "Answer in the user's language" in json.dumps(request["payload"])
    remote = remote_contracts.tool_name("docs", "lookup")
    assert "needle" not in json.dumps(request["payload"])
    hits = remote_contracts.discover(st, "lookup")
    assert [h["name"] for h in hits] == [remote]
    assert not remote_contracts.discover(st, "publish")
    st["discovered_remote_tools"] = [remote]
    messages, _ = compile_context(st["task"], st, [], [], [], 32768, 1024)
    request = model_protocol.build_request(messages, st, remote_contracts.catalog(st))
    assert "needle" in json.dumps(request["payload"])
    if mode == "native":
        assert remote in request["names"].values()
        assert "needle" not in messages[1]["content"]


async def test_worker_discovers_only_local_metadata_and_persists_selection(tenant, make_run):
    connection = registered(tenant)
    run_id = make_run(model="configured", connections=[connection], capabilities=["external.read"],
                      harness={"defer_remote_tools": True})
    async def propose(*args):
        decision = {"kind": "tool_calls", "summary": "Find a lookup tool",
                    "calls": [{"tool": "tools.discover", "args": {"query": "lookup"}}]}
        return json.dumps(decision), {"input_tokens": 1, "output_tokens": 1}, {}, "local-fixture"
    worker = Worker(model=propose, target_run=run_id)
    await worker.once(tenant)
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        name = remote_contracts.tool_name(connection, "lookup")
        assert run.state["discovered_remote_tools"] == [name]
        assert name in remote_contracts.catalog(run.state)
        actions = db.rows(s, db.Action, tenant, run_id=run_id)
        assert [(a.tool, a.status) for a in actions] == [("tools.discover", "SUCCEEDED")]
        assert not db.rows(s, db.Outbox, tenant)


@pytest.mark.parametrize("size", [2, 64])
def test_deferred_native_request_advertises_bounded_schema_set(monkeypatch, size):
    monkeypatch.setattr(settings, "model_provider", "openai")
    st = model_state("native")
    prototype = st["remote_connections"][0]["data"]["tools"][0]
    st["remote_connections"][0]["data"]["tools"] = [
        {**prototype, "operation": f"lookup_{i}"} for i in range(size)]
    def request():
        messages, _ = compile_context(st["task"], st, [], [], [], 32768, 1024)
        return model_protocol.build_request(messages, st, remote_contracts.catalog(st))
    hits = remote_contracts.discover(st, "lookup_0")
    st["discovered_remote_tools"] = [h["name"] for h in hits]
    selected = request()
    st["semantic"]["harness"]["defer_remote_tools"] = False
    eager = request()
    assert len(set(selected["names"].values()) & {remote_contracts.tool_name("docs", f"lookup_{i}") for i in range(size)}) == 1
    if size == 64:
        assert count(selected["payload"]["tools"]) < count(eager["payload"]["tools"])
