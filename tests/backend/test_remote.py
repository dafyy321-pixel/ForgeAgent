import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from forgeagent import db, remote, service
from forgeagent.config import settings
from forgeagent.domain import Fault, canonical, digest, uid
from forgeagent.worker import Worker


async def test_sdk_client_factory_accepts_positional_protocol_arguments():
    async with remote.safe_client({"X-Test": "factory"}, httpx.Timeout(7), None) as client:
        assert client.headers["X-Test"] == "factory"
        assert client.timeout.read == 7 and not client.follow_redirects


def pending_remote(tenant, run_id, kind="mcp", protocol="2026-07-28"):
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = db.Action(
            tenant_id=tenant,
            run_id=run_id,
            logical_key=uid(),
            tool="remote.call",
            args={
                "connection_id": "test-provider",
                "connection": {"kind": kind, "protocol": protocol, "url": "https://example.com/mcp"},
            },
            effect_class="external_write",
            effect_digest=digest({}),
            status="RUNNING",
            receipt={"remote_task_id": "task-1", "remote_status": "working", "pending": True},
        )
        s.add(action)
        s.flush()
        run.status, run.wait_reason = "WAITING", "TOOL"
        return action.id


async def test_mcp_task_handle_survives_claim(tenant, make_run, monkeypatch):
    id = make_run(capabilities=["repo.read", "external.write"])
    action_id = pending_remote(tenant, id)
    rpc = AsyncMock(return_value={"taskId": "task-1", "status": "working", "pollIntervalMs": 5000})
    monkeypatch.setattr(remote, "rpc", rpc)
    await Worker().once(tenant)
    assert rpc.call_args.args[1] == "tasks/get"
    with db.transaction(tenant) as s:
        a = db.get(s, db.Action, tenant, action_id)
        assert a.status == "RUNNING" and a.receipt["remote_task_id"] == "task-1"
        assert a.attempt == 0


async def test_remote_cancel_ack_does_not_imply_terminal(tenant, make_run, monkeypatch):
    id = make_run()
    action_id = pending_remote(tenant, id)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        r.cancel_requested = True
    rpc = AsyncMock(side_effect=[{}, {"taskId": "task-1", "status": "working"}])
    monkeypatch.setattr(remote, "rpc", rpc)
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        a = db.get(s, db.Action, tenant, action_id)
        assert r.status != "CANCELLED" and r.cancel_requested
        assert a.receipt["cancel_sent"] and a.status == "RUNNING"


async def test_remote_terminal_failed_tool_result_is_failure(tenant, make_run, monkeypatch):
    id = make_run()
    action_id = pending_remote(tenant, id)
    monkeypatch.setattr(
        remote, "rpc", AsyncMock(return_value={"taskId": "task-1", "status": "completed", "result": {"isError": True}})
    )
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Action, tenant, action_id).status == "FAILED"


async def test_remote_input_creates_durable_outbox(client, tenant, make_run, monkeypatch):
    id = make_run(capabilities=["repo.read", "external.write"])
    action_id = pending_remote(tenant, id)
    with db.transaction(tenant) as s:
        a = db.get(s, db.Action, tenant, action_id)
        a.receipt = {
            **a.receipt,
            "remote_status": "input_required",
            "response": {"inputRequests": {"answer": {"method": "elicitation/create", "params": {
                "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}},
                                    "required": ["name"], "additionalProperties": False}}}}},
        }
        r = db.get(s, db.Run, tenant, id)
        r.status = "PAUSED"
        version = r.version
    result = client.post(
        f"/v1/actions/{action_id}/input",
        json={
            "expected_version": version,
            "reason": "reviewed input",
            "responses": {"answer": {"result": {"action": "accept", "content": {"name": "test"}}}},
        },
    )
    assert result.status_code == 202
    rpc = AsyncMock(side_effect=[{}, {"taskId": "task-1", "status": "completed", "result": {"isError": False}}])
    monkeypatch.setattr(remote, "rpc", rpc)
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Outbox, tenant, result.json()["id"]).status == "sent"
        assert db.get(s, db.Action, tenant, action_id).status == "SUCCEEDED"


async def test_remote_input_timeout_never_resends(tenant, make_run, monkeypatch):
    id = make_run(capabilities=["repo.read", "external.write"])
    action_id = pending_remote(tenant, id)
    with db.transaction(tenant) as s:
        job = db.Outbox(
            tenant_id=tenant,
            status="pending",
            data={"action_id": action_id, "policy_epoch": 1, "params": {"taskId": "task-1", "input": "approved"}},
        )
        s.add(job)
        s.flush()
        job_id = job.id
    rpc = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(remote, "rpc", rpc)
    await Worker().once(tenant)
    await Worker().once(tenant)
    assert rpc.await_count == 1
    with db.transaction(tenant) as s:
        assert db.get(s, db.Outbox, tenant, job_id).status == "unknown"
        assert db.get(s, db.Action, tenant, action_id).status == "UNKNOWN"


async def test_interrupted_outbox_is_not_dispatched_again(tenant, make_run, monkeypatch):
    id = make_run(capabilities=["repo.read", "external.write"])
    action_id = pending_remote(tenant, id)
    with db.transaction(tenant) as s:
        s.add(db.Outbox(tenant_id=tenant, status="dispatching", data={"action_id": action_id}))
    rpc = AsyncMock()
    monkeypatch.setattr(remote, "rpc", rpc)
    await Worker().once(tenant)
    rpc.assert_not_called()
    with db.transaction(tenant) as s:
        assert db.get(s, db.Action, tenant, action_id).status == "UNKNOWN"


def test_input_reconciliation_preserves_remote_task_handle(client, tenant, make_run):
    id = make_run(capabilities=["repo.read", "external.write"])
    action_id = pending_remote(tenant, id)
    with db.transaction(tenant) as s:
        a = db.get(s, db.Action, tenant, action_id)
        a.status = "UNKNOWN"
        job = db.Outbox(tenant_id=tenant, status="unknown", data={"action_id": action_id})
        s.add(job)
        s.flush()
        job_id = job.id
        version = db.get(s, db.Run, tenant, id).version
    response = client.post(
        f"/v1/outbox/{job_id}/reconcile",
        json={"expected_version": version, "outcome": "occurred", "evidence": "provider confirmed input receipt 123"},
    )
    assert response.status_code == 200
    with db.transaction(tenant) as s:
        a = db.get(s, db.Action, tenant, action_id)
        assert a.status == "RUNNING" and a.receipt["remote_task_id"] == "task-1"
        assert db.get(s, db.Outbox, tenant, job_id).status == "sent"


async def test_remote_deadline_never_blind_retries(tenant, make_run, monkeypatch):
    id = make_run()
    action_id = pending_remote(tenant, id)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        r.created_at = db.clock(s) - timedelta(days=2)
    rpc = AsyncMock()
    monkeypatch.setattr(remote, "rpc", rpc)
    await Worker().once(tenant)
    rpc.assert_not_called()
    with db.transaction(tenant) as s:
        assert db.get(s, db.Action, tenant, action_id).status == "UNKNOWN"


@pytest.mark.parametrize(
    "state,pending,code",
    [
        ("TASK_STATE_WORKING", True, 0),
        ("TASK_STATE_COMPLETED", False, 0),
        ("TASK_STATE_FAILED", False, 1),
        ("TASK_STATE_INPUT_REQUIRED", True, 0),
    ],
)
def test_a2a_v1_status_mapping(state, pending, code):
    receipt = remote.remote_result({"kind": "a2a"}, {"task": {"id": "task-1", "status": {"state": state}}})
    assert receipt["pending"] == pending and receipt["exit_code"] == code


async def test_remote_ssrf_blocks_private_dns(monkeypatch):
    monkeypatch.setattr(settings, "remote_hosts", "example.com")
    resolver = AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    with pytest.raises(Fault, match="Private"):
        await remote.validate_url("https://example.com/mcp")


async def test_transport_pins_validated_address(monkeypatch):
    monkeypatch.setattr(remote, "validate_url", AsyncMock(return_value="93.184.216.34"))
    transport = remote.SafeTransport()
    inner = AsyncMock()
    inner.handle_async_request.return_value = httpx.Response(200, json={"ok": True})
    transport.transport = inner
    request = httpx.Request("POST", "https://example.com/mcp")
    await transport.handle_async_request(request)
    passed = inner.handle_async_request.call_args.args[0]
    assert passed.url.host == "93.184.216.34"
    assert passed.headers["Host"] == "example.com" and passed.extensions["sni_hostname"] == "example.com"


def test_callback_signature_and_deduplication(tenant, make_run, monkeypatch):
    monkeypatch.setattr(settings, "remote_credentials", json.dumps({"test-callback": {
        "tenant_id": tenant, "origin": "https://example.com", "callback_secret": "test-secret"}}))
    id = make_run()
    action_id = pending_remote(tenant, id)
    body = canonical({"action_id": action_id, "state": "completed"})
    with db.transaction(tenant) as s:
        s.add(db.ToolVersion(tenant_id=tenant, id="test-provider", data={
            "tenant_id": tenant, "url": "https://example.com/mcp", "callback_key_ref": "test-callback"}))
        s.flush()
        timestamp = int(db.clock(s).timestamp())
        signature = remote.callback_signature("test-secret", tenant, "test-provider", "message-1", timestamp, body)
        assert not remote.accept_callback(s, tenant, "test-provider", "message-1", timestamp, body, signature)[
            "duplicate"
        ]
        s.flush()
        assert remote.accept_callback(s, tenant, "test-provider", "message-1", timestamp, body, signature)["duplicate"]
        with pytest.raises(Fault, match="signature"):
            remote.accept_callback(s, tenant, "test-provider", "message-2", timestamp, body, "wrong")


async def test_timeout_keeps_reservation_and_pauses(tenant, make_run):
    id = make_run(model="configured")

    async def failing_model(*args):
        raise TimeoutError()

    await Worker(model=failing_model).once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, id).status == "PAUSED"
        assert db.rows(s, db.ModelCall, tenant, run_id=id)[0].status == "UNKNOWN"
        assert db.rows(s, db.BudgetEntry, tenant)[0].status == "unknown"


async def test_missing_usage_does_not_execute_decision_until_reconciled(client, tenant, make_run):
    id = make_run(model="configured")
    response = {"kind": "tool_calls", "summary": "read", "calls": [{"tool": "repo.list", "args": {}}]}

    async def model(*args):
        return json.dumps(response), None, response, "provider-1"

    await Worker(model=model).once(tenant)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        assert r.status == "PAUSED"
        assert not db.rows(s, db.Action, tenant, run_id=id)
        call = db.rows(s, db.ModelCall, tenant, run_id=id)[0]
        assert call.data["receipt_state"] == "received"
        version = r.version
    result = client.post(
        f"/v1/runs/{id}/budget/{call.id}/reconcile",
        json={
            "expected_version": version,
            "actual_micros": 0,
            "evidence": "Provider invoice shows zero charge",
            "reason": "reviewed invoice",
        },
    )
    assert result.status_code == 200
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, id)
        service.control(s, tenant, id, "resume", run.version, "continue with reconciled response")
    await Worker(model=model).once(tenant)
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.ModelCall, tenant, run_id=id)) == 1
        action = db.rows(s, db.Action, tenant, run_id=id)[0]
        assert action.status == "READY" and action.attempt == 0


async def test_malformed_decision_retries_are_bounded(tenant, make_run):
    id = make_run(model="configured")

    async def model(*args):
        return "not json", {"input_tokens": 1, "output_tokens": 1}, {"text": "not json"}, "request"

    worker = Worker(model=model)
    for _ in range(4):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, id).status == "PAUSED"
        assert len(db.rows(s, db.ModelCall, tenant, run_id=id)) == 3


def test_terminal_and_action_identity_enforced_in_database(tenant, make_run):
    id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, id)
        run.status = "CANCELLED"
    with pytest.raises(Exception, match="terminal run"), db.transaction(tenant) as s:
        db.get(s, db.Run, tenant, id).status = "ACTIVE"
    other = make_run()
    aid = pending_remote(tenant, other)
    with pytest.raises(Exception, match="intent is immutable"), db.transaction(tenant) as s:
        db.get(s, db.Action, tenant, aid).args = {"tampered": True}
