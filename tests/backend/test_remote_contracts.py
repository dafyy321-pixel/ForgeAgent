import json
import ssl
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from forgeagent import db, model_protocol, remote, remote_contracts, service
from forgeagent.config import settings
from forgeagent.domain import Fault, ToolCall, canonical, digest
from forgeagent.storage import objects
from forgeagent.worker import Worker
from test_remote import pending_remote

SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}


def registered(tenant, effect="read", operation="lookup", schema=None, **fields):
    tool = remote_contracts.RemoteTool(operation=operation, description="Reviewed test operation", effect=effect,
                                      input_schema=schema or SCHEMA, **fields).model_dump()
    with db.transaction(tenant) as s:
        connection = db.ToolVersion(tenant_id=tenant, data={"tenant_id": tenant, "kind": "mcp", "protocol": "2026-07-28",
                                                          "url": "https://example.com/mcp", "tools": [tool],
                                                          "negotiated": {"digest": digest("local contract evidence")}})
        s.add(connection)
        s.flush()
        return connection.id


def ready(tenant, run_id):
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.available_at = db.clock(s)
        db.emit(s, run, "TEST_TIMER", "Test advances a durable retry timer")


async def test_cancel_ack_survives_failed_poll_without_resending(tenant, make_run, monkeypatch):
    run_id = make_run()
    action_id = pending_remote(tenant, run_id)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.control(s, tenant, run_id, "cancel", run.version, "cancel remote")
    calls = AsyncMock(side_effect=[{}, TimeoutError(), {"taskId": "task-1", "status": "input_required"}])
    monkeypatch.setattr(remote, "rpc", calls)
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Outbox, tenant, "remote-cancel-" + action_id).status == "sent"
        assert db.get(s, db.Action, tenant, action_id).receipt["cancel_sent"]
    ready(tenant, run_id)
    await Worker(target_run=run_id).once(tenant)
    assert [c.args[1] for c in calls.await_args_list] == ["tasks/cancel", "tasks/get", "tasks/get"]
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "CANCELLING" and run.cancel_requested
        assert not run.state.get("input_required")


async def test_cancel_timeout_queries_terminal_without_repeating_control(tenant, make_run, monkeypatch):
    run_id = make_run()
    action_id = pending_remote(tenant, run_id)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.control(s, tenant, run_id, "cancel", run.version, "cancel remote")
    calls = AsyncMock(side_effect=[TimeoutError(), {"taskId": "task-1", "status": "cancelled"}])
    monkeypatch.setattr(remote, "rpc", calls)
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Outbox, tenant, "remote-cancel-" + action_id).status == "unknown"
    ready(tenant, run_id)
    await Worker(target_run=run_id).once(tenant)
    assert [c.args[1] for c in calls.await_args_list] == ["tasks/cancel", "tasks/get"]
    with db.transaction(tenant) as s:
        assert db.get(s, db.Outbox, tenant, "remote-cancel-" + action_id).status == "settled"
        assert db.get(s, db.Action, tenant, action_id).status == "CANCELLED"


async def test_interrupted_cancel_and_pause_never_repeat_or_resume(tenant, make_run, monkeypatch):
    run_id = make_run()
    action_id = pending_remote(tenant, run_id)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.pause_requested = True
        s.add(db.Outbox(tenant_id=tenant, id="remote-cancel-" + action_id, status="dispatching",
                       data={"kind": "cancel", "action_id": action_id}))
        db.emit(s, run, "TEST_PAUSE_INTENT", "Pause is concurrent with a late remote result")
    calls = AsyncMock(return_value={"taskId": "task-1", "status": "completed"})
    monkeypatch.setattr(remote, "rpc", calls)
    await Worker(target_run=run_id).once(tenant)
    assert calls.await_count == 1 and calls.call_args.args[1] == "tasks/get"
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).status == "PAUSED"


def test_business_key_cas_scope_schema_and_live_revocation(tenant, make_run):
    schema = {"type": "object", "properties": {"value": {"type": "string"}, "request_key": {"type": "string"},
                                                "expected_version": {"type": "string"}},
              "required": ["value", "request_key", "expected_version"], "additionalProperties": False}
    connection_id = registered(tenant, "irreversible", "publish", schema, idempotency_field="request_key", cas_field="expected_version")
    run_id = make_run(connections=[connection_id], capabilities=["external.write"])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        name = remote_contracts.tool_name(connection_id, "publish")
        catalog = remote_contracts.catalog(run.state)
        assert "request_key" not in catalog[name]["input_schema"]["properties"]
        with pytest.raises(Fault, match="schema"):
            service.prepare_action(s, run, ToolCall(tool=name, args={"value": "x"}), "1:0")
        action = service.prepare_action(s, run, ToolCall(tool=name, args={"value": "x", "expected_version": "remote-v1"}), "1:0")
        assert action.effect_class == "irreversible" and action.status == "WAITING_APPROVAL"
        assert action.args["business_key"] == action.args["arguments"]["request_key"]
        assert action.args["arguments"]["expected_version"] == "remote-v1"
        assert len(db.rows(s, db.Approval, tenant, action_id=action.id)) == 1
        db.get(s, db.ToolVersion, tenant, connection_id).status = "disabled"
        with pytest.raises(Fault, match="disabled"):
            remote.authorize_remote(s, run, action)
    unselected = make_run(capabilities=["external.write"])
    with db.transaction(tenant) as s:
        with pytest.raises(Fault, match="unselected"):
            remote.prepare_remote(s, db.get(s, db.Run, tenant, unselected), connection_id, "publish", {"value": "x"})


async def test_native_remote_intent_runs_through_worker_receipt_and_continuation(tenant, make_run, monkeypatch):
    connection_id = registered(tenant)
    run_id = make_run(model="configured", connections=[connection_id], capabilities=["external.read"])
    with db.transaction(tenant) as s:
        state = db.get(s, db.Run, tenant, run_id).state
    catalog = remote_contracts.catalog(state)
    request = model_protocol.build_request([{"role": "system", "content": "Scoped actions"}, {"role": "user", "content": "Read"}], state, catalog)
    name = remote_contracts.tool_name(connection_id, "lookup")
    wire = next(k for k, v in request["names"].items() if v == name)
    raw = {"id": "response-local", "status": "completed", "output": [
        {"type": "function_call", "name": wire, "call_id": "provider-call-1", "arguments": '{"value":"x"}'}]}
    content, metadata = model_protocol.normalize_response(raw, request)
    assert metadata["end_status"] == "completed"
    assert json.loads(content)["calls"][0]["provider_call_id"] == "provider-call-1"
    async def model(*args):
        return content, {"input_tokens": 1, "output_tokens": 1}, {**raw, "_forge": metadata}, "response-local"
    worker = Worker(model=model, target_run=run_id)
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        action = db.rows(s, db.Action, tenant, run_id=run_id)[0]
        action_id = action.id
        assert action.status == "READY" and not db.rows(s, db.Approval, tenant, action_id=action_id)
        assert db.get(s, db.Run, tenant, run_id).state["native_exchange"]["actions"][0]["action_id"] == action_id
    monkeypatch.setattr(remote, "dispatch_remote", AsyncMock(return_value={"exit_code": 0, "response": {"content": [{"type": "text", "text": "found"}]}}))
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        action = db.get(s, db.Action, tenant, action_id)
        assert action.status == "SUCCEEDED" and action.receipt["response"]["content"][0]["text"] == "found"
        stored = json.loads(objects.get(tenant, action.receipt["ref"]))
        assert stored["_observation"]["tool"] == "remote.call"
        assert service.replay(s, tenant, run_id)["projection"] == db.projection(db.get(s, db.Run, tenant, run_id))


def test_scoped_callback_tamper_duplicate_conflict_and_consumption(tenant, make_run, monkeypatch):
    connection_id = registered(tenant)
    monkeypatch.setattr(settings, "remote_credentials", json.dumps({"callback": {"tenant_id": tenant, "origin": "https://example.com",
                                                                                "callback_secret": "scoped-secret"}}))
    run_id = make_run()
    action_id = pending_remote(tenant, run_id)
    with db.transaction(tenant) as s:
        connection = db.get(s, db.ToolVersion, tenant, "test-provider") if s.get(db.ToolVersion, (tenant, "test-provider")) else None
        assert connection is None
        source = db.get(s, db.ToolVersion, tenant, connection_id)
        s.add(db.ToolVersion(tenant_id=tenant, id="test-provider", data={**source.data, "callback_key_ref": "callback"}))
    body = canonical({"action_id": action_id, "state": "completed"})
    with db.transaction(tenant) as s:
        timestamp = int(db.clock(s).timestamp())
        signature = remote.callback_signature("scoped-secret", tenant, "test-provider", "m-1", timestamp, body)
        with pytest.raises(Fault, match="signature"):
            remote.accept_callback(s, tenant, "test-provider", "m-2", timestamp, body, signature)
        remote.accept_callback(s, tenant, "test-provider", "m-1", timestamp, body, signature)
        changed = canonical({"action_id": action_id, "state": "failed"})
        other_sig = remote.callback_signature("scoped-secret", tenant, "test-provider", "m-1", timestamp, changed)
        with pytest.raises(Fault, match="different content"):
            remote.accept_callback(s, tenant, "test-provider", "m-1", timestamp, changed, other_sig)
        run = db.get(s, db.Run, tenant, run_id)
        remote.consume_inbox(s, run)
        remote.consume_inbox(s, run)
        assert run.status == "WAITING" and not run.state.get("verification")
        assert db.rows(s, db.Inbox, tenant)[0].status == "consumed"
        assert len([e for e in db.rows(s, db.Event, tenant, run_id=run_id) if e.type == "CALLBACK_CONSUMED"]) == 1


def test_credentials_cannot_cross_tenant_or_origin(tenant, monkeypatch):
    monkeypatch.setattr(settings, "remote_credentials", json.dumps({"auth": {"tenant_id": tenant, "origin": "https://example.com",
                                                                            "authorization": "Bearer test-token"}}))
    connection = {"tenant_id": tenant, "url": "https://example.com/mcp", "credential_ref": "auth"}
    assert remote_contracts.headers(connection) == {"Authorization": "Bearer test-token"}
    for changed in [{**connection, "tenant_id": "other"}, {**connection, "url": "https://other.example.com/mcp"}]:
        with pytest.raises(Fault, match="tenant/origin"):
            remote_contracts.headers(changed)


@pytest.fixture
def protocol_server(tmp_path):
    """Real TLS sockets with a test-owned certificate; protocol fixture, not external interop."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(hours=1)).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "server.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    requests, responses = [], {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, status=200):
            body = canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            requests.append(("GET", dict(self.headers), self.path))
            self.respond(responses[self.path])

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((data["method"], dict(self.headers), data))
            if "id" not in data:
                self.respond({}, 202)
                return
            value = responses[data["method"]]
            self.respond({"jsonrpc": "2.0", "id": "wrong-id" if responses.get("bad_id") else data["id"], "result": value})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", requests, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("kind,protocol", [("mcp", "2026-07-28"), ("mcp", "2025-11-25"), ("a2a", "0.3.0"), ("a2a", "1.0")])
async def test_four_adapter_negotiations_and_calls_over_local_tls(kind, protocol, protocol_server, monkeypatch):
    base, requests, responses = protocol_server
    monkeypatch.setattr(remote, "validate_url", AsyncMock(return_value="127.0.0.1"))
    # The test certificate and loopback exception exist only in this fixture.
    def test_client(**kwargs):
        kwargs.pop("follow_redirects", None)
        return httpx.AsyncClient(**kwargs, verify=False, follow_redirects=False, trust_env=False)
    monkeypatch.setattr(remote, "safe_client", test_client)
    monkeypatch.setattr(settings, "remote_credentials", json.dumps({"tls-test": {"tenant_id": "tls-test", "origin": base,
                                                                                "authorization": "Bearer local-fixture"}}))
    connection = {"tenant_id": "tls-test", "kind": kind, "protocol": protocol, "url": base + "/rpc", "credential_ref": "tls-test"}
    responses["server/discover"] = {"supportedVersions": [protocol], "capabilities": {"tools": {}}}
    responses["initialize"] = {"protocolVersion": protocol, "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
    responses["tools/list"] = {"tools": [{"name": "lookup", "inputSchema": SCHEMA}]}
    responses["tools/call"] = {"content": [{"type": "text", "text": "found"}], "isError": False}
    responses["/.well-known/agent-card.json"] = {"name": "fixture", "description": "local", "version": "1", "protocolVersion": protocol,
                                                "url": connection["url"], "supportedInterfaces": [{"url": connection["url"],
                                                "protocolBinding": "JSONRPC", "protocolVersion": protocol}], "capabilities": {},
                                                "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"], "skills": []}
    responses["SendMessage"] = {"task": {"id": "task-1", "status": {"state": "TASK_STATE_WORKING"}}}
    responses["message/send"] = {"kind": "task", "id": "task-1", "contextId": "context-1", "status": {"state": "working"}}
    result = await remote.discover(connection)
    assert result["protocol"] == protocol
    arguments = {"message": {"role": "user", "messageId": "m-1", "parts": [{"kind": "text", "text": "work"}]}} if kind == "a2a" else {"value": "x"}
    if protocol == "1.0":
        arguments = {"message": {"role": "ROLE_USER", "messageId": "m-1", "parts": [{"text": "work"}]}}
    dispatch_args = {"connection": connection, "operation": "SendMessage" if kind == "a2a" else "lookup", "arguments": arguments}
    if kind == "mcp":
        dispatch_args["contract"] = remote_contracts.RemoteTool(operation="lookup", effect="read", input_schema=SCHEMA).model_dump()
    receipt = await remote.dispatch_remote("tls-test", "run", "action", dispatch_args)
    assert receipt["exit_code"] == 0
    assert all(item[1].get("Authorization") == "Bearer local-fixture" for item in requests)
    if kind == "a2a":
        method = "SendMessage" if protocol == "1.0" else "message/send"
        sent = next(item[2]["params"]["message"] for item in requests if item[0] == method)
        assert sent["role"] == ("ROLE_USER" if protocol == "1.0" else "user")
        assert ("kind" in sent["parts"][0]) == (protocol == "0.3.0")
    if kind == "mcp" and protocol == "2026-07-28":
        assert all(item[2]["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == protocol for item in requests)
        assert not any(item[0] == "initialize" for item in requests)
        responses["bad_id"] = True
        with pytest.raises(Fault, match="match"):
            await remote.rpc(connection, "tools/list", {})


def test_discovery_review_download_and_untrusted_verification_boundary(client, tenant, make_run, monkeypatch):
    from forgeagent import api

    monkeypatch.setattr(remote, "validate_url", AsyncMock(return_value="93.184.216.34"))
    monkeypatch.setattr(api, "validate_url", remote.validate_url)
    connection = client.post("/v1/connections", json={"name": "reviewed", "kind": "mcp", "protocol": "2026-07-28",
                                                     "url": "https://example.com/mcp", "tools": [{"operation": "lookup", "effect": "read", "input_schema": SCHEMA}]})
    assert connection.status_code == 201 and connection.json()["status"] == "pending"
    connection_id = connection.json()["id"]
    with pytest.raises(Fault, match="Discover"):
        make_run(connections=[connection_id])
    monkeypatch.setattr(remote, "discover", AsyncMock(return_value={"tools": [{"name": "lookup", "inputSchema": SCHEMA}]}))
    assert client.post(f"/v1/connections/{connection_id}/discover").status_code == 200
    run_id = make_run(connections=[connection_id], capabilities=["external.read"])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = remote.prepare_remote(s, run, connection_id, "lookup", {"value": "x"})
        action.status = "SUCCEEDED"
        action.receipt = {"response": {"content": [{"type": "resource_link", "uri": "https://example.com/output"}]}}
        action_id, version = action.id, run.version
    monkeypatch.setattr(remote, "get_document", AsyncMock(return_value=b"remote artifact"))
    result = client.post(f"/v1/actions/{action_id}/artifacts", json={"expected_version": version, "index": 0,
                                                                  "expected_digest": digest(b"remote artifact")})
    assert result.status_code == 201, result.text
    with db.transaction(tenant) as s:
        artifact = db.get(s, db.Artifact, tenant, result.json()["id"])
        assert not artifact.verified and objects.get(tenant, artifact.ref) == b"remote artifact"
        assert db.get(s, db.Run, tenant, run_id).status != "SUCCEEDED"


def test_remote_api_idempotency_and_reconciliation_evidence(client, tenant, make_run, monkeypatch):
    schema = {"type": "object", "properties": {"value": {"type": "string"}, "request_key": {"type": "string"}},
              "required": ["value", "request_key"], "additionalProperties": False}
    connection_id = registered(tenant, "external_write", "publish", schema, idempotency_field="request_key",
                               reconcile_operation="lookup", reconcile_key_field="value")
    with db.transaction(tenant) as s:
        connection = db.get(s, db.ToolVersion, tenant, connection_id)
        connection.data = {**connection.data, "tools": [*connection.data["tools"],
            remote_contracts.RemoteTool(operation="lookup", effect="read", input_schema=SCHEMA).model_dump()]}
    run_id = make_run(connections=[connection_id], capabilities=["external.write"])
    with db.transaction(tenant) as s:
        version = db.get(s, db.Run, tenant, run_id).version
    body = {"expected_version": version, "connection_id": connection_id, "operation": "publish", "arguments": {"value": "content"}, "idempotency_key": "publish-one"}
    first = client.post(f"/v1/runs/{run_id}/remote-actions", json=body)
    second = client.post(f"/v1/runs/{run_id}/remote-actions", json=body)
    assert first.status_code == second.status_code == 202 and first.json()["id"] == second.json()["id"]
    conflict = client.post(f"/v1/runs/{run_id}/remote-actions", json={**body, "arguments": {"value": "other"}})
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    action_id = first.json()["id"]
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        db.get(s, db.Action, tenant, action_id).status = "UNKNOWN"
        db.emit(s, run, "TEST_UNKNOWN", "Simulate lost effect acknowledgement")
        version = run.version
    probe = AsyncMock(return_value={"exit_code": 0, "response": {"content": [{"type": "text", "text": "provider record"}]}})
    monkeypatch.setattr(remote, "dispatch_remote", probe)
    result = client.post(f"/v1/actions/{action_id}/reconcile-query", json={"expected_version": version})
    assert result.status_code == 200, result.text
    assert probe.call_args.args[3]["operation"] == "lookup"
    with db.transaction(tenant) as s:
        action = db.get(s, db.Action, tenant, action_id)
        assert action.status == "UNKNOWN" and action.receipt["reconciliation_query"]["ref"]
        assert probe.call_args.args[3]["arguments"] == {"value": action.args["business_key"]}


def test_paused_remote_handle_can_resume_polling_and_reviewer_input_is_required(client, tenant, make_run):
    from forgeagent.api import app
    from forgeagent.auth import Identity, identity

    run_id = make_run(capabilities=["external.write"])
    action_id = pending_remote(tenant, run_id)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.control(s, tenant, run_id, "pause", run.version, "pause polling")
        resumed = service.control(s, tenant, run_id, "resume", run.version, "resume the known remote handle")
        assert resumed.status == "QUEUED" and not resumed.pause_requested
        action = db.get(s, db.Action, tenant, action_id)
        action.receipt = {**action.receipt, "response": {"inputRequests": {"answer": {"method": "elicitation/create"}}}}
        auth = db.get(s, db.Authorization, tenant, "tester")
        auth.data = {**auth.data, "project_permissions": {}}
        version = run.version
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester", False)
    result = client.post(f"/v1/actions/{action_id}/input", json={"expected_version": version, "responses": {"answer": {"result": {"action": "decline"}}}})
    assert result.status_code == 403


def test_erased_task_cannot_republish_objects(client, tenant, make_run):
    from forgeagent import resources
    from test_knowledge import memory

    memory_id = memory(client)
    run_id = make_run()
    assert client.post(f"/v1/memories/{memory_id}/purge", json={"erase_derived_runs": True}).status_code == 200
    with pytest.raises(Fault, match="republished"):
        resources.put(tenant, run_id, b"late response")


def test_a2a_v1_rejects_legacy_role_and_ambiguous_parts():
    connection = {"kind": "a2a", "protocol": "1.0"}
    for message in [{"role": "user", "messageId": "m", "parts": [{"kind": "text", "text": "x"}]},
                    {"role": "ROLE_USER", "messageId": "m", "parts": [{"text": "x", "url": "https://example.com"}]}]:
        with pytest.raises(Fault):
            remote_contracts.validate_protocol_arguments(connection, {"message": message})
