import json

import pytest
from forgeagent import db, service
from forgeagent.domain import Budget, digest, uid
from forgeagent.sandbox import sandbox
from forgeagent.storage import objects
from forgeagent.worker import Worker


@pytest.mark.parametrize("baseline,current", [
    ({}, {"empty.txt": ""}), ({"empty.txt": ""}, {}),
    ({"a.txt": "before"}, {"a.txt": "after"}),
    ({"空 格.txt": "line\r\n"}, {"空 格.txt": "line\nnext"}),
    ({"path": "old"}, {"path/file": "new"}),
    ({"a.txt": "same"}, {"a.txt": "same"}),
])
def test_deliverable_git_patch_roundtrip(baseline, current):
    patch = sandbox.patch(baseline, current)
    assert sandbox.check_patch(baseline, current, patch)["status"] == "passed"


def test_delivery_rejects_patch_that_does_not_reproduce_tree():
    with pytest.raises(Exception, match="does not reproduce"):
        sandbox.check_patch({}, {"missing.txt": ""}, "")


def test_restore_removes_residue_and_failed_stage_preserves_tree(tenant):
    run_id = uid()
    root = sandbox.restore(tenant, run_id, 1, {"keep": "correct", "old": "remove"})
    sandbox.restore(tenant, run_id, 1, {"keep": "correct"})
    assert not (root / "old").exists()
    with pytest.raises(Exception):
        sandbox.restore(tenant, run_id, 1, {"../bad": "no"})
    assert (root / "keep").read_text() == "correct"
    assert not list(root.parent.glob("*.stage-*"))


async def test_acceptance_failure_runs_bounded_feedback_repair(tenant, make_run):
    run_id = make_run(budget=Budget(max_repair_attempts=1))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "completion": "premature completion"}
    worker = Worker()
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "ACTIVE" and run.phase == "REPAIRING"
        assert run.state["repair_attempts"] == 1 and not run.state["completion"]
        assert run.state["verification_feedback"]["verdict"] == "FAIL"
    for _ in range(4):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "SUCCEEDED"
        assert run.state["verification"]["checks"][-1]["name"] == "交付补丁可应用"


async def test_repair_attempt_limit_is_durable(tenant, make_run):
    run_id = make_run(budget=Budget(max_repair_attempts=1))
    worker = Worker()
    for _ in range(2):
        with db.transaction(tenant) as s:
            run = db.get(s, db.Run, tenant, run_id)
            run.state = {**run.state, "completion": "still wrong"}
            db.emit(s, run, "TEST_COMPLETION", "Repeat a failed completion for the bounded retry check")
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "FAILED" and run.state["repair_attempts"] == 1


async def test_new_input_invalidates_completion_and_is_consumed(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "PAUSED"
        run.state = {**run.state, "completion": "old completion"}
        version = run.version
    response = client.post(f"/v1/runs/{run_id}/input", json={"expected_version": version, "content": "NEW_REQUIREMENT"})
    assert response.status_code == 200
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.state["completion"] is None and run.state["input_revision"] == 1
        service.control(s, tenant, run_id, "resume", run.version, "continue")
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.ModelCall, tenant, run_id=run_id)) == 1
        context = db.rows(s, db.ContextManifest, tenant, run_id=run_id)[0]
        assert "NEW_REQUIREMENT" in json.dumps(context.data)
        assert not db.rows(s, db.VerificationResult, tenant, run_id=run_id)


def test_resume_checkpoint_is_explicitly_fork_only(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "PAUSED"
        version, checkpoint = run.version, run.state["checkpoint_id"]
        original = objects.get(tenant, run.state["workspace_ref"])
    response = client.post(f"/v1/runs/{run_id}/resume", json={"expected_version": version, "checkpoint_id": checkpoint})
    assert response.status_code == 422 and response.json()["code"] == "CHECKPOINT_FORK_REQUIRED"
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert digest(objects.get(tenant, run.state["workspace_ref"])) == digest(original)
