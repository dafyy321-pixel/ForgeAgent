from copy import deepcopy

import pytest
from forgeagent import db, evaluations
from forgeagent.benchmarks import normalize_external
from forgeagent.domain import Fault, digest
from forgeagent.pilot import prepare_plan


def source(index=0, repo="external/python"):
    return {"repository": repo, "base_commit": "a" * 40, "task_id": f"issue-{index}",
            "problem_family": f"family-{index}", "language": "python", "source_digest": digest(str(index))}


def prepared_cases():
    # Metadata fixtures, not claimed external model repairs or executed repositories.
    values = []
    for i in range(20):
        provenance = source(i, "external/python" if i < 10 else "external/typescript")
        provenance["language"] = "python" if i < 10 else "typescript"
        image = "sha256:" + "b" * 64
        values.append({"case": {"id": f"case-{i}", "project_id": f"project-{i}",
            "task": {"goal": f"Issue {i}", "allowed_paths": ["src"]}, "provenance": provenance},
            "project": {"id": f"project-{i}", "repository": {"commit": "a" * 40},
                "protected_tests": {"tests/hidden.py": "PRIVATE_ORACLE"}, "build": {"dependency_image": image}},
            "environment_image": image})
    return values


def test_pilot_is_single_variable_bounded_and_never_launches():
    plan = prepare_plan(prepared_cases(), "pilot@1", "development", {"max_cost_usd": "0.1"})
    assert plan["independent_tasks"] == 20 and plan["experiment"]["repetitions"] == 3
    assert plan["experiment"]["max_total_cost_usd"] == "12.0"
    left, right = plan["experiment"]["configurations"]
    assert left["harness"]["code_retrieval"] == "off" and right["harness"]["code_retrieval"] == "structure"
    assert {**left["harness"], "code_retrieval": "structure"} == right["harness"]
    assert not left["harness"]["memory"] and not left["harness"]["delegation"]
    assert "PRIVATE_ORACLE" not in str(plan)
    assert plan["model_run"] == plan["sandbox_run"] == "not_run" and not plan["launch_authorized"]
    values = prepared_cases()
    values[1]["case"]["provenance"]["task_id"] = "issue-0"
    with pytest.raises(Fault, match="unique issues"):
        prepare_plan(values, "pilot@1", "development", {"max_cost_usd": "0.1"})
    with pytest.raises(Fault, match="20–30"):
        prepare_plan(values[:2], "pilot@1", "development", {"max_cost_usd": "0.1"}, repetitions=10)


def test_external_ts_normalization_excludes_gold_and_preserves_issue_binding():
    record = {"instance_id": "external__ts-1", "repo": "external/ts", "base_commit": "a" * 40,
              "problem_statement": "Fix bug", "test_patch": "hidden test diff", "patch": "PRIVATE_GOLD_PATCH",
              "language": "typescript", "problem_family": "parser-errors"}
    result = normalize_external(record, "development")
    assert "PRIVATE_GOLD_PATCH" not in str(result)
    assert result["case_source"]["task_id"] == record["instance_id"] and result["case_source"]["language"] == "typescript"


def test_external_identity_and_holdout_partition_cannot_be_reworded(client, tenant):
    with db.transaction(tenant) as session:
        project = db.get(session, db.Project, tenant, "runtime-lab")
        project.data = {**project.data, "repository": {"commit": "a" * 40}}
    case = {"id": "one", "project_id": "runtime-lab", "task": {"goal": "First issue", "allowed_paths": ["src"]},
            "provenance": source()}
    body = {"id": "sources@1", "source": "metadata fixture", "cases": [case]}
    assert client.post("/v1/evaluation-datasets", json=body).status_code == 201
    duplicate = deepcopy(case)
    duplicate.update(id="renamed", task={"goal": "Reworded same issue", "allowed_paths": ["src"]})
    body.update(id="duplicates@1", cases=[case, duplicate])
    assert client.post("/v1/evaluation-datasets", json=body).json()["code"] == "DUPLICATE_TASK"
    duplicate["provenance"]["task_id"] = "different-issue"
    body.update(id="hold@1", split="held_out", cases=[duplicate])
    assert client.post("/v1/evaluation-datasets", json=body).json()["code"] == "HOLDOUT_SOURCE_OVERLAP"
    duplicate["provenance"]["repository"] = "external/other"
    duplicate["provenance"]["problem_family"] = "other-family"
    assert client.post("/v1/evaluation-datasets", json=body).status_code == 201
    duplicate["provenance"]["base_commit"] = "b" * 40
    body["id"] = "drift@1"
    assert client.post("/v1/evaluation-datasets", json=body).json()["code"] == "CASE_SOURCE_BINDING"


def test_report_retains_negative_transfer_and_counts_actual_repairs(client, tenant):
    from test_evaluations import dataset, experiment

    dataset(client)
    result = client.post("/v1/experiments", json=experiment()).json()
    with db.transaction(tenant) as session:
        for entry in result["entries"]:
            run = db.get(session, db.Run, tenant, entry["id"])
            run.status = "SUCCEEDED" if entry["config"] == "baseline" else "FAILED"
            run.state = {**run.state, "verification": {"verdict": "PASS" if run.status == "SUCCEEDED" else "FAIL"}}
            db.emit(session, run, "TEST_RESULT", "Synthetic report boundary")
        report = evaluations.report(session, db.get(session, db.Evaluation, tenant, result["id"]), tenant)
    assert report["independent_cases"] == 1
    assert report["summaries"]["baseline"]["repair_rate"] == 1
    assert report["summaries"]["candidate"]["repair_rate"] == 0
    comparison = report["comparisons"]["candidate"]
    assert comparison["paired_repair_difference"] == -1
    assert comparison["negative_transfer_cases"] == [{"case_id": "add", "repair_difference": -1}]
    assert comparison["per_project_repair_difference"] == {"runtime-lab": -1}
