"""Regressions for the engineering review, using PostgreSQL row locks and real workers."""

import threading
from concurrent.futures import ThreadPoolExecutor

from forgeagent import db, service
from forgeagent.domain import CreateRun, Task, uid
from forgeagent.reducer import rebuild
from forgeagent.storage import objects
from forgeagent.worker import Worker
from sqlalchemy import text


def test_confirmation_binds_reviewed_plan_even_with_current_version(tenant, make_run, client):
    run_id = make_run(model="configured", capabilities=["repo.read"], task=Task(mode="discuss", goal="Review"))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "PAUSED"
        run.state = {**run.state, "plan_ready": True}
        service.append_message(s, run, "assistant", "Plan A")
        db.emit(s, run, "PLAN_A", "ready")
    reviewed = client.get(f"/v1/runs/{run_id}/conversation").json()
    assert reviewed.get("plan_digest"), "Confirmation requires a digest of the displayed plan"
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "dialogue": []}
        service.append_message(s, run, "assistant", "Plan B")
        db.emit(s, run, "PLAN_B", "updated")
        current = run.version
    body = {"expected_version": current, "plan_digest": reviewed["plan_digest"],
            "spec": {"project_id": "runtime-lab", "model": "fixture", "task": {"goal": "Execute"}}}
    response = client.post(f"/v1/runs/{run_id}/execute", json=body, headers={"Idempotency-Key": uid()})
    assert response.status_code == 409 and response.json()["code"] == "PLAN_CONFLICT"


async def test_revoked_identity_stops_existing_worker_before_writes(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        before = db.get(s, db.Run, tenant, run_id).state["workspace_ref"]
        db.get(s, db.Authorization, tenant, "tester").status = "revoked"
    worker = Worker(target_run=run_id)
    for _ in range(4):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "PAUSED"
        assert "PERMISSION_REVOKED" in run.state["reason"]
        assert objects.get(tenant, run.state["workspace_ref"]) == objects.get(tenant, before)
        assert not any(a.status == "SUCCEEDED" for a in db.rows(s, db.Action, tenant, run_id=run_id))


def test_parent_cancel_and_child_event_commit_without_deadlock_or_lost_state(tenant, make_run, monkeypatch):
    parent_id = make_run(capabilities=["repo.read", "delegate"])
    with db.transaction(tenant) as s:
        parent = db.get(s, db.Run, tenant, parent_id)
        child_id = service.create_run(s, tenant, "tester", CreateRun(
            project_id="runtime-lab", model="fixture", capabilities=["repo.read"],
            task=Task(goal="child", allowed_paths=["src"])), uid(), parent=parent).id
        parent_version = parent.version
    child_locked, parent_locked = threading.Event(), threading.Event()
    original_get = db.get

    def observe_parent(s, cls, scope, id, lock=False):
        row = original_get(s, cls, scope, id, lock)
        if cls is db.Run and id == parent_id and lock:
            parent_locked.set()
        return row

    monkeypatch.setattr(db, "get", observe_parent)

    def child_commit():
        with db.transaction(tenant) as s:
            s.execute(text("SET LOCAL lock_timeout = '5s'"))
            child = db.get(s, db.Run, tenant, child_id, True)
            child_locked.set()
            assert parent_locked.wait(5)
            child.state = {**child.state, "review_child_progress": "committed"}
            db.emit(s, child, "REVIEW_CHILD_PROGRESS", "Child progress while parent cancellation is pending")

    def cancel_parent():
        assert child_locked.wait(5)
        with db.transaction(tenant) as s:
            s.execute(text("SET LOCAL lock_timeout = '5s'"))
            service.control(s, tenant, parent_id, "cancel", parent_version, "User cancellation")

    with ThreadPoolExecutor(2) as pool:
        child_future = pool.submit(child_commit)
        parent_future = pool.submit(cancel_parent)
        child_future.result(timeout=15)
        parent_future.result(timeout=15)
    with db.transaction(tenant) as s:
        parent = db.get(s, db.Run, tenant, parent_id)
        child = db.get(s, db.Run, tenant, child_id)
        assert parent.cancel_requested and child.cancel_requested
        assert child.state["review_child_progress"] == "committed"
        assert rebuild(s, tenant, child_id) == db.projection(child)


def test_user_status_does_not_mix_failure_reason_with_machine_phase(tenant, make_run, client):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status, run.phase = "PAUSED", "PLANNING"
        run.state = {**run.state, "reason": "MODEL_NOT_CONFIGURED: Configure model credentials and ID, then resume"}
    view = client.get(f"/v1/runs/{run_id}").json()
    assert view["phase"] == "PLANNING"
    assert view["reason_code"] == "MODEL_NOT_CONFIGURED"
    assert "尚未配置模型" in view["user_message"] and view["next_action"]["href"] == "/settings"
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status, run.phase = "SUCCEEDED", "VERIFYING"
    assert client.get(f"/v1/runs/{run_id}").json()["user_message"] == "验收已通过"


def test_create_key_cannot_attach_an_unrelated_task_to_a_plan(tenant, make_run, client):
    run_id = make_run(model="configured", capabilities=["repo.read"], task=Task(mode="discuss", goal="Review"))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "PAUSED"
        run.state = {**run.state, "plan_ready": True}
        db.emit(s, run, "TEST_PLAN", "ready")
    plan = client.get(f"/v1/runs/{run_id}/conversation").json()
    spec = {"project_id": "runtime-lab", "model": "fixture", "task": {"goal": "Execute"}}
    headers = {"Idempotency-Key": uid()}
    assert client.post('/v1/runs', json=spec, headers=headers).status_code == 202
    response = client.post(f"/v1/runs/{run_id}/execute", json={"expected_version": plan["version"],
        "plan_digest": plan["plan_digest"], "spec": spec}, headers=headers)
    assert response.status_code == 409 and response.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_create_and_fork_with_same_key_finish_without_advisory_deadlock(tenant, make_run, client):
    source = make_run()
    version = client.get(f"/v1/runs/{source}").json()["stateVersion"]
    for _ in range(3):
        start = threading.Barrier(2)
        headers = {"Idempotency-Key": uid()}

        def ordinary():
            start.wait(timeout=5)
            return client.post('/v1/runs', json={"project_id": "runtime-lab", "model": "fixture",
                "task": {"goal": "Concurrent create"}}, headers=headers)

        def fork():
            start.wait(timeout=5)
            return client.post(f'/v1/runs/{source}/fork', json={"expected_version": version}, headers=headers)

        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(ordinary), pool.submit(fork)
            results = [a.result(timeout=15), b.result(timeout=15)]
        assert sorted(r.status_code for r in results) == [202, 409]
        assert next(r for r in results if r.status_code == 409).json()["code"] == "IDEMPOTENCY_CONFLICT"
