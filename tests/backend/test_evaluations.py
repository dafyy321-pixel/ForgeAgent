import pytest
from forgeagent import db, service
from forgeagent.domain import Fault, digest
from forgeagent.evaluations import cluster_interval
from forgeagent.worker import Worker


def dataset(client):
    body = {
        "id": "cases@1",
        "source": "contract fixtures",
        "cases": [
            {
                "id": "add",
                "project_id": "runtime-lab",
                "task": {"goal": "Fix addition", "allowed_paths": ["src"]},
                "budget": {"max_cost_usd": "0.10"},
            }
        ],
    }
    assert client.post("/v1/evaluation-datasets", json=body).status_code == 201
    return body


def experiment():
    return {
        "dataset_id": "cases@1",
        "model": "fixture",
        "repetitions": 1,
        "configurations": [{"name": "baseline"}, {"name": "candidate", "harness": {"memory": False}}],
        "max_total_cost_usd": "0.20",
    }


async def test_paired_experiment_is_durable_and_not_independent_repeats(client, tenant):
    dataset(client)
    response = client.post("/v1/experiments", json=experiment())
    assert response.status_code == 202, response.text
    id = response.json()["id"]
    for _ in range(12):
        if not await Worker().once(tenant):
            break
    result = client.get(f"/v1/evaluations/{id}").json()
    assert result["status"] == "completed" and result["successes"] == 2
    assert result["independent_cases"] == 1
    assert result["comparisons"]["candidate"]["cluster_95_ci"] is None
    assert not result["comparisons"]["candidate"]["noninferiority_supported"]
    assert client.get(f"/v1/evaluations/{id}").json() == result
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.EvaluationResult, tenant)) == 2


def test_dataset_immutable_and_experiment_cost_capped(client):
    body = dataset(client)
    assert client.post("/v1/evaluation-datasets", json=body).status_code == 409
    spec = experiment()
    spec["max_total_cost_usd"] = "0.19"
    response = client.post("/v1/experiments", json=spec)
    assert response.status_code == 422 and response.json()["code"] == "EXPERIMENT_BUDGET"
    assert client.get("/v1/evaluations").json() == []


def test_project_drift_blocks_evaluation(client, tenant):
    dataset(client)
    with db.transaction(tenant) as s:
        project = db.get(s, db.Project, tenant, "runtime-lab")
        project.data = {**project.data, "name": "changed"}
    assert client.post("/v1/experiments", json=experiment()).json()["code"] == "DATASET_DRIFT"


def test_candidate_only_available_in_explicit_experiment(client, tenant, make_run):
    body = {
        "name": "test-skill",
        "version": "1.0.0",
        "description": "evidence",
        "content": "Read tests",
        "source": "test",
        "license": "MIT",
    }
    client.post("/v1/skills", json=body)
    with pytest.raises(Fault, match="released"):
        make_run(skills=["test-skill@1.0.0"])
    response = client.post("/v1/skills/test-skill@1.0.0/release", json={"enabled": True, "review": "manual review"})
    assert response.status_code == 409 and response.json()["code"] == "SKILL_GATE"
    dataset(client)
    spec = experiment()
    spec["configurations"][1]["skills"] = ["test-skill@1.0.0"]
    assert client.post("/v1/experiments", json=spec).status_code == 202


def test_cluster_bootstrap_reproducible():
    assert cluster_interval([0, 1, -1, 0.5], 42) == cluster_interval([0, 1, -1, 0.5], 42)
    assert cluster_interval([0], 42) is None


def test_recheck_and_semantic_compatibility(client, tenant, make_run, monkeypatch):
    id = make_run()
    run = client.get(f"/v1/runs/{id}").json()
    response = client.post(f"/v1/runs/{id}/recheck", json={"expected_version": run["stateVersion"]})
    assert response.status_code == 200 and response.json()["checkpoint"]["environment"]
    monkeypatch.setattr(service, "implementation_bindings", lambda: {"changed": digest("changed")})
    assert not client.get(f"/v1/runs/{id}").json()["checkpoint"]["compatible"]


async def test_fixture_does_not_accept_additional_executable_code(tenant):
    from forgeagent.verification import verify

    current = {"src/calculator.py": "def add(left, right):\n    return left + right\nimport os\nos.remove('victim')\n"}
    result = await verify(tenant, "test", 1, {}, current, {"kind": "fixture_ast"}, ["src"], None)
    assert result["verdict"] == "FAIL"


def test_denied_remote_action_requires_fresh_approval(client, tenant, make_run):
    from forgeagent.remote import prepare_remote

    id = make_run(capabilities=["repo.read", "external.write"])
    with db.transaction(tenant) as s:
        s.add(
            db.ToolVersion(
                tenant_id=tenant,
                id="remote",
                data={"kind": "mcp", "protocol": "2026-07-28", "url": "https://example.com/mcp"},
            )
        )
        s.flush()
        run = db.get(s, db.Run, tenant, id)
        old = prepare_remote(s, run, "remote", "example", {"value": 1})
        approval = db.rows(s, db.Approval, tenant, action_id=old.id)[0]
        old_id, approval_id, version, effect = old.id, approval.id, run.version, old.effect_digest
    response = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={
            "expected_version": version,
            "decision": "deny",
            "effect_digest": effect,
            "reason": "requires new review",
        },
    )
    assert response.status_code == 200
    run = client.get(f"/v1/runs/{id}").json()
    response = client.post(f"/v1/runs/{id}/reapprove", json={"expected_version": run["stateVersion"]})
    assert response.status_code == 202, response.text
    with db.transaction(tenant) as s:
        actions = db.rows(s, db.Action, tenant, run_id=id)
        assert len(actions) == 2 and actions[-1].id != old_id
        assert actions[-1].status == "WAITING_APPROVAL"
        assert db.get(s, db.Approval, tenant, approval_id).decision == "denied"
