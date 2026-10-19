"""Opt-in performance samples; no provider calls or external side effects."""
import asyncio
import json
import os

import pytest
from forgeagent import db
from forgeagent.config import settings
from forgeagent.research import snapshot
from forgeagent.worker import Worker

pytestmark = pytest.mark.skipif(os.environ.get("FORGE_TEST_PERFORMANCE") != "1", reason="Independent performance job only")


async def test_abandoned_lease_pickup_profile(tenant, make_run, monkeypatch, record_property):
    monkeypatch.setattr(settings, "lease_seconds", 1)
    run_id = make_run()
    abandoned = Worker(target_run=run_id)
    assert abandoned.claim(tenant)
    # Let an actual lease expire without a heartbeat or release. This measures
    # takeover after expiry, not OS process crash detection or remote effects.
    await asyncio.sleep(1.1)
    monkeypatch.setattr(settings, "lease_seconds", 30)
    replacement = Worker(target_run=run_id)
    for _ in range(8):
        await replacement.once(tenant)
        with db.transaction(tenant) as session:
            run = db.get(session, db.Run, tenant, run_id)
            if run.status == "SUCCEEDED":
                report = snapshot(session, run)
                assert len(report["recovery_pickup_seconds"]) == 1
                assert report["recovery_to_progress_seconds"]
                record_property("takeover_profile", json.dumps({
                    "pickup_seconds": report["recovery_pickup_seconds"],
                    "to_progress_seconds": report["recovery_to_progress_seconds"],
                    "external_duplicate_effects": None,
                    "scope": "Abandoned fixture lease; no process kill, provider or external effects",
                }))
                return
    pytest.fail("Replacement worker did not finish the fixture")
