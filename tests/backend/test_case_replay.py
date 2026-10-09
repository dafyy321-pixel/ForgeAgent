from forgeagent import db


def test_case_replay_pages_are_read_only_and_omit_event_state_bodies(client, tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        for i in range(3):
            db.emit(s, run, "CASE_NOTE", f"Note {i}")
        before = (run.seq, run.version)
    first = client.get(f"/v1/runs/{run_id}/case-replay?limit=2").json()
    assert first["read_only"] and len(first["events"]) == 2 and first["next_after"] == 2
    assert set(first["events"][0]) == {"seq", "type", "time", "message"}
    assert "protected_tests" not in str(first) and "workspace_ref" not in str(first)
    second = client.get(f"/v1/runs/{run_id}/case-replay?after=2&through_seq={first['through_seq']}").json()
    assert all(e["seq"] > 2 for e in second["events"]) and second["next_after"] is None
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id)
        assert (run.seq, run.version) == before
        assert not db.rows(s, db.Action, tenant, run_id=run_id)
        assert not db.rows(s, db.ModelCall, tenant, run_id=run_id)
    assert client.get(f"/v1/runs/{run_id}/case-replay?after=999999").status_code == 422


def test_case_replay_erased_history_is_a_tombstone(client, tenant, make_run):
    from forgeagent import knowledge_erasure

    run_id = make_run()
    with db.transaction(tenant) as s:
        knowledge_erasure.prepare(s, tenant, None, "tester", run_id=run_id)
    response = client.get(f"/v1/runs/{run_id}/case-replay")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "erased" and data["events"] == []
    assert data["task"] == {"goal": "[erased]", "allowed_paths": [], "deliverables": []}
    assert "Fix add" not in response.text and data["verification"] is None
