import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from forgeagent import concurrency, db, progress, semantic, service
from forgeagent.config import settings
from forgeagent.domain import Budget, ChildContract, Control, CreateRun, Fault, Task, ToolCall, digest, uid
from forgeagent.reducer import rebuild
from forgeagent.sandbox import files, sandbox
from forgeagent.storage import objects
from forgeagent.worker import Worker


def child_run(s, tenant, parent, **kwargs):
    payload = {"project_id": "runtime-lab", "model": "fixture", "capabilities": ["repo.read"],
               "task": Task(goal="child", allowed_paths=["src"]),
               "budget": Budget(max_cost_usd="1", max_tokens=10)}
    payload.update(kwargs)
    return service.create_run(s, tenant, "tester", CreateRun(**payload), uid(), parent=parent)


def test_provider_admission_shared_between_worker_instances(monkeypatch):
    monkeypatch.setattr(settings, "provider_concurrency", 2)
    with ThreadPoolExecutor(8) as pool:
        acquired = list(pool.map(lambda _: concurrency.acquire_provider(), range(8)))
    leases = [x for x in acquired if x]
    try:
        assert len(leases) == 2
        assert concurrency.acquire_provider() is None
    finally:
        for lease in leases:
            concurrency.release_provider(lease)
    lease = concurrency.acquire_provider()
    assert lease
    concurrency.release_provider(lease)


async def test_slow_tenant_cannot_occupy_fast_tenant_slots(monkeypatch):
    monkeypatch.setattr(settings, "worker_slots", 4)
    worker = Worker()
    worker.tenants = lambda: ["slow", "fast"]
    running, maxima, completed = {"slow": 0, "fast": 0}, {"slow": 0, "fast": 0}, {"slow": 0, "fast": 0}
    slow_gate = asyncio.Event()

    async def once(tenant):
        running[tenant] += 1
        maxima[tenant] = max(maxima[tenant], running[tenant])
        if tenant == "slow":
            await slow_gate.wait()
        else:
            await asyncio.sleep(0.01)
            completed[tenant] += 1
            if completed[tenant] >= 20:
                worker.stopping = True
                slow_gate.set()
        assert sum(running.values()) <= 4
        running[tenant] -= 1
        return True

    worker.once = once
    await asyncio.wait_for(worker.run(), 5)
    assert completed["fast"] >= 20 and maxima["slow"] <= 2 and maxima["fast"] <= 2
    assert not any(running.values())


async def test_twenty_active_runs_finish_under_bounded_fair_scheduler(tenant, monkeypatch):
    other = "fair-" + uid()
    service.provision(other, "tester")
    run_ids = {}
    for scope in [tenant, other]:
        with db.transaction(scope) as s:
            run_ids[scope] = [service.create_run(s, scope, "tester", CreateRun(project_id="runtime-lab",
                model="fixture", task=Task(goal=f"queued fixture {i}", allowed_paths=["src"])), uid()).id for i in range(10)]
    monkeypatch.setattr(settings, "worker_slots", 4)
    worker = Worker()
    worker.tenants = lambda: [tenant, other]

    def snapshot():
        result, leased = {}, 0
        for scope in run_ids:
            with db.transaction(scope) as s:
                runs = [db.get(s, db.Run, scope, id) for id in run_ids[scope]]
                result[scope] = sum(r.status == "SUCCEEDED" for r in runs)
                active = sum(bool(r.lease_until and r.lease_until > db.clock(s)) for r in runs)
                assert active <= 2
                leased += active
        assert leased <= 4
        return result

    task = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(45):
            while True:
                counts = await asyncio.to_thread(snapshot)
                if all(n == 10 for n in counts.values()):
                    break
                await asyncio.sleep(0.05)
    finally:
        worker.stopping = True
        await task
    assert sum(counts.values()) == 20


async def test_cancelled_admission_releases_shared_slot():
    async with concurrency.admission("cancellation-test", 1) as admitted:
        assert admitted
        assert concurrency.acquire("cancellation-test", 1) is None
    ready = asyncio.Event()

    async def hold():
        async with concurrency.admission("cancellation-test", 1) as admitted:
            assert admitted
            ready.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(hold())
    await ready.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    lease = concurrency.acquire("cancellation-test", 1)
    assert lease
    concurrency.release_provider(lease)


def test_root_limit_keeps_other_roots_schedulable(tenant, make_run):
    root_id = make_run(capabilities=["repo.read", "delegate"], budget=Budget(max_concurrent_runs=1))
    other_id = make_run()
    with db.transaction(tenant) as s:
        policy = db.get(s, db.PolicyVersion, tenant, "settings")
        policy.data = {**policy.data, "concurrency": 4}
        child_run(s, tenant, db.get(s, db.Run, tenant, root_id))
    worker = Worker()
    assert worker.claim(tenant)[0] == root_id
    assert Worker().claim(tenant)[0] == other_id
    assert Worker().claim(tenant) is None


def test_child_allocations_hold_cost_and_tokens_then_release(tenant, make_run):
    root_id = make_run(capabilities=["repo.read", "delegate"], budget=Budget(max_cost_usd="4", max_tokens=30))
    with db.transaction(tenant) as s:
        root = db.get(s, db.Run, tenant, root_id)
        child = child_run(s, tenant, root)
        child_id = child.id
        with pytest.raises(Fault, match="Root token"):
            service.reserve(s, root, "too-many", 0, 21)
        with pytest.raises(Fault, match="Child token"):
            service.reserve(s, child, "child-too-many", 0, 11)
        service.reserve(s, child, "child-call", 200000, 8)
        s.flush()
        service.settle(s, child, "child-call", 100000, 3)
        account = db.get(s, db.BudgetAccount, tenant, root_id)
        assert service.allocation_holds(s, tenant, account) == (900000, 7)
        child.status = "FAILED"
        db.emit(s, child, "TEST_TERMINAL", "Failed optional work")
        service.release_child_allocation(s, child)
        assert service.allocation_holds(s, tenant, account) == (0, 0)
        assert account.resources["child_allocations"][child_id]["released"]
        service.reserve(s, root, "returned-capacity", 3000000, 27)


def test_concurrent_child_allocation_does_not_oversell(tenant, make_run):
    root_id = make_run(capabilities=["repo.read", "delegate"], budget=Budget(max_cost_usd="1", max_tokens=10))

    def allocate(_):
        try:
            with db.transaction(tenant) as s:
                service.allocate_child(s, db.get(s, db.Run, tenant, root_id), uid(), Budget(max_cost_usd="0.6", max_tokens=6))
            return True
        except Fault as exc:
            assert exc.code == "CHILD_ALLOCATION_LIMIT"
            return False

    with ThreadPoolExecutor(6) as pool:
        assert sum(pool.map(allocate, range(6))) == 1


def test_optional_failure_and_required_delivery_contracts(tenant, make_run):
    root_id = make_run(capabilities=["repo.read", "delegate"])
    with db.transaction(tenant) as s:
        root = db.get(s, db.Run, tenant, root_id)
        optional = child_run(s, tenant, root, child_contract=ChildContract(required=False))
        optional.status = "FAILED"
        db.emit(s, optional, "TEST_TERMINAL", "Optional failure")
        assert service.join_children(s, root, [optional]) == (False, False)
        service.require_finalizable(s, root)
        required = child_run(s, tenant, root, child_contract=ChildContract(join="integrate"))
        required.status = "SUCCEEDED"
        db.emit(s, required, "TEST_TERMINAL", "Missing delivery")
        assert service.join_children(s, root, [required]) == (False, True)
        with pytest.raises(Fault, match="required children"):
            service.require_finalizable(s, root)
        s.add(db.Artifact(tenant_id=tenant, run_id=required.id, kind="summary", name="summary.md",
                          ref=objects.put(tenant, required.id, b"summary"), verified=True, version=1))
        s.flush()
        with pytest.raises(Fault, match="integrated"):
            service.require_finalizable(s, root)
        root.state = {**root.state, "integrated_children": [required.id]}
        db.emit(s, root, "TEST_INTEGRATION", "Joined required work")
        service.require_finalizable(s, root)


async def test_optional_children_cancel_and_settle_before_parent_success(tenant, make_run):
    root_id = make_run(capabilities=["repo.read", "workspace.write", "delegate"])
    with db.transaction(tenant) as s:
        root = db.get(s, db.Run, tenant, root_id)
        optional = child_run(s, tenant, root, child_contract=ChildContract(required=False))
        optional_id = optional.id
        current = {"src/calculator.py": "def add(left, right):\n    return left + right\n"}
        root.state = {**root.state, "workspace_ref": objects.put(tenant, root.id, current),
                      "workspace_digest": digest(current), "completion": "Ready"}
        db.emit(s, root, "TEST_COMPLETION", "Parent completion")
    parent_worker = Worker(target_run=root_id)
    await parent_worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, root_id).status != "SUCCEEDED"
        assert db.get(s, db.Run, tenant, optional_id).cancel_requested
    await Worker(target_run=optional_id).once(tenant)
    with db.transaction(tenant) as s:
        root = db.get(s, db.Run, tenant, root_id)
        db.schedule(s, root)
    await parent_worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, optional_id).status == "CANCELLED"
        assert db.get(s, db.Run, tenant, root_id).status == "SUCCEEDED"


async def test_child_deadline_cancels_without_new_model_call(tenant, make_run):
    root_id = make_run(capabilities=["repo.read", "delegate"])
    with db.transaction(tenant) as s:
        root = db.get(s, db.Run, tenant, root_id)
        child = child_run(s, tenant, root, child_contract=ChildContract(deadline_seconds=1))
        child_id = child.id
        child.state = {**child.state, "deadline": (db.clock(s) - timedelta(seconds=1)).isoformat()}
        db.emit(s, child, "TEST_DEADLINE", "Expired child")
    await Worker(target_run=child_id).once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, child_id).status == "CANCELLED"
        assert not db.rows(s, db.ModelCall, tenant, run_id=child_id)


async def test_worker_blocking_io_never_runs_on_event_loop(tenant, make_run, monkeypatch):
    run_id = make_run()
    event_thread = threading.get_ident()
    original_transaction, original_get = db.transaction, objects.get
    calls = []

    def transaction(*args):
        assert threading.get_ident() != event_thread
        return original_transaction(*args)

    def slow_get(*args):
        assert threading.get_ident() != event_thread
        calls.append(True)
        time.sleep(0.05)
        return original_get(*args)

    monkeypatch.setattr(db, "transaction", transaction)
    monkeypatch.setattr(objects, "get", slow_get)
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    try:
        worker = Worker(target_run=run_id)
        for _ in range(4):
            await worker.once(tenant)
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
    assert calls and ticks >= 20


async def test_slow_object_store_does_not_expire_heartbeat(tenant, make_run, monkeypatch):
    run_id = make_run()
    monkeypatch.setattr(settings, "lease_seconds", 3)
    original = objects.get

    def slow_get(*args):
        time.sleep(1.2)
        return original(*args)

    monkeypatch.setattr(objects, "get", slow_get)
    worker = Worker(target_run=run_id)
    task = asyncio.create_task(worker.once(tenant))
    await asyncio.sleep(3.4)
    assert await asyncio.to_thread(Worker(target_run=run_id).claim, tenant) is None
    assert await task
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.epoch == 1 and run.status == "ACTIVE", run.state.get("reason")
        assert db.rows(s, db.Action, tenant, run_id=run_id)[0].status == "READY"


async def test_sse_delayed_database_calls_leave_event_loop_responsive(client, tenant, make_run, monkeypatch):
    from forgeagent.api import app

    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "CANCELLED"
        db.emit(s, run, "TEST_TERMINAL", "Terminal stream")
    original, event_thread = db.transaction, threading.get_ident()

    def delayed_transaction(*args):
        assert threading.get_ident() != event_thread
        time.sleep(0.05)
        return original(*args)

    monkeypatch.setattr(db, "transaction", delayed_transaction)
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get(f"/v1/runs/{run_id}/events")
        assert response.status_code == 200 and "event: domain" in response.text
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
    assert ticks >= 20


async def test_failed_write_snapshot_does_not_leak_to_next_action(tenant, make_run, monkeypatch):
    from forgeagent import repo_tools as worker_module

    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        baseline = json.loads(objects.get(tenant, run.state["workspace_ref"]))
        before = run.state["workspace_digest"]
        service.prepare_action(s, run, ToolCall(tool="repo.write", args={"path": "src/calculator.py",
            "content": "BROKEN", "expected_digest": digest(baseline["src/calculator.py"].encode())}), "write")
        service.prepare_action(s, run, ToolCall(tool="repo.read", args={"path": "src/calculator.py"}), "read")
    monkeypatch.setattr(worker_module, "files", lambda _: (_ for _ in ()).throw(OSError("snapshot fault")))
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        actions = db.rows(s, db.Action, tenant, run_id=run_id)
        assert [a.status for a in actions] == ["FAILED", "SUCCEEDED"]
        assert run.state["workspace_digest"] == before
        receipt = json.loads(objects.get(tenant, actions[1].receipt["ref"]))
        assert receipt["content"] == baseline["src/calculator.py"]
        assert rebuild(s, tenant, run_id) == db.projection(run)


def test_restore_rollback_preserves_backup_and_cleans_residues(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = sandbox.restore("tenant", "run", 1, {"src/a": "old"})
    replace = os.replace

    def fail(source, target):
        if ".stage-" in str(source) or ".backup-" in str(source):
            raise OSError("install and rollback failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        sandbox.restore("tenant", "run", 1, {"src/a": "new"})
    backups = list(root.parent.glob("1.backup-*"))
    assert len(backups) == 1 and files(backups[0]) == {"src/a": "old"}
    assert not list(root.parent.glob("1.stage-*"))
    monkeypatch.setattr(os, "replace", replace)
    assert files(sandbox.restore("tenant", "run", 1, {"src/a": "committed"})) == {"src/a": "committed"}
    assert not list(root.parent.glob("1.backup-*"))


def test_progress_persists_stalls_even_for_different_writes(tenant, make_run):
    run_id = make_run(budget=Budget(max_no_progress_turns=3))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        for turn in range(1, 4):
            run.state = {**run.state, "turn": turn, "workspace_digest": digest(str(turn))}
            progress.observed(s, run, "repo.write", {"path": "src/a", "digest": digest(str(turn))}, "SUCCEEDED")
            allowed = progress.decided(s, run)
        assert not allowed and run.status == "PAUSED"
        assert run.state["failure_counts"]["resource"] == 1
        assert rebuild(s, tenant, run_id) == db.projection(run)
        progress.observed(s, run, "repo.read", {"digest": digest("new fact")}, "SUCCEEDED")
        assert run.state["progress"]["stalled_turns"] == 0
        assert progress.decided(s, run)
        progress.observed(s, run, "repo.read", {"digest": digest("new fact")}, "SUCCEEDED")
        assert run.state["progress"]["stalled_turns"] == 1
        for _ in range(80):
            progress.record(s, run, "bounded")
        assert len(run.state["progress"]["journal"]) == 64


def test_migration_preserves_contract_and_invalidates_old_evidence(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        original = {k: deepcopy(run.state[k]) for k in ["task", "baseline_ref", "acceptance", "workspace_ref"]}
        run.status = "PAUSED"
        run.state = {**run.state, "semantic": {**run.state["semantic"], "implementation": {"old": "version"}},
                     "completion": "Old proposal", "verification": {"verdict": "PASS"}}
        db.emit(s, run, "TEST_LEGACY", "Legacy implementation")
        version = run.version
    response = client.post(f"/v1/runs/{run_id}/migrate", json={"expected_version": version, "reason": "Reviewed upgrade"})
    assert response.status_code == 200, response.text
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert {k: run.state[k] for k in original} == original
        assert run.state["semantic"]["implementation"] == semantic.bindings()
        assert run.state["completion"] is None and run.state["verification"] is None
        assert len(run.state["semantic_segments"]) == 1 and run.state["input_revision"] == 1
        assert rebuild(s, tenant, run_id) == db.projection(run)
        service.control(s, tenant, run_id, "resume", run.version, "Continue upgraded task")


def test_migration_unknown_billing_rejected(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.status = "PAUSED"
        service.reserve(s, run, "unknown", 1, 1)
        s.flush()
        service.settle(s, run, "unknown", None)
        with pytest.raises(Fault, match="reservations"):
            service.migrate(s, tenant, run_id, Control(expected_version=run.version))


def test_archive_dependency_and_digest_checks(tenant, make_run, tmp_path, monkeypatch):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        saved = json.loads(objects.get(tenant, run.state["executor_ref"]))
        saved["sources"]["worker.py"] += "\n# changed\n"
        run.state = {**run.state, "executor_ref": objects.put(tenant, "executors", saved)}
        with pytest.raises(Fault, match="executable module"):
            semantic.materialize(tenant, run, tmp_path)
        monkeypatch.setattr(semantic, "environment", lambda: {})
        with pytest.raises(Fault, match="dependency"):
            semantic.materialize(tenant, run, tmp_path)


def test_archived_executor_consumes_fixture_in_real_subprocess(tenant, make_run, monkeypatch):
    run_id = make_run()
    monkeypatch.setattr(service, "implementation_bindings", lambda: {"current": "changed runtime"})
    assert Worker().claim(tenant) is None
    semantic.execute_archived(tenant, run_id, quanta=4)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "SUCCEEDED", run.state.get("reason")
        assert len(db.rows(s, db.ModelCall, tenant, run_id=run_id)) == 2
        assert rebuild(s, tenant, run_id) == db.projection(run)


@pytest.mark.parametrize("window", ["upload", "commit"])
async def test_write_publish_fault_restores_and_retries_durable_snapshot(tenant, make_run, monkeypatch, window):
    run_id = make_run()
    worker = Worker(target_run=run_id)
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        before = db.get(s, db.Run, tenant, run_id).state["workspace_digest"]
    original_put, original_emit = objects.put, db.emit

    def fail_upload(tenant, namespace, value):
        if isinstance(value, dict) and value.get("src/calculator.py") == "def add(left, right):\n    return left + right\n":
            raise OSError("workspace publication failed")
        return original_put(tenant, namespace, value)

    def fail_commit(s, run, kind, *args, **kwargs):
        if kind == "ACTION_RESULT":
            original_emit(s, run, kind, *args, **kwargs)
            raise OSError("database commit fault")
        return original_emit(s, run, kind, *args, **kwargs)

    monkeypatch.setattr(objects, "put", fail_upload if window == "upload" else original_put)
    monkeypatch.setattr(db, "emit", fail_commit if window == "commit" else original_emit)
    await worker.once(tenant)
    monkeypatch.setattr(objects, "put", original_put)
    monkeypatch.setattr(db, "emit", original_emit)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = db.rows(s, db.Action, tenant, run_id=run_id)[0]
        assert run.status == "PAUSED" and run.state["workspace_digest"] == before
        assert action.status == "READY" and action.attempt == 1
        assert rebuild(s, tenant, run_id) == db.projection(run)
        service.control(s, tenant, run_id, "resume", run.version, "Retry safe workspace publication")
    for _ in range(3):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "SUCCEEDED", run.state.get("reason")
        assert len(db.rows(s, db.Action, tenant, run_id=run_id)) == 1
        assert db.rows(s, db.Action, tenant, run_id=run_id)[0].attempt == 2
