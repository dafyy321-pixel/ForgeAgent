from forgeagent import db, models, service
from forgeagent.domain import Fault
from forgeagent.worker import Worker


class CrashBeforeApply(Worker):
    async def consume_response(self, *args):
        raise Fault("INJECTED_CRASH", "Crash after durable response, before decision consumption")


async def test_missing_parsed_decision_is_consumed_as_format_error(tenant, make_run, monkeypatch):
    run_id = make_run()
    monkeypatch.setattr(models, "parse_decision", lambda content: None)
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.state["format_errors"] == 1
        assert not db.rows(s, db.Action, tenant, run_id=run_id)
        assert db.rows(s, db.ModelCall, tenant, run_id=run_id)[0].data["receipt_state"] == "applied"


async def test_response_committed_before_crash_is_applied_without_new_model_request(tenant, make_run):
    run_id = make_run()
    await CrashBeforeApply().once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        calls = db.rows(s, db.ModelCall, tenant, run_id=run_id)
        assert run.status == "PAUSED" and run.state["turn"] == 0
        assert len(calls) == 1 and calls[0].data["receipt_state"] == "received"
        assert not db.rows(s, db.Action, tenant, run_id=run_id)
        service.control(s, tenant, run_id, "resume", run.version, "recover persisted response")
    worker = Worker()
    await worker.once(tenant)
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.ModelCall, tenant, run_id=run_id)) == 1
        assert len(db.rows(s, db.Action, tenant, run_id=run_id)) == 1
        assert db.rows(s, db.ModelCall, tenant, run_id=run_id)[0].data["receipt_state"] == "applied"
    for _ in range(4):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        assert db.get(s, db.Run, tenant, run_id).status == "SUCCEEDED"
        assert len(db.rows(s, db.Action, tenant, run_id=run_id)) == 1
        assert len(db.rows(s, db.Turn, tenant, run_id=run_id)) == 2


async def test_partial_decision_transaction_rolls_back_and_reconsumes_once(tenant, make_run, monkeypatch):
    run_id = make_run()
    original = service.prepare_action

    def fail_after_intent(*args):
        original(*args)
        raise RuntimeError("Injected failure after intent flush")

    monkeypatch.setattr(service, "prepare_action", fail_after_intent)
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "PAUSED" and run.state["turn"] == 0
        assert not db.rows(s, db.Action, tenant, run_id=run_id)
        assert db.rows(s, db.ModelCall, tenant, run_id=run_id)[0].data["receipt_state"] == "received"
        service.control(s, tenant, run_id, "resume", run.version, "retry consumption only")
    monkeypatch.setattr(service, "prepare_action", original)
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        assert len(db.rows(s, db.ModelCall, tenant, run_id=run_id)) == 1
        assert len(db.rows(s, db.Action, tenant, run_id=run_id)) == 1


async def test_input_supersedes_received_decision_instead_of_applying_old_actions(client, tenant, make_run):
    run_id = make_run()
    await CrashBeforeApply().once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        version = run.version
    assert client.post(f"/v1/runs/{run_id}/input", json={"expected_version": version, "content": "A new constraint"}).status_code == 200
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.control(s, tenant, run_id, "resume", run.version, "consume new input")
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        assert not db.rows(s, db.Action, tenant, run_id=run_id)
        assert db.rows(s, db.ModelCall, tenant, run_id=run_id)[0].data["receipt_state"] == "superseded_input"


async def test_manual_verification_cannot_skip_a_received_decision(client, tenant, make_run):
    run_id = make_run()
    await CrashBeforeApply().once(tenant)
    with db.transaction(tenant) as s:
        version = db.get(s, db.Run, tenant, run_id).version
    response = client.post(f"/v1/runs/{run_id}/verify", json={"expected_version": version})
    assert response.status_code == 409 and response.json()["code"] == "UNCONSUMED_RESPONSE"
