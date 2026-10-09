import inspect
import json
from pathlib import Path

import pytest
from forgeagent import db, model_protocol
from forgeagent.benchmarks import export_predictions, normalize_swebench, prepare
from forgeagent.config import settings
from forgeagent.context import compile_context, decision_schema
from forgeagent.domain import Fault, Harness, digest, uid
from forgeagent.research import CostAssumptions, aggregate, snapshot
from forgeagent.sandbox import git

ROOT = Path(__file__).parents[2]
PILOT = [json.loads(line) for split in ["development", "held_out"]
    for line in (ROOT / f"benchmarks/forge-regressions-{split}.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("case", PILOT, ids=lambda case: case["instance_id"])
def test_authored_historical_acceptance_against_current_code(case, client, tenant, make_run, tmp_path):
    # Only these repository-authored public tests execute here. Imported benchmark code runs in the sandbox.
    namespace = {}
    exec(compile(case["protected_tests"]["tests/backend/test_benchmark_acceptance.py"], case["instance_id"], "exec"), namespace)
    fixtures = {"client": client, "tenant": tenant, "make_run": make_run, "tmp_path": tmp_path}
    function = namespace["test_acceptance"]
    function(**{name: fixtures[name] for name in inspect.signature(function).parameters})


def test_external_benchmark_import_excludes_gold_and_exports_official_predictions():
    record = {"instance_id": "repo-1", "repo": "example/repo", "base_commit": "a" * 40,
        "problem_statement": "Fix pagination", "test_patch": "acceptance test diff", "patch": "SECRET-GOLD-SOLUTION"}
    normalized = normalize_swebench(record, "held_out")
    assert "SECRET-GOLD" not in json.dumps(normalized) and normalized["split"] == "held_out"
    output = export_predictions([{"instance_id": "repo-1", "model_name_or_path": "test-model", "model_patch": "delivered-patch"}])
    assert json.loads(output)["model_patch"] == "delivered-patch"
    with pytest.raises(Fault):
        export_predictions([{"instance_id": "duplicate", "model_patch": ""}])


def test_benchmark_preparation_pins_git_and_keeps_tests_out_of_task(tmp_path):
    git(tmp_path, "init", "--quiet")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/code.py").write_text("broken = True\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--quiet", "-m", "baseline")
    commit = git(tmp_path, "rev-parse", "HEAD").decode().strip()
    case = {"benchmark": "local", "instance_id": "regression", "base_commit": commit, "split": "development", "goal": "Fix code",
        "protected_tests": {"tests/test_hidden.py": "SECRET-ACCEPTANCE"}}
    result = prepare(tmp_path, case, "sha256:" + "a" * 64, ["python", "-m", "pytest"], ["src"])
    assert result["project"]["repository"]["commit"] == commit
    assert "SECRET-ACCEPTANCE" not in json.dumps(result["case"])
    assert result["project"]["build"]["dependency_image"] == "sha256:" + "a" * 64
    with pytest.raises(Fault, match="independent"):
        prepare(tmp_path, {**case, "protected_tests": {}}, "sha256:" + "a" * 64, ["python"], ["src"])
    from forgeagent.sandbox import sandbox

    patch = sandbox.patch({}, {"tests/test_external.py": "def test_external():\n    assert True\n"})
    imported = prepare(tmp_path, {**case, "protected_tests": {}, "acceptance_patch": patch},
        "sha256:" + "a" * 64, ["python"], ["src"], ["tests"])
    assert "tests/test_external.py" in imported["project"]["protected_tests"]
    with pytest.raises(Fault, match="outside"):
        prepare(tmp_path, {**case, "acceptance_patch": patch}, "sha256:" + "a" * 64, ["python"], ["src"], ["other"])


def test_harness_ablation_changes_actual_context_and_catalog(monkeypatch):
    task = {"goal": "Inspect src code", "allowed_paths": ["src"]}
    state = {"capabilities": ["delegate", "repo.read"], "semantic": {"model_id": "test", "harness": Harness(planning=False,
        summarization=False, delegation=False, tool_form="json", observation_fusion=True).model_dump()}}
    observations = [{"action_id": uid(), "status": "SUCCEEDED", "tool": "repo.read", "content": str(index)} for index in range(3)]
    _, manifest = compile_context(task, state, observations, [], [], 32768, 1024)
    assert not any(item["type"] == "plan" for item in manifest["items"])
    fused = next(item for item in manifest["items"] if item["type"] == "observation_batch")
    assert {value["action_id"] for value in fused["content"]} == {value["action_id"] for value in observations}
    assert manifest["summary"] is None and "delegate" not in decision_schema(state)["properties"]["kind"]["enum"]
    monkeypatch.setattr(settings, "model_output_mode", "native")
    request = model_protocol.build_request([{"role": "system", "content": "system"}, {"role": "user", "content": "task"}], state, {})
    assert request["mode"] == "json" and not request["payload"].get("tools")


def test_full_cost_incomplete_rates_are_explicit_and_assumptions_are_bound(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        first = snapshot(s, run)
        assert not first["complete"] and first["total_cost_usd"] is None and "environment_usd" in first["missing"]
        assumptions = {"environment_usd_per_run": "0", "cpu_usd_per_second": "0", "storage_usd_per_gib_hour": "0"}
        complete = snapshot(s, run, assumptions)
        assert complete["complete"] and complete["total_cost_usd"] == 0
        assert complete["assumptions_digest"] == digest(CostAssumptions.model_validate(assumptions).model_dump(mode="json"))
        result = aggregate([{"research": first, "status": "FAILED"}])
        assert result["full_cost_usd"] is None and sum(result["failure_distribution"].values()) == 1
    with pytest.raises(ValueError):
        CostAssumptions(tool_usd_per_call={"tool": "-1"})


def test_zero_money_unknown_usage_keeps_experiment_report_live(client, tenant):
    from forgeagent import service
    from test_evaluations import dataset, experiment

    dataset(client)
    result = client.post("/v1/experiments", json=experiment()).json()
    ids = result["runs"]
    with db.transaction(tenant) as s:
        for identity in ids:
            run = db.get(s, db.Run, tenant, identity)
            service.reserve(s, run, "unknown-" + identity, 0, token_upper=10)
            s.flush()
            service.settle(s, run, "unknown-" + identity, None)
            run.status = "PAUSED"
            db.emit(s, run, "TEST_UNKNOWN", "Zero-dollar token reservation is not settled")
    report = client.get(f"/v1/evaluations/{result['id']}").json()
    assert report["status"] == "awaiting_reconciliation"
    with db.transaction(tenant) as s:
        assert "report" not in db.get(s, db.Evaluation, tenant, result["id"]).data
        for identity in ids:
            service.settle(s, db.get(s, db.Run, tenant, identity), "unknown-" + identity, 0, 3)
    assert client.get(f"/v1/evaluations/{result['id']}").json()["status"] == "completed"


async def test_normal_quanta_are_not_reported_as_crash_recovery(tenant, make_run):
    from forgeagent.worker import Worker

    run_id = make_run()
    for _ in range(4):
        await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        claims = [event for event in db.rows(s, db.Event, tenant, run_id=run_id) if event.type == "LEASE_CLAIMED"]
        assert len(claims) >= 3 and not any(event.payload["recovered"] for event in claims)
        report = snapshot(s, db.get(s, db.Run, tenant, run_id))
        assert not report["recovery_to_progress_seconds"] and report["pending_recoveries"] == 0


@pytest.mark.parametrize("enabled", [True, False])
async def test_action_fusion_retains_write_when_following_check_fails(tenant, make_run, monkeypatch, enabled):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from forgeagent.sandbox import sandbox
    from forgeagent.worker import Worker, ordered_actions

    run_id = make_run(model="configured", harness=Harness(action_fusion=enabled))
    with db.transaction(tenant) as s:
        before = db.get(s, db.Run, tenant, run_id).state["workspace_ref"]
    async def model(*args):
        decision = {"kind": "tool_calls", "summary": "Write and check with separate receipts", "calls": [
            {"tool": "repo.write", "args": {"path": "src/new.py", "content": "committed = True\n", "expected_digest": "absent"}},
            {"tool": "tests.run", "args": {"argv": ["python", "-c", "raise AssertionError('business failure')"]}}]}
        return json.dumps(decision), {"input_tokens": 1, "output_tokens": 1}, {}, "fusion-test"
    monkeypatch.setattr(sandbox, "image_digest", AsyncMock(return_value="sha256:" + "a" * 64))
    monkeypatch.setattr(sandbox, "execute", AsyncMock(return_value={"exit_code": 1, "output": "business failure", "elapsed_seconds": .1}))
    worker = Worker(model=model, target_run=run_id)
    await worker.once(tenant)
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        actions = db.rows(s, db.Action, tenant, run_id=run_id)
        if enabled:
            assert {action.tool: action.status for action in actions} == {"repo.write": "SUCCEEDED", "tests.run": "FAILED"}
            from forgeagent.storage import objects

            assert "src/new.py" in json.loads(objects.get(tenant, run.state["workspace_ref"]))
            same_time = run.created_at
            sample = [SimpleNamespace(id="aaa", logical_key="1:1", created_at=same_time),
                      SimpleNamespace(id="zzz", logical_key="1:0", created_at=same_time)]
            assert [action.id for action in ordered_actions(sample)] == ["zzz", "aaa"]
        else:
            assert not actions and run.state["workspace_ref"] == before
            assert run.state["format_errors"] == 2
