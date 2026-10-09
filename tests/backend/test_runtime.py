from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from forgeagent import db, service
from forgeagent.context import compile_context
from forgeagent.domain import ApprovalDecision, Budget, CreateRun, Decision, Fault, Task, digest, uid
from forgeagent.sandbox import safe_path
from forgeagent.storage import objects
from forgeagent.worker import Worker
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import select, text


async def finish(tenant, id, limit=12):
    worker = Worker()
    for _ in range(limit):
        with db.transaction(tenant) as s:
            status = db.get(s, db.Run, tenant, id).status
        if status in {"SUCCEEDED", "FAILED", "PAUSED", "CANCELLED"}:
            return status
        await worker.once(tenant)
    return status


async def test_end_to_end_evidence(tenant, make_run):
    id = make_run()
    assert await finish(tenant, id) == "SUCCEEDED"
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        artifacts = db.rows(s, db.Artifact, tenant, run_id=id)
        assert {a.kind for a in artifacts} == {"patch", "test_report", "summary", "log"} and all(a.verified for a in artifacts)
        patch = next(a for a in artifacts if a.kind == "patch")
        assert r.state["verification"]["artifact_digest"] == patch.ref["digest"]
        assert b"+    return left + right" in objects.get(tenant, patch.ref)
        seqs = sorted(e.seq for e in db.rows(s, db.Event, tenant, run_id=id))
        assert seqs == list(range(1, r.seq + 1))


def test_idempotency_concurrent(tenant):
    spec, key = CreateRun(project_id="runtime-lab", model="fixture", task=Task(goal="same")), uid()

    def create(_):
        with db.transaction(tenant) as s:
            return service.create_run(s, tenant, "tester", spec, key).id

    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(create, range(12)))
    assert len(set(ids)) == 1
    with db.transaction(tenant) as s, pytest.raises(Fault, match="different parameters"):
        service.create_run(s, tenant, "tester", spec.model_copy(update={"title": "changed"}), key)


def test_single_lease_concurrent(tenant, make_run):
    make_run()
    with ThreadPoolExecutor(8) as pool:
        claims = list(pool.map(lambda _: Worker().claim(tenant), range(8)))
    assert sum(c is not None for c in claims) == 1


def test_old_worker_fenced_after_takeover(tenant, make_run):
    id = make_run()
    first, second = Worker("a"), Worker("b")
    _, epoch = first.claim(tenant)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id, True)
        r.lease_until = db.clock(s) - timedelta(seconds=1)
    assert second.claim(tenant)[1] > epoch
    with db.transaction(tenant) as s, pytest.raises(Fault, match="superseded"):
        db.fence(s, tenant, id, "a", epoch)


def test_rls_even_without_application_filter(tenant, make_run):
    id = make_run()
    other = "other-" + uid()
    service.provision(other, "tester")
    with db.transaction(other) as s:
        assert s.scalar(select(db.Run).where(db.Run.id == id)) is None
        assert s.execute(text("SELECT * FROM runs WHERE id=:id"), {"id": id}).first() is None
    with db.engine.connect() as c:
        role = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).first()
        assert role == (False, False)


def test_event_append_only(tenant, make_run):
    id = make_run()
    with pytest.raises(Exception, match="append only"), db.transaction(tenant) as s:
        s.execute(text("DELETE FROM run_events WHERE run_id=:id"), {"id": id})


def test_budget_concurrent_no_oversell(tenant, make_run):
    id = make_run(budget=Budget(max_cost_usd="1.00"))

    def reserve(_):
        try:
            with db.transaction(tenant) as s:
                r = db.get(s, db.Run, tenant, id)
                service.reserve(s, r, uid(), 600000)
            return True
        except Fault:
            return False

    with ThreadPoolExecutor(6) as pool:
        assert sum(pool.map(reserve, range(6))) == 1
    with db.transaction(tenant) as s:
        assert db.get(s, db.BudgetAccount, tenant, id).reserved == 600000


def test_unknown_usage_holds_reservation_and_settles_once(tenant, make_run):
    id, op = make_run(), uid()
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        service.reserve(s, r, op, 200000)
        s.flush()
        service.settle(s, r, op, None)
        assert db.get(s, db.BudgetAccount, tenant, id).reserved == 200000
        service.settle(s, r, op, 150000)
        service.settle(s, r, op, 150000)
        account = db.get(s, db.BudgetAccount, tenant, id)
        assert account.spent == 150000 and account.reserved == 0


async def test_cancel_unknown_never_clean_terminal(tenant, make_run):
    id = make_run()
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id, True)
        s.add(
            db.Action(
                tenant_id=tenant,
                run_id=id,
                logical_key="remote",
                tool="remote.call",
                args={},
                effect_class="external_write",
                effect_digest=digest({}),
                status="UNKNOWN",
            )
        )
        service.control(s, tenant, id, "cancel", r.version, "cancel")
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        assert r.status == "CANCELLING" and r.cancel_requested and r.wait_reason == "RECONCILIATION"


async def test_cancellation_settles_without_actions(tenant, make_run):
    id = make_run()
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        service.control(s, tenant, id, "cancel", r.version, "cancel")
    assert await finish(tenant, id) == "CANCELLED"


async def test_terminal_cannot_resume(tenant, make_run):
    id = make_run()
    assert await finish(tenant, id) == "SUCCEEDED"
    with db.transaction(tenant) as s, pytest.raises(Fault, match="finished run"):
        r = db.get(s, db.Run, tenant, id)
        service.control(s, tenant, id, "resume", r.version, "resume")


def test_control_version_conflict(tenant, make_run):
    id = make_run()
    with db.transaction(tenant) as s, pytest.raises(Fault, match="Task changed"):
        service.control(s, tenant, id, "pause", 999, "stale browser")


async def test_receipt_consumed_without_duplicate(tenant, make_run):
    id = make_run()
    w = Worker()
    await w.once(tenant)  # decision + intent
    await w.once(tenant)  # actual write + receipt, before consume
    with db.transaction(tenant) as s:
        a = db.rows(s, db.Action, tenant, run_id=id)[0]
        assert a.status == "SUCCEEDED" and not a.consumed
    assert await finish(tenant, id) == "SUCCEEDED"
    with db.transaction(tenant) as s:
        a = db.rows(s, db.Action, tenant, run_id=id)[0]
        assert a.attempt == 1 and a.consumed


async def test_intent_survives_worker_restart(tenant, make_run):
    id = make_run()
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        intent = db.rows(s, db.Action, tenant, run_id=id)[0].id
    assert await finish(tenant, id) == "SUCCEEDED"
    with db.transaction(tenant) as s:
        assert [a.id for a in db.rows(s, db.Action, tenant, run_id=id)] == [intent]


def test_external_dispatch_recovery_becomes_unknown(tenant, make_run):
    id = make_run()
    with db.transaction(tenant) as s:
        s.add(
            db.Action(
                tenant_id=tenant,
                run_id=id,
                logical_key="r",
                tool="remote.call",
                args={},
                effect_class="external_write",
                effect_digest=digest({}),
                status="DISPATCHED",
            )
        )
    Worker().claim(tenant)
    with db.transaction(tenant) as s:
        assert db.rows(s, db.Action, tenant, run_id=id)[0].status == "UNKNOWN"


def test_revocation_invalidates_approved_effect(tenant, make_run):
    from forgeagent.remote import authorize_remote, prepare_remote
    from test_remote_contracts import registered

    connection_id = registered(tenant, "external_write", "send", {"type": "object", "properties": {"body": {"type": "string"}},
                                                                 "required": ["body"], "additionalProperties": False})
    id = make_run(connections=[connection_id], capabilities=["repo.read", "external.write"])
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        a = prepare_remote(s, r, connection_id, "send", {"body": "test"})
        s.flush()
        p = db.rows(s, db.Approval, tenant, action_id=a.id)[0]
        service.approve(
            s,
            tenant,
            "tester",
            p.id,
            ApprovalDecision(
                expected_version=r.version,
                decision="approve",
                effect_digest=a.effect_digest,
                reason="approve explicit action",
            ),
        )
        auth = db.get(s, db.Authorization, tenant, "tester")
        auth.data = {**auth.data, "epoch": auth.data["epoch"] + 1}
        with pytest.raises(Fault, match="valid approval"):
            authorize_remote(s, r, a)


def test_child_scope_cannot_expand(tenant, make_run):
    id = make_run(capabilities=["repo.read", "delegate"])
    with db.transaction(tenant) as s, pytest.raises(Fault):
        r = db.get(s, db.Run, tenant, id)
        service.create_run(
            s, tenant, "tester", CreateRun(project_id="runtime-lab", task=Task(goal="child")), uid(), parent=r
        )


def test_child_shares_root_budget(tenant, make_run):
    id = make_run(capabilities=["repo.read", "delegate"])
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        child = service.create_run(
            s,
            tenant,
            "tester",
            CreateRun(
                project_id="runtime-lab", capabilities=["repo.read"], task=Task(goal="child", allowed_paths=["src"])
            ),
            uid(),
            parent=r,
        )
        assert child.root_id == id and child.parent_id == id
        assert s.get(db.BudgetAccount, (tenant, child.id)) is None


def test_root_token_reservations_are_atomic(tenant, make_run):
    id = make_run(budget=Budget(max_tokens=10))

    def reserve(_):
        try:
            with db.transaction(tenant) as s:
                r = db.get(s, db.Run, tenant, id)
                service.reserve(s, r, uid(), 0, token_upper=6)
            return True
        except Fault as exc:
            assert exc.code == "TOKEN_BUDGET_LIMIT"
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(reserve, range(2))) == 1
    with db.transaction(tenant) as s:
        assert db.get(s, db.BudgetAccount, tenant, id).resources["tokens_reserved"] == 6


def test_unknown_tokens_remain_reserved_until_reconciled(tenant, make_run):
    id = make_run(budget=Budget(max_tokens=20))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, id)
        service.reserve(s, run, "call", 1, token_upper=10)
        s.flush()
        service.settle(s, run, "call", None)
        assert db.get(s, db.BudgetAccount, tenant, id).resources["tokens_reserved"] == 10
        service.settle(s, run, "call", 1, 3)
        account = db.get(s, db.BudgetAccount, tenant, id)
        assert {k: account.resources[k] for k in ("tokens_reserved", "tokens_spent")} == {"tokens_reserved": 0, "tokens_spent": 3}
        assert account.resources["storage_bytes"] > 0


def test_child_cannot_expand_root_tool_or_own_cost_budget(tenant, make_run):
    id = make_run(capabilities=["repo.read", "delegate"], budget=Budget(max_tool_calls=1))
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, id)
        child = service.create_run(
            s,
            tenant,
            "tester",
            CreateRun(
                project_id="runtime-lab",
                model="fixture",
                capabilities=["repo.read"],
                task=Task(goal="child", allowed_paths=["src"]),
                budget=Budget(max_cost_usd="0.000001"),
            ),
            uid(),
            parent=run,
        )
        service.consume_tool_slot(s, run)
        with pytest.raises(Fault, match="Root tool-call"):
            service.consume_tool_slot(s, child)
        with pytest.raises(Fault, match="Child allocation"):
            service.reserve(s, child, uid(), 2)


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", "C:/secret", ".env", "src/../../secret", ".git/config", "src/../bad"]
)
def test_workspace_path_confinement(tmp_path, path):
    with pytest.raises(Fault):
        safe_path(tmp_path, path)


def test_corrupt_object_rejected(tenant):
    ref = objects.put(tenant, uid(), b"evidence")
    (objects.root / ref["key"]).write_bytes(b"tampered")
    with pytest.raises(Fault, match="checksum"):
        objects.get(tenant, ref)


def test_cross_tenant_object_reference_rejected(tenant):
    ref = objects.put(tenant, uid(), b"private")
    with pytest.raises(Fault, match="tenant"):
        objects.get("another", ref)


def test_context_keeps_constraints_and_deduplicates():
    messages, manifest = compile_context(
        {"goal": "do not change tests"}, {}, [{"large": "x" * 50000}], [], [], 8000, 1000
    )
    assert "do not change tests" in messages[1]["content"]
    assert manifest["omitted"] and manifest["estimated_tokens_upper_bound"] <= manifest["input_budget"]


def test_core_context_overflow_stops():
    with pytest.raises(Fault, match="Mandatory"):
        compile_context({"goal": "x" * 10000}, {}, [], [], [], 3000, 1000)


@given(st.lists(st.text(min_size=0, max_size=200), max_size=50))
@hypothesis_settings(max_examples=100)
def test_context_budget_invariant(values):
    _, m = compile_context({"goal": "required"}, {}, [{"output": v} for v in values], [], [], 8000, 1000)
    assert m["estimated_tokens_upper_bound"] <= m["input_budget"]
    assert m["items"][1]["content"]["goal"] == "required"


async def test_verifier_fails_wrong_artifact(tenant):
    from forgeagent.verification import verify

    base = {"src/calculator.py": "def add(left, right):\n    return left - right\n"}
    result = await verify(tenant, uid(), 1, base, base, {"kind": "fixture_ast"}, ["src"], None)
    assert result["verdict"] == "FAIL"


async def test_missing_verifier_is_inconclusive(tenant):
    from forgeagent.verification import verify

    result = await verify(tenant, uid(), 1, {}, {}, {}, ["src"], None)
    assert result["verdict"] == "INCONCLUSIVE"


async def test_modified_existing_tests_cannot_pass(tenant):
    from forgeagent.verification import verify

    base = {"tests/test_a.py": "assert False"}
    current = {"tests/test_a.py": "pass", "src/calculator.py": "def add(left, right):\n    return left + right\n"}
    result = await verify(tenant, uid(), 1, base, current, {"kind": "fixture_ast"}, ["src", "tests"], None)
    assert result["verdict"] == "FAIL"


def test_model_cannot_directly_set_success():
    with pytest.raises(ValueError):
        Decision.model_validate({"kind": "set_status", "summary": "done", "status": "SUCCEEDED"})


def test_api_errors_and_request_key(client):
    payload = {"project_id": "runtime-lab", "model": "fixture", "task": {"goal": "Fix add", "allowed_paths": ["src"]}}
    assert client.post("/v1/runs", json=payload).status_code == 422
    a = client.post("/v1/runs", json=payload, headers={"Idempotency-Key": "browser-key"})
    b = client.post("/v1/runs", json=payload, headers={"Idempotency-Key": "browser-key"})
    assert a.status_code == 202 and a.json()["id"] == b.json()["id"]
    r = client.get("/v1/runs/missing")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert "correlation_id" in r.json()


async def test_sse_cursor_and_replay(client, tenant):
    r = client.post("/v1/examples/smoke", headers={"Idempotency-Key": uid()}).json()
    assert await finish(tenant, r["id"]) == "SUCCEEDED"
    events = client.get(f"/v1/runs/{r['id']}/events", headers={"Last-Event-ID": "2"})
    assert events.status_code == 200 and "id: 1\n" not in events.text and "id: 3\n" in events.text
    replay = client.get(f"/v1/runs/{r['id']}/replay").json()
    assert replay["projection"]["status"] == "SUCCEEDED"


def test_skill_immutable_version(client):
    body = {
        "name": "my-skill",
        "description": "small",
        "version": "1.0.0",
        "content": "read tests",
        "source": "test",
        "license": "MIT",
    }
    assert client.post("/v1/skills", json=body).status_code == 201
    assert client.post("/v1/skills", json=body).status_code == 409


async def test_unconfigured_model_pauses_honestly(tenant, make_run):
    id = make_run(model="configured")
    await Worker().once(tenant)
    with db.transaction(tenant) as s:
        r = db.get(s, db.Run, tenant, id)
        assert r.status == "PAUSED" and "MODEL_NOT_CONFIGURED" in r.state["reason"]


def test_memory_is_tenant_scoped(client, tenant):
    result = client.post("/v1/memories", json={"title": "fact", "content": "private", "source": "test"})
    assert result.status_code == 201
    other = "other-" + uid()
    service.provision(other, "tester")
    with db.transaction(other) as s:
        assert s.scalar(select(db.Memory).where(db.Memory.id == result.json()["id"])) is None


def test_memory_expiry_uses_instants_not_lexical_offsets():
    from datetime import datetime

    current = datetime.fromisoformat("2026-09-24T05:00:00+00:00")
    expired = db.Memory(data={"expires_at": "2026-09-24T12:30:00+08:00"})
    future = db.Memory(data={"expires_at": "2026-09-24T14:00:00+08:00"})
    assert not service.memory_valid(expired, current)
    assert service.memory_valid(future, current)


def test_memory_requires_timezone_when_expiry_supplied(client):
    result = client.post(
        "/v1/memories", json={"title": "fact", "content": "fact", "source": "user", "expires_at": "2026-09-25T01:00:00"}
    )
    assert result.status_code == 422
