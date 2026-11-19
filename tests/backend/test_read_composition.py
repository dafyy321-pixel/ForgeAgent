import json
from datetime import timedelta

import pytest
from forgeagent import db, repo_tools, service
from forgeagent.domain import Budget, Fault, Harness, ToolCall, digest
from forgeagent.remote_contracts import catalog
from forgeagent.storage import objects
from forgeagent.worker import Worker


def test_inspect_symbol_charges_each_bounded_range_and_preserves_source(tmp_path):
    (tmp_path / "mod.py").write_bytes(b"def item_a():\n    return 1\n\ndef item_b():\n    return 2\n")
    steps = []
    result = repo_tools.inspect_symbol(tmp_path, {"path": "mod.py", "query": "item", "max_matches": 2},
                                       {}, steps.extend)
    assert [step["line_start"] for step in steps] == [0, 3]
    assert [x["name"] for x in result["excerpts"]] == ["item_a", "item_b"]
    assert result["excerpts"][0]["content"] == "def item_a():\n    return 1\n"
    assert all(x["digest"].startswith("sha256:") for x in result["excerpts"])
    def denied(_):
        raise Fault("TOOL_BUDGET_LIMIT", "budget")
    with pytest.raises(Fault, match="budget"):
        repo_tools.inspect_symbol(tmp_path, {"path": "mod.py", "query": "item"}, {}, denied)


def test_inspection_is_absent_and_cannot_be_prepared_without_opt_in(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert "repo.inspect_symbol" not in catalog(run.state)
        action = service.prepare_action(s, run, ToolCall(tool="repo.inspect_symbol",
                                        args={"path": "src/calculator.py", "query": "add"}), "disabled")
        assert action.status == "FAILED"


@pytest.mark.parametrize("slots, expected_status", [(1, "FAILED"), (2, "SUCCEEDED")])
async def test_composition_worker_requires_one_root_slot_per_primitive(tenant, make_run, slots, expected_status):
    run_id = make_run(harness=Harness(read_composition=True), budget=Budget(max_tool_calls=slots))
    worker = Worker()
    _, epoch = worker.claim(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert "repo.inspect_symbol" in catalog(run.state)
        action_id = service.prepare_action(s, run, ToolCall(tool="repo.inspect_symbol",
                                           args={"path": "src/calculator.py", "query": "add"}), "inspect").id
    await worker.dispatch(tenant, run_id, epoch, action_id, None)
    with db.transaction(tenant) as s:
        action = db.get(s, db.Action, tenant, action_id)
        assert action.status == expected_status
        assert db.get(s, db.BudgetAccount, tenant, run_id).resources["tool_calls"] == slots
        receipt = json.loads(objects.get(tenant, action.receipt["ref"]))
        if slots == 1:
            assert receipt["error_code"] == "TOOL_BUDGET_LIMIT" and "excerpts" not in receipt
        else:
            [excerpt] = receipt["excerpts"]
            assert excerpt["line_start"] == 0 and excerpt["excerpt_digest"] == digest(excerpt["content"].encode())
            events = [e for e in db.rows(s, db.Event, tenant, run_id=run_id) if e.type == "SYMBOL_READS_ADMITTED"]
            assert events[0].payload["ranges"][0]["digest"] == excerpt["digest"]


async def test_interrupted_composition_reuses_its_range_reservation(tenant, make_run, monkeypatch):
    run_id = make_run(harness=Harness(read_composition=True), budget=Budget(max_tool_calls=2))
    worker = Worker()
    _, epoch = worker.claim(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action_id = service.prepare_action(s, run, ToolCall(tool="repo.inspect_symbol",
                                           args={"path": "src/calculator.py", "query": "add"}), "crash").id
    class Interrupted(BaseException):
        pass
    execute = repo_tools.execute
    def stop_before_range(root, tool, args, state):
        if tool == "repo.read":
            raise Interrupted()
        return execute(root, tool, args, state)
    with monkeypatch.context() as scoped:
        scoped.setattr(repo_tools, "execute", stop_before_range)
        with pytest.raises(Interrupted):
            await worker.dispatch(tenant, run_id, epoch, action_id, None)
    with db.transaction(tenant) as s:
        db.get(s, db.Run, tenant, run_id).lease_until = db.clock(s) - timedelta(seconds=1)
    replacement = Worker()
    _, epoch = replacement.claim(tenant)
    await replacement.dispatch(tenant, run_id, epoch, action_id, None)
    with db.transaction(tenant) as s:
        action = db.get(s, db.Action, tenant, action_id)
        assert action.status == "SUCCEEDED" and action.attempt == 2
        assert db.get(s, db.BudgetAccount, tenant, run_id).resources["tool_calls"] == 2
