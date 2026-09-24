import os
import time

from forgeagent import db
from forgeagent.maintenance import collect
from forgeagent.storage import objects


def test_gc_never_deletes_referenced_or_active_objects(tenant, make_run):
    id = make_run()
    orphan = objects.put(tenant, id, b"orphan upload after failed transaction")
    path = objects.root / orphan["key"]
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))
    with db.transaction(tenant) as s:
        assert collect(s, tenant, apply=True)["objects"] == []
        run = db.get(s, db.Run, tenant, id)
        reference = run.state["workspace_ref"]
        run.status = "CANCELLED"
    with db.transaction(tenant) as s:
        preview = collect(s, tenant)
        assert preview["objects"] == [{"key": orphan["key"], "bytes": orphan["bytes"]}]
        assert path.exists()
        collect(s, tenant, apply=True)
    assert not path.exists()
    assert objects.get(tenant, reference)


def test_gc_cannot_cross_tenants(tenant, make_run):
    id = make_run()
    ref = objects.put(tenant, id, b"private")
    with db.transaction("unrelated") as s:
        assert collect(s, "unrelated", apply=True)["objects"] == []
    assert objects.get(tenant, ref) == b"private"
