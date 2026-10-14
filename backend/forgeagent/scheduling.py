"""Work-conserving tenant admission; durable quotas stay in Worker.claim."""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from .config import settings
from .telemetry import slot_utilization

log = logging.getLogger("forge.worker")


async def run_scheduler(worker):
    # Blocking effect work and lease renewals need distinct spare execution capacity.
    # The pool remains bounded even at the supported maximum number of run slots.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
        max_workers=settings.worker_slots * 3 + 8, thread_name_prefix="forge-io",
    ))
    active, cursor, idle_until = {}, 0, {}
    try:
        while not worker.stopping:
            tenants = await asyncio.to_thread(worker.tenants)
            completed = [task for task in active if task.done()]
            worked = False
            for task in completed:
                try:
                    result = task.result()
                    worked = result or worked
                    idle_until[active[task]] = 0 if result else time.monotonic() + 0.1
                except Exception as exc:
                    log.error("Worker quantum failed: %s", type(exc).__name__)
            for task in completed:
                del active[task]
            # Admit the least-occupied eligible tenant, rotating equal choices.
            # Empty probes cool down, so busy tenants can borrow unused slots;
            # database claim still enforces tenant and root concurrency quotas.
            while tenants and len(active) < settings.worker_slots:
                rotated = tenants[cursor % len(tenants):] + tenants[:cursor % len(tenants)]
                eligible = [t for t in rotated if idle_until.get(t, 0) <= time.monotonic()]
                if not eligible:
                    break
                tenant = min(eligible, key=lambda t: sum(value == t for value in active.values()))
                cursor = (tenants.index(tenant) + 1) % len(tenants)
                active[asyncio.create_task(worker.once(tenant))] = tenant
            slot_utilization.observe(len(active) / settings.worker_slots)
            if active:
                await asyncio.wait(active, timeout=1 if worked else 0.1, return_when=asyncio.FIRST_COMPLETED)
            if not worked and not any(not task.done() for task in active):
                await asyncio.sleep(0.1)
    finally:
        # Stop admitting new work and let existing quanta reach their durable boundary.
        await asyncio.gather(*active, return_exceptions=True)
