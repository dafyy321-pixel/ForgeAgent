import io
from datetime import timedelta

import pytest
from forgeagent import db
from forgeagent.domain import Fault, uid
from forgeagent.maintenance import clean_workspaces, reference_graph
from forgeagent.maintenance_jobs import claim, enqueue, once
from forgeagent.sandbox import sandbox
from forgeagent.storage import objects
from forgeagent.telemetry import calls, observed


async def test_maintenance_is_persistent_idempotent_and_retry_bounded(tenant, monkeypatch):
    from forgeagent import maintenance

    with db.transaction(tenant) as s:
        job = enqueue(s, tenant, "objects", {"apply": True}, "cleanup")
        job_id = job.id
        assert enqueue(s, tenant, "objects", {"apply": True}, "cleanup").id == job_id
        with pytest.raises(Fault, match="another operation"):
            enqueue(s, tenant, "objects", {"apply": False}, "cleanup")
    def unavailable(*args, **kwargs):
        raise Fault("OBJECT_UNAVAILABLE", "sensitive provider detail", 503)
    monkeypatch.setattr(maintenance, "collect", unavailable)
    for attempt in range(5):
        assert await once(tenant)
        with db.transaction(tenant) as s:
            job = db.get(s, db.PolicyVersion, tenant, job_id)
            assert job.data["attempts"] == attempt + 1
            assert "sensitive" not in str(job.data)
            job.data = {**job.data, "available_at": (db.clock(s) - timedelta(seconds=1)).isoformat()}
    with db.transaction(tenant) as s:
        assert db.get(s, db.PolicyVersion, tenant, job_id).status == "dead_letter"
    assert not await once(tenant)


def test_concurrent_claim_is_fenced_and_expired_claim_can_recover(tenant):
    with db.transaction(tenant) as s:
        job_id = enqueue(s, tenant, "objects", {}, uid()).id
    assert claim(tenant, "first")[0] == job_id
    assert claim(tenant, "second") is None
    with db.transaction(tenant) as s:
        job = db.get(s, db.PolicyVersion, tenant, job_id)
        job.data = {**job.data, "available_at": (db.clock(s) - timedelta(seconds=1)).isoformat()}
    assert claim(tenant, "second")[0] == job_id


async def test_complete_run_purge_does_not_require_memory(client, tenant, make_run):
    from forgeagent.worker import Worker

    run_id = make_run(harness={"memory": False})
    for _ in range(6):
        await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).status == "SUCCEEDED"
        cost = db.get(s, db.BudgetAccount, tenant, run_id).spent
    response = client.post(f"/v1/operations/runs/{run_id}/purge")
    assert response.status_code == 200, response.text
    assert not sandbox.root(tenant, run_id, 0).parent.exists()
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).state["knowledge_erased"]
        assert db.get(s, db.BudgetAccount, tenant, run_id).spent == cost
        assert not db.rows(s, db.Artifact, tenant, run_id=run_id)


def test_physical_erasure_rejects_unregistered_family(tenant, make_run):
    from forgeagent.knowledge_erasure import finish

    run_id = make_run()
    with db.transaction(tenant) as s:
        s.add(db.PolicyVersion(tenant_id=tenant, id="fake", status="audit", data={"runs": [run_id]}))
    with pytest.raises(Fault, match="exact registered"):
        finish(tenant, [run_id], "fake")


def test_reference_classes_and_workspace_cleanup_preserve_live_runs(tenant, make_run):
    import os
    import time

    run_id = make_run()
    path = sandbox.root(tenant, run_id, "verify-1")
    path.mkdir(parents=True, exist_ok=True)
    (path / "temp.txt").write_text("scratch")
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))
    with db.transaction(tenant) as s:
        graph = reference_graph(s, tenant)
        assert any(link["class"] == "recovery" for links in graph.values() for link in links)
        assert clean_workspaces(s, tenant, True)["paths"] == []
        db.get(s, db.Run, tenant, run_id).status = "CANCELLED"
    with db.transaction(tenant) as s:
        preview = clean_workspaces(s, tenant)
        assert str(path) in preview["paths"] and path.exists()
        clean_workspaces(s, tenant, True)
    assert not path.exists()
    # The durable workspace object remains available for history/recovery.
    with db.transaction(tenant) as s:
        assert objects.get(tenant, db.get(s, db.Run, tenant, run_id).state["workspace_ref"])


def test_telemetry_only_records_operation_and_outcome():
    @observed("test.redaction")
    def failed(secret):
        raise ValueError(secret)
    with pytest.raises(ValueError):
        failed("sensitive-api-key")
    output = io.StringIO()
    for metric in calls.collect():
        output.write(str(metric.samples))
    assert "test.redaction" in output.getvalue() and "sensitive-api-key" not in output.getvalue()


async def test_orphan_container_cleanup_requires_matching_storage_tenant_and_deadline(monkeypatch):
    import asyncio
    import json
    import time

    from forgeagent.config import settings
    from forgeagent.domain import digest

    scope = digest("cleanup-tenant")[7:23]
    storage = digest(str(settings.data_dir.resolve()))
    identifiers = [str(i) * 12 for i in range(1, 5)]
    removed = []
    class Process:
        returncode = 0
        def __init__(self, data=b""):
            self.data = data
        async def communicate(self):
            return self.data, b""
        async def wait(self):
            return 0
    async def process(*argv, **kwargs):
        if argv[1] == "ps":
            return Process("\n".join(identifiers).encode())
        if argv[1] == "rm":
            removed.append(argv[-1])
            return Process()
        index = identifiers.index(argv[-1])
        labels = {"forge.managed": "true", "forge.storage": storage, "forge.tenant": scope,
                  "forge.deadline": str(int(time.time()) - 100)}
        if index == 1:
            labels["forge.deadline"] = str(int(time.time()) + 100)
        elif index == 2:
            labels["forge.tenant"] = "another-tenant"
        elif index == 3:
            labels["forge.storage"] = "another-manager"
        return Process(json.dumps([{"Config": {"Labels": labels}}]).encode())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process)
    result = await sandbox.reap(True, scope)
    assert result["containers"] == removed == [identifiers[0]]


def test_old_epochs_reclaimable_while_current_epoch_is_preserved(tenant, make_run):
    import os
    import time

    run_id = make_run()
    old, current = sandbox.root(tenant, run_id, 1), sandbox.root(tenant, run_id, 2)
    for path in [old, current]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "temp").write_text("scratch")
        os.utime(path, (time.time() - 48 * 3600,) * 2)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.epoch = 2
        db.emit(s, run, "LEASE_CLAIMED", "test epoch fencing")
    with db.transaction(tenant) as s:
        clean_workspaces(s, tenant, True)
    assert not old.exists() and current.exists()
