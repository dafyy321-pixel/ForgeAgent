import asyncio

from forgeagent.config import settings
from forgeagent.worker import Worker


async def test_busy_tenant_borrows_capacity_from_registered_idle_tenants(monkeypatch):
    monkeypatch.setattr(settings, "worker_slots", 4)
    worker = Worker()
    worker.tenants = lambda: ["busy", *[f"idle-{n}" for n in range(16)]]
    release, full = asyncio.Event(), asyncio.Event()
    occupancy, peak = 0, 0

    async def once(tenant):
        nonlocal occupancy, peak
        if tenant != "busy":
            return False
        occupancy += 1
        peak = max(peak, occupancy)
        if occupancy == 4:
            full.set()
        await release.wait()
        occupancy -= 1
        return True

    worker.once = once
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(full.wait(), 2)
    finally:
        worker.stopping = True
        release.set()
        await task
    assert peak == 4 and occupancy == 0
