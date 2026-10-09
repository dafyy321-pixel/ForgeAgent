import pytest
from forgeagent import db, service
from forgeagent.domain import ToolCall, uid
from forgeagent.worker import Worker


def test_json_relationships_cannot_cross_runs_or_tenants(tenant, make_run):
    run_id, other = make_run(), make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        action = service.prepare_action(s, run, ToolCall(tool="repo.list", args={}), "read")
        action_id = action.id
    with pytest.raises(Exception, match="same run and tenant"), db.transaction(tenant) as s:
        s.add(db.Attempt(tenant_id=tenant, run_id=other, data={"action_id": action_id, "number": 1, "epoch": 1}))
    with pytest.raises(Exception, match="same tenant"), db.transaction(tenant + "-other") as s:
        s.add(db.Outbox(tenant_id=tenant + "-other", data={"action_id": action_id}))


async def test_received_response_and_checkpoint_are_immutable_but_consumption_is_allowed(tenant, make_run):
    run_id = make_run()
    await Worker(target_run=run_id).once(tenant)
    with db.transaction(tenant) as s:
        call = db.rows(s, db.ModelCall, tenant, run_id=run_id)[0]
        call_id = call.id
        checkpoint_id = db.rows(s, db.Checkpoint, tenant, run_id=run_id)[0].id
    with pytest.raises(Exception, match="received model response is immutable"), db.transaction(tenant) as s:
        call = db.get(s, db.ModelCall, tenant, call_id)
        call.data = {**call.data, "decision": {"kind": "propose_completion", "summary": "forged"}}
    with pytest.raises(Exception, match="committed evidence is immutable"), db.transaction(tenant) as s:
        checkpoint = db.get(s, db.Checkpoint, tenant, checkpoint_id)
        checkpoint.data = {**checkpoint.data, "state_digest": "forged"}
    with pytest.raises(Exception, match="registered settled erasure"), db.transaction(tenant) as s:
        s.delete(db.get(s, db.Checkpoint, tenant, checkpoint_id))
    # Normal decision consumption remains mutable without changing its persisted response.
    with db.transaction(tenant) as s:
        call = db.get(s, db.ModelCall, tenant, call_id)
        call.data = {**call.data, "receipt_state": "applied"}


async def test_terminal_action_cannot_be_requeued_or_renamed(tenant, make_run):
    run_id = make_run()
    worker = Worker(target_run=run_id)
    await worker.once(tenant)
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        action = db.rows(s, db.Action, tenant, run_id=run_id)[0]
        assert action.status == "SUCCEEDED"
        action_id = action.id
    with pytest.raises(Exception, match="settled action"), db.transaction(tenant) as s:
        db.get(s, db.Action, tenant, action_id).status = "READY"
    with pytest.raises(Exception, match="intent is immutable"), db.transaction(tenant) as s:
        db.get(s, db.Action, tenant, action_id).logical_key = uid()


def test_settled_financial_fact_cannot_be_overwritten(tenant, make_run):
    run_id, operation = make_run(), uid()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.reserve(s, run, operation, 100)
        s.flush()
        service.settle(s, run, operation, 50)
        entry_id = db.rows(s, db.BudgetEntry, tenant)[0].id
    with pytest.raises(Exception, match="settled budget is immutable"), db.transaction(tenant) as s:
        db.get(s, db.BudgetEntry, tenant, entry_id).actual = 0
    with pytest.raises(Exception, match="cannot be deleted"), db.transaction(tenant) as s:
        s.delete(db.get(s, db.BudgetEntry, tenant, entry_id))


def test_budget_run_link_cannot_escape_its_root(tenant, make_run):
    root, other = make_run(), make_run()
    with pytest.raises(Exception, match="account root and tenant"), db.transaction(tenant) as s:
        s.add(db.BudgetEntry(tenant_id=tenant, account_id=root, operation_id=uid(), reserved=1,
                             data={"run_id": other}))


def test_control_cannot_be_rebound_to_another_action(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        first = service.prepare_action(s, run, ToolCall(tool="repo.list", args={}), "first")
        second = service.prepare_action(s, run, ToolCall(tool="repo.list", args={}), "second")
        record = db.Outbox(tenant_id=tenant, data={"action_id": first.id})
        s.add(record)
        s.flush()
        record_id, second_id = record.id, second.id
    with pytest.raises(Exception, match="binding is immutable"), db.transaction(tenant) as s:
        db.get(s, db.Outbox, tenant, record_id).data = {"action_id": second_id}
