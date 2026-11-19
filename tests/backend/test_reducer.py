import json
from copy import deepcopy
from datetime import timedelta

import pytest
from forgeagent import db, operations, reducer, service
from forgeagent.domain import Fault, digest
from forgeagent.reducer import EventReducer, rebuild
from forgeagent.worker import Worker


async def test_full_history_and_checkpoint_reduction_match_live_projection(tenant, make_run):
    run_id = make_run()
    worker = Worker()
    for _ in range(5):
        await worker.once(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        projection = {}
        events = sorted(db.rows(s, db.Event, tenant, run_id=run_id), key=lambda e: e.seq)
        for event in events:
            assert "projection" not in event.payload
            projection = EventReducer.apply(projection, event.payload)
        assert projection == db.projection(run) == rebuild(s, tenant, run_id)
        first = db.rows(s, db.Checkpoint, tenant, run_id=run_id)[0]
        assert rebuild(s, tenant, run_id, checkpoint_id=first.id) == projection
        assert service.replay(s, tenant, run_id)["projection"] == projection
        assert run.status == "SUCCEEDED"


def test_legacy_event_upgrades_to_delta_and_rejects_digest_tampering():
    legacy = {"state": {"keep": "fact", "remove": True}, "status": "ACTIVE", "wait_reason": None}
    before = EventReducer.apply({}, {"schema_version": 1, "projection": legacy})
    after = {"state": {"keep": "fact", "new": "result"}, "status": "PAUSED", "wait_reason": None}
    payload = {"schema_version": 2, "transition": EventReducer.transition(before, after),
               "prior_digest": digest(before), "projection_digest": digest(after)}
    assert EventReducer.apply(before, payload) == after
    corrupted = deepcopy(payload)
    corrupted["transition"]["state_set"]["new"] = "wrong"
    with pytest.raises(Fault, match="digest mismatch"):
        EventReducer.apply(before, corrupted)
    with pytest.raises(Fault, match="preceding committed"):
        EventReducer.apply({}, payload)
    with pytest.raises(Fault, match="schema"):
        EventReducer.apply(before, {"schema_version": 999})


def test_worker_quarantines_unjournaled_projection_changes(tenant, make_run):
    run_id = make_run()
    worker = Worker()
    _, epoch = worker.claim(tenant)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        run.lease_owner = run.lease_until = None
        run.state = {**run.state, "input": "uncommitted corruption"}
    assert worker.claim(tenant) is None
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert run.status == "PAUSED" and run.epoch == epoch
        assert "REPLAY_DIVERGENCE" in run.state["reason"]
        assert json.dumps(rebuild(s, tenant, run_id))


def test_temporary_history_failure_retries_without_starving_next_run(tenant, make_run, monkeypatch):
    worker = Worker()
    broken = make_run()
    _, epoch = worker.claim(tenant)
    worker.release(tenant, broken, epoch)
    healthy = make_run()

    def fail_one(s, tenant_id, run_id, *args, **kwargs):
        if run_id == broken:
            raise Fault("OBJECT_UNAVAILABLE", "read failed", retryable=True)
        return rebuild(s, tenant_id, run_id, *args, **kwargs)

    monkeypatch.setattr(reducer, "rebuild", fail_one)
    assert worker.claim(tenant) is None
    assert worker.claim(tenant)[0] == healthy
    with db.transaction(tenant) as s:
        job = db.get(s, db.Job, tenant, broken)
        run = db.get(s, db.Run, tenant, broken)
        assert job.data["replay_error"] == "OBJECT_UNAVAILABLE" and run.epoch == epoch
        assert job.status == "READY" and run.available_at > db.clock(s)
        run.available_at = db.clock(s) - timedelta(seconds=1)
    monkeypatch.setattr(reducer, "rebuild", rebuild)
    assert worker.claim(tenant)[0] == broken


def test_invalid_persisted_checkpoint_is_isolated_from_other_jobs(tenant, make_run):
    broken = make_run()
    healthy = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, broken)
        s.add(db.Checkpoint(tenant_id=tenant, run_id=broken, status="READY",
                            data={"schema_version": 2, "event_seq": run.seq, "manifest_digest": "invalid"}))
    worker = Worker()
    assert worker.claim(tenant) is None
    assert worker.claim(tenant)[0] == healthy
    with db.transaction(tenant) as s:
        job = db.get(s, db.Job, tenant, broken)
        assert job.status == "QUARANTINED" and job.data["replay_error"] == "CHECKPOINT_CORRUPT"
        alert = next(a for a in operations.health(s, tenant)["alerts"] if a["code"] == "REPLAY_QUARANTINED")
        assert broken in alert["resource_ids"]
