from forgeagent import db, operations, reducer, service
from sqlalchemy import event


def test_revision_is_transactional_and_detects_content_only_edits(client, tenant):
    before = client.get("/v1/workspace/revision").json()
    with db.transaction(tenant) as s:
        s.add(db.Memory(tenant_id=tenant, id="changing", data={"content": "first"}))
    created = client.get("/v1/workspace/revision").json()
    assert created != before
    try:
        with db.transaction(tenant) as s:
            db.get(s, db.Memory, tenant, "changing").data = {"content": "rolled back"}
            s.flush()
            raise RuntimeError("rollback")
    except RuntimeError:
        pass
    assert client.get("/v1/workspace/revision").json() == created
    with db.transaction(tenant) as s:
        db.get(s, db.Memory, tenant, "changing").data = {"content": "committed"}
    assert client.get("/v1/workspace/revision").json() != created


def test_rebuild_materializes_one_checkpoint_and_diagnostics_bound_ids(tenant, make_run):
    run_id = make_run()
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id, True)
        service.checkpoint(s, run)
        checkpoint = db.rows(s, db.Checkpoint, tenant, run_id=run_id)[0]
        for index in range(110):
            s.add(db.Checkpoint(tenant_id=tenant, run_id=run_id, status="READY", data=checkpoint.data))
            s.add(db.Outbox(tenant_id=tenant, status="failed", data={}))
    loaded = []
    with db.transaction(tenant) as s:
        def record(session, instance):
            if isinstance(instance, db.Checkpoint):
                loaded.append(instance.id)
        event.listen(s, "loaded_as_persistent", record)
        projection = reducer.rebuild(s, tenant, run_id)
        assert projection["status"] == "QUEUED" and len(loaded) == 1
        check = next(item for item in operations.health(s, tenant)["checks"] if item["name"] == "outbox_attention")
        assert check["count"] == 110 and len(check["resource_ids"]) == 100 and check["ids_truncated"]
