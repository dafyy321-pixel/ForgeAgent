import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from forgeagent import db, evaluations, knowledge, service
from forgeagent.domain import Fault, canonical, digest, uid
from forgeagent.storage import objects
from forgeagent.worker import Worker


def memory(client, **fields):
    response = client.post("/v1/memories", json={"title": "Fix add convention", "content": "private_delta", "source": "reviewed development evidence", **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def skill(client, version="1.0.0", **fields):
    response = client.post("/v1/skills", json={"name": "repair-add", "version": version,
        "description": "Read add failure evidence", "content": "Read the source failure before patching.",
        "source": "reviewed development", "license": "MIT", **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_memory_retrieval_scopes_time_versions_conflicts_and_bound(client, tenant, make_run):
    first = memory(client, subject="add-contract", content="Use the old rule")
    second = memory(client, subject="add-contract", content="Use the new rule")
    irrelevant = memory(client, title="Marketing calendar", content="release dates")
    stale = memory(client, repository_revision="wrong-commit")
    expired = memory(client, expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    future = memory(client, valid_from=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        selected = run.state["memories"]
        assert {m["id"] for m in selected} == {first, second}
        assert all(m["conflict_ids"] for m in selected)
        assert not {irrelevant, stale, expired, future} & set(run.state["memory_lineage"])
        assert len(knowledge.retrieve(s, tenant, "runtime-lab", {"goal": "Fix add"}, limit=1)) == 1
        assert knowledge.retrieve(s, tenant, "runtime-lab", {"goal": "Fix add"}, limit=1)[0]["conflict_ids"]
    new = memory(client, subject="add-contract", content="Correct source-backed rule", supersedes_id=second)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Memory, tenant, second).status == "superseded"
        assert db.get(s, db.Memory, tenant, new).data["version"] == 2


async def test_withdrawal_invalidates_received_decision_and_historical_observations(client, tenant, make_run):
    from forgeagent.domain import Task
    from test_response_recovery import CrashBeforeApply

    memory_id = memory(client)
    run_id = make_run()
    await CrashBeforeApply(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        call = db.rows(s, db.ModelCall, tenant, run_id=run_id)[0]
        assert call.data["receipt_state"] == "received"
        revision = db.get(s, db.Run, tenant, run_id).state.get("input_revision", 0)
    result = client.delete(f"/v1/memories/{memory_id}")
    assert result.status_code == 200 and run_id in result.json()["affected_runs"]
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.state["memories"] == [] and run.state["knowledge_barrier"] == revision + 1
        assert db.rows(s, db.ModelCall, tenant, run_id=run_id)[0].data["receipt_state"] == "superseded_knowledge"
        service.control(s, tenant, run_id, "resume", run.version, "consume new knowledge state")
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.ModelCall, tenant, run_id=run_id)) == 2
        assert len(db.rows(s, db.Action, tenant, run_id=run_id)) == 1
    # A fresh task also excludes withdrawn knowledge.
    fresh = make_run(task=Task(goal="Fix add", allowed_paths=["src"]))
    with db.transaction(tenant) as s:
        assert not db.get(s, db.Run, tenant, fresh).state["memories"]


def test_erasure_traverses_derived_skills_and_runs_and_retries_storage(client, tenant, make_run, monkeypatch):
    memory_id = memory(client)
    source_id = make_run()
    skill_id = skill(client, source_runs=[source_id], content="private_delta from source trace")
    with db.transaction(tenant) as s:
        candidate = db.get(s, db.SkillVersion, tenant, skill_id)
        candidate.status = "active"  # test-only publication for lineage coverage
        source = db.get(s, db.Run, tenant, source_id)
        ref = source.state["workspace_ref"]
    client.delete(f"/v1/memories/{memory_id}")
    derived_id = make_run(skills=[skill_id])
    with db.transaction(tenant) as s:
        assert not db.get(s, db.Run, tenant, derived_id).state["memories"]
    original = objects.purge_scope
    def fail(*args):
        raise Fault("OBJECT_DELETE_FAILED", "injected storage failure")
    monkeypatch.setattr(objects, "purge_scope", fail)
    request = {"erase_derived_runs": True}
    result = client.post(f"/v1/memories/{memory_id}/purge", json=request)
    assert result.status_code == 409
    with db.transaction(tenant) as s:
        assert db.get(s, db.PolicyVersion, tenant, "memory-erasure-" + memory_id).status == "pending"
        for run_id in [source_id, derived_id]:
            run = db.get(s, db.Run, tenant, run_id)
            assert run.state["knowledge_erased"] and run.status == "CANCELLED"
            assert "private_delta" not in canonical(run.state).decode()
            assert not db.rows(s, db.Checkpoint, tenant, run_id=run_id)
            assert not db.rows(s, db.ContextManifest, tenant, run_id=run_id)
            assert service.replay(s, tenant, run_id)["projection"] == db.projection(run)
        assert db.get(s, db.SkillVersion, tenant, skill_id).status == "erased"
    monkeypatch.setattr(objects, "purge_scope", original)
    result = client.post(f"/v1/memories/{memory_id}/purge", json=request)
    assert result.status_code == 200 and set(result.json()["derived_runs"]) == {source_id, derived_id}
    with pytest.raises(Fault, match="missing"):
        objects.get(tenant, ref)
    view = client.get(f"/v1/runs/{source_id}")
    assert view.status_code == 200 and "private_delta" not in view.text
    assert client.get("/v1/workspace").status_code == 200
    assert client.get(f"/v1/skills/{skill_id}/package").status_code == 409
    result = client.post(f"/v1/runs/{source_id}/fork", headers={"Idempotency-Key": uid()}, json={"expected_version": view.json()["stateVersion"]})
    assert result.status_code == 409 and result.json()["code"] == "KNOWLEDGE_ERASED"


def test_erasure_retains_unsettled_billing_and_does_not_scrub_evidence(client, tenant, make_run):
    memory_id = memory(client)
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        operation = uid()
        service.reserve(s, run, operation, 1, 2)
        s.add(db.ModelCall(tenant_id=tenant, id=operation, run_id=run_id, status="UNKNOWN", data={"provider_request_id": "original"}))
    result = client.post(f"/v1/memories/{memory_id}/purge", json={"erase_derived_runs": True})
    assert result.status_code == 409 and result.json()["code"] == "UNKNOWN_USAGE"
    with db.transaction(tenant) as s:
        assert not db.get(s, db.Run, tenant, run_id).state.get("knowledge_erased")
        assert db.get(s, db.ModelCall, tenant, operation).data["provider_request_id"] == "original"


def test_package_metadata_and_binary_resources_are_lazy_and_exportable(client, tenant, make_run):
    from forgeagent.context import compile_context

    resource = {"data": "AAEC", "mode": "100755", "encoding": "base64"}
    skill_id = skill(client, resources={"scripts/tool.bin": resource, "references/rule.md": "private_resource_body"})
    with db.transaction(tenant) as s:
        row = db.get(s, db.SkillVersion, tenant, skill_id)
        row.status = "active"
    run_id = make_run(skills=[skill_id])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        _, manifest = compile_context(run.state["task"], run.state, [], run.state["skills"], [], 32768, 4096)
        assert "private_resource_body" not in canonical(manifest).decode()
        read = knowledge.read_skill(run.state, skill_id, "scripts/tool.bin")
        assert read["content"] == "AAEC" and read["mode"] == "100755"
        with pytest.raises(Fault, match="pinned"):
            knowledge.read_skill(run.state, skill_id, "../unregistered")
    response = client.get(f"/v1/skills/{skill_id}/package")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("scripts/tool.bin") == b"\x00\x01\x02"
        assert archive.read("SKILL.md").startswith(b"---\nname:")
        manifest = json.loads(archive.read("FORGE-MANIFEST.json"))
        assert manifest["files"]["scripts/tool.bin"]["digest"] == digest(b"\x00\x01\x02")
    response = client.post("/v1/skills", json={"name": "escape-skill", "version": "1.0.0", "description": "bad",
        "content": "bad", "source": "test", "license": "MIT", "resources": {"../escape": "bad"}})
    assert response.status_code == 403


def test_offline_proposals_exclude_holdout_and_require_failures(client, tenant, make_run):
    run_id = make_run()
    payload = {"run_ids": [run_id], "name": "failure-repair", "version": "1.0.0"}
    assert client.post("/v1/skills/proposals", json=payload).json()["code"] == "NO_FAILURE_EVIDENCE"
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        from forgeagent import progress
        progress.failed(s, run, "MERGE_CONFLICT", "repo.write")
    result = client.post("/v1/skills/proposals", json=payload)
    assert result.status_code == 201 and result.json()["clusters"] == {"conflict": 1}
    with db.transaction(tenant) as s:
        row = db.get(s, db.SkillVersion, tenant, result.json()["id"])
        assert row.status == "candidate" and row.data["auxiliary_cost_micros"] == 0
        evaluation = db.Evaluation(tenant_id=tenant, data={"split": "held_out"})
        s.add(evaluation)
        s.flush()
        run = db.get(s, db.Run, tenant, run_id)
        run.state = {**run.state, "evaluation_id": evaluation.id}
    payload["version"] = "1.0.1"
    assert client.post("/v1/skills/proposals", json=payload).json()["code"] == "HOLDOUT_LEAKAGE"


def test_holdout_is_single_use_disjoint_and_metadata_only(client):
    body = {"id": "dev-knowledge@1", "source": "test", "cases": [{"id": "case", "project_id": "runtime-lab",
            "task": {"goal": "Fix add", "allowed_paths": ["src"]}, "budget": {"max_cost_usd": "0.1"}}]}
    assert client.post("/v1/evaluation-datasets", json=body).status_code == 201
    body.update(id="hold-knowledge@1", split="held_out")
    assert client.post("/v1/evaluation-datasets", json=body).json()["code"] == "HOLDOUT_OVERLAP"
    body["cases"][0]["task"]["goal"] = "Disjoint held-out task"
    assert client.post("/v1/evaluation-datasets", json=body).status_code == 201
    visible = next(x for x in client.get("/v1/evaluation-datasets").json() if x["id"] == body["id"])
    assert "cases" not in visible and "Disjoint" not in canonical(visible).decode()
    spec = {"dataset_id": body["id"], "model": "fixture", "repetitions": 1,
            "configurations": [{"name": "base"}, {"name": "candidate"}], "max_total_cost_usd": "0.2"}
    assert client.post("/v1/experiments", json=spec).status_code == 202
    assert client.post("/v1/experiments", json=spec).json()["code"] == "HOLDOUT_CONSUMED"
    body["id"] = "hold-knowledge@2"
    assert client.post("/v1/evaluation-datasets", json=body).json()["code"] == "HOLDOUT_OVERLAP"


def test_release_gate_rejects_multivariable_skill_comparisons(tenant, monkeypatch):
    base = {"name": "base", "skills": [], "harness": {"memory": False}}
    candidate = {"name": "candidate", "skills": ["repair@1"], "harness": {"memory": False}}
    result = {"configurations": [base, candidate], "comparisons": {"candidate": {"noninferiority_supported": True, "cost_ratio": 0.9}},
              "results": [{"project_id": "a", "reserved": 0}, {"project_id": "b", "reserved": 0}],
              "split": "held_out", "model": "real-fixed-test-model", "independent_cases": 20,
              "repetitions": 3, "max_cost_ratio": 1}
    monkeypatch.setattr(evaluations, "report", lambda *args: result)
    with db.transaction(tenant) as s:
        experiment = db.Evaluation(tenant_id=tenant, status="completed", data={"kind": "paired"})
        s.add(experiment)
        s.flush()
        assert evaluations.release_gate(s, tenant, "repair@1", experiment.id, "candidate") == digest(result)
        candidate["skills"].append("another@1")
        with pytest.raises(Fault, match="single-skill"):
            evaluations.release_gate(s, tenant, "repair@1", experiment.id, "candidate")
        candidate["skills"].pop()
        candidate["harness"]["memory"] = True
        with pytest.raises(Fault, match="single-skill"):
            evaluations.release_gate(s, tenant, "repair@1", experiment.id, "candidate")


def test_canary_assignment_and_rollback_require_previous_release(client, tenant, make_run):
    old, current = skill(client), skill(client, version="2.0.0")
    with db.transaction(tenant) as s:
        row = db.get(s, db.SkillVersion, tenant, current)
        row.status = "active"
        row.data = {**row.data, "rollout_percent": 0}
        assert not knowledge.admitted(row, tenant, "fixed-run", None)
        assert knowledge.admitted(row, tenant, "fixed-run", "explicit-evaluation")
    run_id = make_run(skills=[current])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert not run.state["skills"] and not run.state["skill_assignments"][0]["selected"]
    assert client.post(f"/v1/skills/{current}/rollback", json={"target_id": old, "review": "reviewed rollback"}).json()["code"] == "SKILL_GATE"
    with db.transaction(tenant) as s:
        row = db.get(s, db.SkillVersion, tenant, old)
        row.status = "retired"
        row.data = {**row.data, "release_history": [{"enabled": True, "evaluation_digest": digest("reviewed release")}]}
    response = client.post(f"/v1/skills/{current}/rollback", json={"target_id": old, "review": "reviewed rollback"})
    assert response.status_code == 200
    with db.transaction(tenant) as s:
        assert db.get(s, db.SkillVersion, tenant, current).status == "retired"
        active = db.get(s, db.SkillVersion, tenant, old)
        assert active.status == "active" and active.data["release_history"][-1]["rollback_from"] == current


def test_events_remain_append_only_without_registered_settled_erasure(tenant, make_run):
    from sqlalchemy.exc import DBAPIError

    run_id = make_run()
    for operation in ["DELETE", "UPDATE"]:
        with pytest.raises(DBAPIError, match="append only"):
            with db.transaction(tenant) as s:
                command = "DELETE FROM run_events WHERE tenant_id=:tenant AND run_id=:run" if operation == "DELETE" else "UPDATE run_events SET payload='{}' WHERE tenant_id=:tenant AND run_id=:run"
                s.execute(db.text(command), {"tenant": tenant, "run": run_id})
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.Event, tenant, run_id=run_id)) >= 1


def test_versioned_s3_erasure_deletes_versions_and_checks_partial_failures():
    from forgeagent.storage import ObjectStore

    calls = []
    class Store:
        errors = False
        def get_paginator(self, name):
            assert name == "list_object_versions"
            return self
        def paginate(self, **kwargs):
            key = kwargs["Prefix"] + "object"
            yield {"Versions": [{"Key": key, "VersionId": "old"}], "DeleteMarkers": [{"Key": key, "VersionId": "marker"}]}
        def delete_objects(self, **kwargs):
            calls.append(kwargs)
            return {"Errors": [{"Code": "Denied"}]} if self.errors else {}
    store = ObjectStore()
    store.s3 = Store()
    store.purge_scope("scoped-tenant", "specific-run")
    assert {v["VersionId"] for v in calls[0]["Delete"]["Objects"]} == {"old", "marker"}
    assert all("/specific-run/" in v["Key"] for v in calls[0]["Delete"]["Objects"])
    store.s3.errors = True
    with pytest.raises(Fault, match="rejected"):
        store.purge_scope("scoped-tenant", "specific-run")


def test_creation_racing_erasure_cannot_leave_an_untracked_snapshot(client, tenant):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from forgeagent.domain import CreateRun, Task
    from forgeagent.knowledge_erasure import finish, prepare

    memory_id = memory(client)
    created, release, deleting = Event(), Event(), Event()
    def create():
        with db.transaction(tenant) as s:
            run = service.create_run(s, tenant, "tester", CreateRun(project_id="runtime-lab", model="fixture",
                                     task=Task(goal="Fix add", allowed_paths=["src"])), uid())
            created.set()
            assert release.wait(10)
            return run.id
    def erase():
        deleting.set()
        with db.transaction(tenant) as s:
            ids, key = prepare(s, tenant, memory_id, "tester")
        return finish(tenant, ids, key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        creation = pool.submit(create)
        assert created.wait(10)
        erasure = pool.submit(erase)
        assert deleting.wait(10)
        assert not erasure.done()
        release.set()
        run_id = creation.result(timeout=20)
        result = erasure.result(timeout=20)
    assert result["derived_runs"] == [run_id]
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).state["knowledge_erased"]


async def test_skill_resource_is_consumed_through_actual_worker_tool_path(client, tenant, make_run):
    from forgeagent.domain import ToolCall

    skill_id = skill(client, resources={"references/rule.md": "Pinned source rule"})
    with db.transaction(tenant) as s:
        db.get(s, db.SkillVersion, tenant, skill_id).status = "active"
    run_id = make_run(skills=[skill_id])
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = service.prepare_action(s, run, ToolCall(tool="skill.read", args={"skill_id": skill_id, "path": "references/rule.md"}), "resource")
        action_id = action.id
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        action = db.get(s, db.Action, tenant, action_id)
        assert action.status == "SUCCEEDED"
        receipt = json.loads(objects.get(tenant, action.receipt["ref"]))
        assert receipt["content"] == "Pinned source rule" and receipt["_observation"]["tool"] == "skill.read"


async def test_completed_run_with_real_actions_can_erase_its_lineage(client, tenant, make_run):
    memory_id = memory(client)
    run_id = make_run()
    worker = Worker(target_run=run_id)
    for _ in range(6):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "SUCCEEDED"
        action = db.rows(s, db.Action, tenant, run_id=run_id)[0]
        assert action.args and action.receipt
        account = db.get(s, db.BudgetAccount, tenant, run_id)
        spent = account.spent
    response = client.post(f"/v1/memories/{memory_id}/purge", json={"erase_derived_runs": True})
    assert response.status_code == 200, response.text
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "CANCELLED" and run.state["knowledge_erased"]
        action = db.rows(s, db.Action, tenant, run_id=run_id)[0]
        assert action.args == {} and action.receipt is None
        assert not db.rows(s, db.Artifact, tenant, run_id=run_id)
        assert db.get(s, db.BudgetAccount, tenant, run_id).spent == spent
        assert service.replay(s, tenant, run_id)["projection"] == db.projection(run)
