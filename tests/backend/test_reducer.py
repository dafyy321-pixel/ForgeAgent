import json
from copy import deepcopy

import pytest
from forgeagent import db, service
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
