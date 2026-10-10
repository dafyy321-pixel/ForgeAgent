import pytest
from forgeagent import db, evaluations, knowledge, progress, skill_evolution
from forgeagent.domain import Fault, Task, digest


def proposal(tenant, make_run):
    run_id = make_run(task=Task(goal="PRIVATE_SOURCE_TASK", allowed_paths=["src"]))
    with db.transaction(tenant) as session:
        run = db.get(session, db.Run, tenant, run_id)
        progress.failed(session, run, "MERGE_CONFLICT", "repo.write")
        result = skill_evolution.propose(session, tenant, [run_id, run_id], "scoped-repair", "1.0.0")
    return run_id, result["id"]


def test_candidate_binds_source_version_without_claiming_fixture_effectiveness(tenant, make_run, client):
    run_id, skill_id = proposal(tenant, make_run)
    with db.transaction(tenant) as session:
        skill = db.get(session, db.SkillVersion, tenant, skill_id)
        assert skill.data["source_runs"] == [run_id]
        assert skill.data["source_evidence"][0]["fixture"] is True
        assert skill.data["source_evidence"][0]["projection_digest"].startswith("sha256:")
        assert "PRIVATE_SOURCE_TASK" not in str(skill.data)
        assert knowledge.applies(skill, "runtime-lab", None, None)
        assert not knowledge.applies(skill, "other", None, None)
        assert not knowledge.applies(skill, "runtime-lab", "a" * 40, None)
        assert knowledge.applies(skill, "other", "a" * 40, "explicit-evaluation")
        skill.status = "active"  # Policy mechanics only; not a genuine approved release.
    assert client.post("/v1/projects", json={"id": "other", "name": "Other",
        "baseline": {"src/a.py": "a = 1"}, "acceptance_id": "other@1", "verification_argv": ["python", "src/a.py"]}).status_code == 201
    response = client.post("/v1/runs", json={"project_id": "other", "model": "configured", "skills": [skill_id],
        "task": {"goal": "Outside source scope", "allowed_paths": ["src"]}}, headers={"Idempotency-Key": "scope-test"})
    assert response.status_code == 422 and response.json()["code"] == "SKILL_APPLICABILITY"


def test_scoped_release_rejects_project_regression_and_restores_reviewed_scope(tenant, make_run, monkeypatch):
    _, skill_id = proposal(tenant, make_run)
    result = {"configurations": [{"name": "base", "skills": [], "harness": {"memory": False}},
        {"name": "candidate", "skills": [skill_id], "harness": {"memory": False}}],
        "comparisons": {"candidate": {"noninferiority_supported": True, "cost_ratio": .9,
            "per_project_repair_difference": {"a": .2, "b": -.1}}},
        "results": [{"project_id": "a", "reserved": 0, "config": "candidate", "repository_commit": "a" * 40},
                    {"project_id": "b", "reserved": 0, "config": "candidate", "repository_commit": "b" * 40}],
        "split": "held_out", "model": "synthetic-model-label", "independent_cases": 20,
        "repetitions": 3, "max_cost_ratio": 1, "noninferiority_margin": .02}
    monkeypatch.setattr(evaluations, "report", lambda *args: result)
    with db.transaction(tenant) as session:
        evaluation = db.Evaluation(tenant_id=tenant, status="completed", data={"kind": "paired"})
        session.add(evaluation)
        session.flush()
        with pytest.raises(Fault, match="per-project repair"):
            evaluations.release_gate(session, tenant, skill_id, evaluation.id, "candidate")
        result["comparisons"]["candidate"]["per_project_repair_difference"]["b"] = 0
        assert evaluations.release_gate(session, tenant, skill_id, evaluation.id, "candidate") == digest(result)
        skill = db.get(session, db.SkillVersion, tenant, skill_id)
        scope = skill_evolution.reviewed_scope(session, tenant, skill, evaluation.id, "candidate")
        assert {"project_id": "b", "repository_commit": "b" * 40} in scope["bindings"]
        skill.data = {**skill.data, "applicability": scope, "release_history": [{"enabled": True,
            "evaluation_digest": digest(result), "applicability": scope}]}
        newer = db.SkillVersion(tenant_id=tenant, id="scoped-repair@2.0.0", status="active",
                               data={"name": "scoped-repair", "version": "2.0.0"})
        session.add(newer)
        session.flush()
        skill_evolution.rollback(session, tenant, newer.id, skill_id, "tester", "Reviewed rollback")
        assert skill.data["applicability"] == scope
        assert skill.data["release_history"][-1]["applicability"] == scope
