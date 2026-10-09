"""Generate real PostgreSQL control, ledger and crash/recovery sequences."""

import asyncio
from datetime import timedelta

import pytest
from forgeagent import db, service
from forgeagent.domain import TERMINAL, CreateRun, Fault, Task, uid
from forgeagent.reducer import rebuild
from forgeagent.worker import Worker
from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from hypothesis.strategies import sampled_from


class RuntimeMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.tenant = "machine-" + uid()
        service.provision(self.tenant, "tester")
        with db.transaction(self.tenant) as s:
            self.id = service.create_run(s, self.tenant, "tester", CreateRun(project_id="runtime-lab", model="fixture",
                task=Task(goal="generated controls", allowed_paths=["src"])), uid()).id
        self.cancelled = False
        self.operations = []
        self.terminal = None

    @rule(command=sampled_from(["pause", "resume", "cancel"]))
    def control(self, command):
        try:
            with db.transaction(self.tenant) as s:
                run = db.get(s, db.Run, self.tenant, self.id)
                service.control(s, self.tenant, self.id, command, run.version, "state machine")
                self.cancelled |= command == "cancel"
        except Fault as error:
            assert error.code in {"INVALID_STATE", "UNKNOWN_USAGE", "UNSETTLED_BUDGET", "TERMINAL_RUN", "CANCEL_PENDING"}

    @rule()
    def reserve(self):
        operation = uid()
        with db.transaction(self.tenant) as s:
            run = db.get(s, db.Run, self.tenant, self.id)
            if run.status in TERMINAL:
                return
            service.reserve(s, run, operation, 1000, token_upper=10)
        self.operations.append(operation)

    @rule(unknown=sampled_from([True, False]))
    def settle(self, unknown):
        if not self.operations:
            return
        with db.transaction(self.tenant) as s:
            service.settle(s, db.get(s, db.Run, self.tenant, self.id), self.operations[0], None if unknown else 400, 4)
        if not unknown:
            self.operations.pop(0)

    @rule()
    def crash_transaction(self):
        with db.transaction(self.tenant) as s:
            before = db.projection(db.get(s, db.Run, self.tenant, self.id))
        with pytest.raises(RuntimeError), db.transaction(self.tenant) as s:
            run = db.get(s, db.Run, self.tenant, self.id, True)
            run.phase = "INJECTED"
            db.emit(s, run, "INJECTED", "Rollback before commit")
            raise RuntimeError("crash")
        with db.transaction(self.tenant) as s:
            assert db.projection(db.get(s, db.Run, self.tenant, self.id)) == before

    @rule()
    def lease_takeover(self):
        with db.transaction(self.tenant) as s:
            run = db.get(s, db.Run, self.tenant, self.id)
            run.lease_until = db.clock(s) - timedelta(seconds=1)
        Worker(target_run=self.id).claim(self.tenant)

    @rule()
    def worker_quantum(self):
        with db.transaction(self.tenant) as s:
            run = db.get(s, db.Run, self.tenant, self.id)
            run.lease_until = db.clock(s) - timedelta(seconds=1)
            run.available_at = db.clock(s)
        asyncio.run(Worker(target_run=self.id).once(self.tenant))

    @invariant()
    def committed_projection_and_money_agree(self):
        with db.transaction(self.tenant) as s:
            run = db.get(s, db.Run, self.tenant, self.id)
            assert rebuild(s, self.tenant, self.id) == db.projection(run)
            assert not self.cancelled or run.cancel_requested and run.status in {"CANCELLING", "CANCELLED"}
            if self.terminal:
                assert run.status == self.terminal
            elif run.status in TERMINAL:
                self.terminal = run.status
            account = db.get(s, db.BudgetAccount, self.tenant, self.id)
            entries = db.rows(s, db.BudgetEntry, self.tenant, account_id=self.id)
            assert account.reserved == sum(entry.reserved for entry in entries if entry.status != "settled")
            assert account.spent == sum(entry.actual for entry in entries if entry.status == "settled")
            assert 0 <= account.spent + account.reserved <= account.limit_micros


TestRuntimeMachine = RuntimeMachine.TestCase
TestRuntimeMachine.settings = settings(max_examples=12, stateful_step_count=15, deadline=None)


def test_pause_cannot_suspend_cancellation(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        service.control(s, tenant, run_id, "cancel", run.version, "cancel")
        version = run.version
    response = client.post(f"/v1/runs/{run_id}/pause", json={"expected_version": version})
    assert response.status_code == 409 and response.json()["code"] == "CANCEL_PENDING"
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "CANCELLING" and run.cancel_requested and not run.pause_requested
