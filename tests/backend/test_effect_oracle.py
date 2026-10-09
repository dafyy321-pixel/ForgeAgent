import importlib.util
from datetime import timedelta
from pathlib import Path
from threading import Thread

import httpx
import pytest
from forgeagent import db, remote, service
from forgeagent.domain import ApprovalDecision, ToolCall, uid
from forgeagent.remote_contracts import tool_name
from forgeagent.worker import Worker
from test_remote_contracts import registered


@pytest.fixture
def oracle(tmp_path):
    spec = importlib.util.spec_from_file_location("independent_oracle", Path(__file__).parents[2] / "scripts/effect_oracle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = uid()
    server = module.server(tmp_path / "oracle.sqlite", token)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", {"Authorization": "Bearer " + token, "X-Test-Scope": uid()}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_commit_then_lost_response_does_not_repeat_external_effect(client, tenant, make_run, monkeypatch, oracle):
    url, headers = oracle
    schema = {"type": "object", "properties": {"value": {"type": "string"}, "request_key": {"type": "string"}},
              "required": ["value", "request_key"], "additionalProperties": False}
    connection = registered(tenant, "external_write", "publish", schema, idempotency_field="request_key")
    run_id = make_run(connections=[connection], capabilities=["external.write"])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = service.prepare_action(s, run, ToolCall(tool=tool_name(connection, "publish"), args={"value": "effect"}), "oracle")
        action_id, business_key = action.id, action.args["business_key"]
        approval = db.rows(s, db.Approval, tenant, action_id=action.id)[0]
        service.approve(s, tenant, "tester", approval.id, ApprovalDecision(expected_version=run.version,
            decision="approve", effect_digest=action.effect_digest, reason="Independent test effect approval"), admin=True)
    async def dispatch(*args):
        async with httpx.AsyncClient() as http:
            await http.post(url, content=b"effect", headers={**headers, "Idempotency-Key": business_key, "X-Test-Drop-After-Commit": "1"})
        raise AssertionError("Oracle deliberately drops the committed reply")
    monkeypatch.setattr(remote, "dispatch_remote", dispatch)
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Action, tenant, action_id).status == "UNKNOWN"
        run = db.get(s, db.Run, tenant, run_id)
        run.lease_until = db.clock(s) - timedelta(seconds=1)
        run.available_at = db.clock(s)
    for _ in range(3):
        await Worker(target_run=run_id).once(tenant)
    async with httpx.AsyncClient() as http:
        evidence = (await http.get(url, headers=headers)).json()
    assert evidence["count"] == evidence["attempts"] == 1
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        version = run.version
        assert run.status == "WAITING" and run.wait_reason == "RECONCILIATION" and not run.state.get("verification")
    response = client.post(f"/v1/actions/{action_id}/reconcile", json={"expected_version": version,
        "outcome": "occurred", "evidence": str(evidence), "reason": "Read-only independent oracle lookup"})
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"


def test_oracle_key_conflicts_and_scope_counts_are_independent(oracle):
    url, headers = oracle
    with httpx.Client() as http:
        for _ in range(2):
            assert http.post(url, content=b"one", headers={**headers, "Idempotency-Key": "stable"}).status_code == 200
        assert http.post(url, content=b"changed", headers={**headers, "Idempotency-Key": "stable"}).status_code == 409
        result = http.get(url, headers=headers).json()
        assert result["count"] == 1 and result["attempts"] == 2
        assert http.get(url, headers={**headers, "X-Test-Scope": "other"}).json()["count"] == 0
        assert http.get(url).status_code == 401
