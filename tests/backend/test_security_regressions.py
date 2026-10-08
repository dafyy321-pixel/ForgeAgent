import json
from datetime import timedelta

import pytest
from forgeagent import db, service
from forgeagent.api import app
from forgeagent.auth import Identity, identity
from forgeagent.domain import ApprovalDecision, Fault, uid
from forgeagent.storage import objects
from forgeagent.worker import Worker


@pytest.mark.parametrize("operation", ["pause", "resume", "cancel", "fork", "input", "verify", "bind-model", "reapprove", "recheck"])
def test_same_tenant_outsider_cannot_control(client, tenant, make_run, operation):
    run_id = make_run()
    app.dependency_overrides[identity] = lambda: Identity(tenant, "outsider")
    body = {"expected_version": 1, "content": "new input"} if operation == "input" else {"expected_version": 1}
    response = client.post(f"/v1/runs/{run_id}/{operation}", json=body, headers={"Idempotency-Key": uid()})
    assert response.status_code == 403


def test_same_tenant_resource_reads_and_explicit_sharing(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        artifact = db.Artifact(tenant_id=tenant, run_id=run_id, name="a", kind="summary",
                               ref=objects.put(tenant, run_id, b"private"))
        s.add(artifact)
        s.flush()
        artifact_id = artifact.id
    app.dependency_overrides[identity] = lambda: Identity(tenant, "outsider")
    for suffix in ["", "/actions", "/checkpoints", "/replay", "/events", "/budget", "/artifacts", "/context/1"]:
        assert client.get(f"/v1/runs/{run_id}{suffix}").status_code == 403
    assert client.get(f"/v1/artifacts/{artifact_id}/download").status_code == 403
    assert client.get("/v1/workspace").json()["runs"] == []
    assert client.get("/v1/runs").json()["items"] == []
    with db.transaction(tenant) as s:
        s.add(db.Authorization(tenant_id=tenant, id="outsider", data={
            "epoch": 1, "capabilities": [], "project_permissions": {"runtime-lab": ["read", "control"]}}))
    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["id"] == run_id
    assert client.post(f"/v1/runs/{run_id}/pause", json={"expected_version": detail["stateVersion"]}).status_code == 202
    with db.transaction(tenant) as s:
        auth = db.get(s, db.Authorization, tenant, "outsider")
        auth.data = {**auth.data, "project_permissions": {}}
    assert client.get(f"/v1/runs/{run_id}").status_code == 403


def test_approval_requires_reviewer_project_grant(tenant, make_run):
    run_id = make_run(capabilities=["external.write"])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = db.Action(tenant_id=tenant, run_id=run_id, logical_key="external", tool="remote.call", args={},
                           effect_class="external_write", effect_digest="effect", status="WAITING_APPROVAL")
        s.add(action)
        s.flush()
        approval = db.Approval(tenant_id=tenant, action_id=action.id, effect_digest="effect", policy_epoch=1,
                              resource_version="1", expires_at=db.clock(s) + timedelta(hours=1))
        s.add(approval)
        s.flush()
        body = ApprovalDecision(expected_version=run.version, decision="approve", effect_digest="effect")
        with pytest.raises(Fault, match="project permission"):
            service.approve(s, tenant, "outsider", approval.id, body)
        service.approve(s, tenant, "administrator", approval.id, body, admin=True)
        assert approval.reviewer == "administrator"


def test_hidden_tests_are_not_in_snapshots_or_public_legacy_projection(client, tenant, make_run):
    secret = "SECRET_ORACLE"
    with db.transaction(tenant) as s:
        project = db.get(s, db.Project, tenant, "runtime-lab")
        project.data = {**project.data, "acceptance": {**project.data["acceptance"],
                        "protected_tests": {"hidden/test.py": secret}}}
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert secret not in json.dumps(run.state)
        assert service.resolve_acceptance(tenant, run.state["acceptance"])["protected_tests"]["hidden/test.py"] == secret
        # Historical checkpoints/events written by older versions must also be filtered.
        run.state = {**run.state, "acceptance": {"id": "old", "protected_tests": {"x": secret}}}
        service.checkpoint(s, run)
    app.dependency_overrides[identity] = lambda: Identity(tenant, "tester")
    for path in ["/projects", "/workspace", f"/runs/{run_id}/checkpoints", f"/runs/{run_id}/replay"]:
        response = client.get("/v1" + path)
        assert response.status_code == 200
        assert secret not in response.text
        assert "protected_tests" not in response.text


@pytest.mark.parametrize("amount,tokens", [(100, 0), (0, 200)])
async def test_manual_verification_and_worker_cannot_bypass_unknown_usage(client, tenant, make_run, amount, tokens):
    run_id = make_run()
    call_id = uid()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.reserve(s, run, call_id, amount, tokens)
        service.settle(s, run, call_id, None)
        s.add(db.ModelCall(tenant_id=tenant, id=call_id, run_id=run_id, status="UNKNOWN", data={}))
        run.status = "PAUSED"
        version = run.version
    response = client.post(f"/v1/runs/{run_id}/verify", json={"expected_version": version})
    assert response.status_code == 409 and response.json()["code"] == "UNKNOWN_USAGE"
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "QUEUED"
        run.state = {**run.state, "completion": "try to finish"}
    worker = Worker()
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).status == "PAUSED"
        assert not db.rows(s, db.Artifact, tenant, run_id=run_id)


async def test_cancel_takeover_billing_reconciliation_wakes_worker(client, tenant, make_run):
    run_id, call_id = make_run(), uid()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.reserve(s, run, call_id, 100, 10)
        s.add(db.ModelCall(tenant_id=tenant, id=call_id, run_id=run_id, status="DISPATCHED", data={}))
        service.control(s, tenant, run_id, "cancel", run.version, "cancel")
    worker = Worker()
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "CANCELLING" and run.wait_reason == "RECONCILIATION"
        assert db.get(s, db.Job, tenant, run_id).status == "READY"
        version = run.version
    response = client.post(f"/v1/runs/{run_id}/budget/{call_id}/reconcile", json={
        "expected_version": version, "actual_micros": 20, "actual_tokens": 5,
        "evidence": "provider confirms the original request bill"})
    assert response.status_code == 200
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).status == "CANCELLED"
        account = db.get(s, db.BudgetAccount, tenant, run_id)
        assert account.reserved == 0 and account.resources["tokens_reserved"] == 0


def test_settings_compare_and_swap(client):
    first = client.get("/v1/workspace").json()
    body = {**first["settings"], "budget": 21, "expected_revision": first["settings_revision"]}
    assert client.put("/v1/settings", json=body).status_code == 200
    assert client.put("/v1/settings", json={**body, "budget": 50}).status_code == 409
    assert client.get("/v1/workspace").json()["settings"]["budget"] == 21


def test_create_idempotency_key_is_scoped_to_actor(client, tenant):
    from forgeagent.domain import CAPABILITIES

    key = uid()
    body = {"project_id": "runtime-lab", "model": "fixture", "task": {"goal": "Fix add"}}
    first = client.post("/v1/runs", json=body, headers={"Idempotency-Key": key})
    with db.transaction(tenant) as s:
        s.add(db.Authorization(tenant_id=tenant, id="other", data={"epoch": 1, "capabilities": sorted(CAPABILITIES),
              "project_permissions": {"runtime-lab": ["create", "read"]}}))
    app.dependency_overrides[identity] = lambda: Identity(tenant, "other")
    second = client.post("/v1/runs", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert client.post("/v1/runs", json=body, headers={"Idempotency-Key": key}).json()["id"] == second.json()["id"]


async def test_repeated_cancel_repairs_paused_intent_and_journals_it(tenant, make_run):
    run_id = make_run()
    worker = Worker()
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status, run.cancel_requested = "PAUSED", True
        db.emit(s, run, "LEGACY_CANCEL_STATE", "Reproduce an interrupted legacy cancellation")
        service.control(s, tenant, run_id, "cancel", 999, "repeat cancellation")
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).status == "CANCELLED"
