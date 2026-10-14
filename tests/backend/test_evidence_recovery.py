"""Real PostgreSQL recovery boundaries; fixture execution is not model effectiveness evidence."""

import pytest
from forgeagent import db, diagnosis, knowledge_erasure, recovery, service
from forgeagent.domain import Budget, Harness, ToolCall, digest, uid
from forgeagent.reducer import rebuild
from forgeagent.worker import Worker
from sqlalchemy.exc import DBAPIError


async def test_failed_acceptance_survives_worker_change_and_requires_public_probe(tenant, make_run, client):
    run_id = make_run(harness=Harness(evidence_recovery=True))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "completion": "premature"}
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.phase == "DIAGNOSING" and run.state["diagnosis_pending"]
        assert run.state["repair_attempts"] == 0
    worker = Worker()
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.phase == "RECOVERING"
        assert run.state["diagnosis_summary"]["root_class"] == "unknown"
        assert run.state["diagnosis_summary"]["conclusion"] == "hypothesis"
        args = {"path": "src/calculator.py", "content": "wrong", "expected_digest": "absent"}
        blocked = service.prepare_action(s, run, ToolCall(tool="repo.write", args=args), "blocked")
        assert blocked.status == "FAILED" and "Inspect public" in blocked.receipt["error"]
        service.prepare_action(s, run, ToolCall(tool="repo.read", args={"path": "src/calculator.py"}), "probe")
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert not run.state["recovery_guard"]["requires_probe"]
        service.prepare_action(s, run, ToolCall(tool="repo.write", args={"path": "src/calculator.py",
            "content": "def add(left, right):\n    return left + right\n",
            "expected_digest": digest(b"def add(left, right):\n    return left - right\n")}), "fix")
    for _ in range(4):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "SUCCEEDED"
        assert db.rows(s, db.RecoveryAttempt, tenant, run_id=run_id)[0].status == "SUCCEEDED"
        assert rebuild(s, tenant, run_id) == db.projection(run)
    response = client.get(f"/v1/runs/{run_id}/recovery?kind=diagnoses&limit=1")
    assert response.status_code == 200 and len(response.json()["items"]) == 1


@pytest.mark.parametrize("reason", ["truncated", "timed_out", "unregistered_exit", "not_run"])
def test_inconclusive_retains_unknown_without_business_edits(tenant, make_run, reason):
    run_id = make_run(harness=Harness(evidence_recovery=True))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        workspace = run.state["workspace_digest"]
        diagnosis.queue(s, run, "verification", None, {"verdict": "INCONCLUSIVE", "inconclusive_reason": reason})
        recovery.advance(s, run)
        assert run.status == "PAUSED" and run.state["workspace_digest"] == workspace
        assert run.state["diagnosis_summary"]["root_class"] == "unknown"
        assert not db.rows(s, db.Action, tenant, run_id=run_id)


def test_environment_retries_same_acceptance_once_then_stops_repeated_failure(tenant, make_run):
    run_id = make_run(harness=Harness(evidence_recovery=True))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        fixed = diagnosis.binding(run)
        facts = {"verdict": "INCONCLUSIVE", "error_code": "SANDBOX_UNAVAILABLE"}
        run.state = {**run.state, "completion": "unchanged artifact"}
        diagnosis.queue(s, run, "verification", None, facts)
        recovery.advance(s, run)
        assert run.state["completion"] == "unchanged artifact"
        assert run.state["recoveries_used"] == 1 and diagnosis.binding(run) == fixed
        diagnosis.queue(s, run, "verification", None, facts)
        recovery.advance(s, run)
        assert run.status == "PAUSED" and "Same failure" in run.state["reason"]
        assert db.rows(s, db.RecoveryAttempt, tenant, run_id=run_id)[0].status == "FAILED"


def test_stale_binding_and_zero_repair_budget_do_not_execute(tenant, make_run):
    for stale in (False, True):
        run_id = make_run(harness=Harness(evidence_recovery=True), budget=Budget(max_repair_attempts=0))
        with db.transaction(tenant) as s:
            run = db.get(s, db.Run, tenant, run_id)
            diagnosis.queue(s, run, "verification", None, {"verdict": "FAIL"})
            if stale:
                run.state = {**run.state, "input_revision": 1}
            recovery.advance(s, run)
            assert run.status == "PAUSED" and not db.rows(s, db.RecoveryAttempt, tenant, run_id=run_id)


def test_record_integrity_scope_and_registered_erasure(tenant, make_run):
    one, two = make_run(), make_run()
    with db.transaction(tenant) as s:
        row = diagnosis.evidence(s, db.get(s, db.Run, tenant, one), "tool", None, {})
        evidence_id = row.id
    with pytest.raises(DBAPIError), db.transaction(tenant) as s:
        db.get(s, db.EvidenceRecord, tenant, evidence_id).data = {"tampered": True}
    with pytest.raises(DBAPIError), db.transaction(tenant) as s:
        s.add(db.Diagnosis(tenant_id=tenant, run_id=two, data={"evidence_ids": [evidence_id]}))
    with db.transaction(tenant) as s:
        knowledge_erasure.prepare(s, tenant, None, "tester", run_id=one)
        assert not db.rows(s, db.EvidenceRecord, tenant, run_id=one)


def test_diagnosis_budget_and_control_are_monotonic(tenant, make_run):
    run_id = make_run(harness=Harness(evidence_recovery=True), budget=Budget(max_diagnoses=0))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        diagnosis.queue(s, run, "verification", None, {"verdict": "FAIL"})
        recovery.advance(s, run)
        assert run.status == "PAUSED" and "budget exhausted" in run.state["reason"]
        run.cancel_requested, run.status = True, "CANCELLING"
        diagnosis.queue(s, run, "tool", None, {"error_code": "SANDBOX_UNAVAILABLE"})
        assert run.status == "CANCELLING" and not run.state.get("diagnosis_pending")


def test_recovery_read_api_is_scoped_and_paginated(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        for _ in range(3):
            diagnosis.evidence(s, run, "tool", None, {"value": uid()})
    first = client.get(f"/v1/runs/{run_id}/recovery?kind=evidence&limit=2").json()
    second = client.get(f"/v1/runs/{run_id}/recovery?kind=evidence&limit=2&cursor={first['next_cursor']}").json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert not {x["id"] for x in first["items"]} & {x["id"] for x in second["items"]}
    assert client.get(f"/v1/runs/{run_id}/recovery?kind=attempts&cursor={first['next_cursor']}").status_code == 422


def test_new_input_supersedes_recovery_without_resetting_budget(client, tenant, make_run):
    run_id = make_run(harness=Harness(evidence_recovery=True))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        diagnosis.queue(s, run, "verification", None, {"verdict": "FAIL"})
        recovery.advance(s, run)
        intent_id = run.state["active_recovery"]
        run.status = "PAUSED"
        db.emit(s, run, "TEST_PAUSED", "Input boundary")
        version = run.version
    result = client.post(f"/v1/runs/{run_id}/input", json={"expected_version": version, "content": "Inspect the public tests"})
    assert result.status_code == 200
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert not run.state["active_recovery"] and not run.state["recovery_guard"]
        assert run.state["repair_attempts"] == 1
        assert db.get(s, db.RecoveryAttempt, tenant, intent_id).status == "SUPERSEDED"


async def test_unknown_effect_blocks_diagnostic_recovery(tenant, make_run):
    run_id = make_run(harness=Harness(evidence_recovery=True))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        diagnosis.queue(s, run, "verification", None, {"verdict": "FAIL"})
        s.add(db.Action(tenant_id=tenant, run_id=run_id, logical_key="uncertain", tool="external",
            args={}, effect_class="external_write", effect_digest=digest({}), status="UNKNOWN"))
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.wait_reason == "RECONCILIATION"
        assert not db.rows(s, db.RecoveryAttempt, tenant, run_id=run_id)
