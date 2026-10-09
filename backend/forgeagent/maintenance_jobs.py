"""Persistent, tenant-scoped, repeatable maintenance with bounded retries and dead letters.

Only idempotent housekeeping handlers are allowed. No model or remote write dispatch.
"""

import asyncio
import logging
import signal
from datetime import timedelta

from sqlalchemy import select

from . import db
from .config import settings
from .domain import Fault, digest, uid
from .telemetry import observed

KINDS = {"objects", "workspaces", "erasure", "containers"}


def enqueue(s, tenant, kind, payload, key):
    if kind not in KINDS:
        raise Fault("MAINTENANCE_KIND", "Unknown maintenance operation", 422)
    identity = "maintenance-" + digest(key)[7:]
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": tenant + identity})
    existing = s.get(db.PolicyVersion, (tenant, identity))
    intent = {"kind": kind, "payload": payload}
    if existing:
        if existing.data["intent"] != intent:
            raise Fault("MAINTENANCE_CONFLICT", "Maintenance key already names another operation")
        return existing
    job = db.PolicyVersion(tenant_id=tenant, id=identity, status="queued", data={"kind": "maintenance_job",
        "intent": intent, "attempts": 0, "available_at": db.clock(s).isoformat(), "history": []})
    s.add(job)
    s.flush()
    return job


def claim(tenant, owner):
    with db.transaction(tenant) as s:
        time = db.clock(s)
        query = (select(db.PolicyVersion).where(db.PolicyVersion.tenant_id == tenant,
            db.PolicyVersion.data["kind"].as_string() == "maintenance_job",
            db.PolicyVersion.status.in_(["queued", "running"]))
            .order_by(db.PolicyVersion.created_at).with_for_update(skip_locked=True))
        from datetime import datetime

        for job in s.scalars(query):
            if datetime.fromisoformat(job.data["available_at"]) > time:
                continue
            attempts = job.data["attempts"] + 1
            if attempts > 5:
                job.status = "dead_letter"
                job.data = {**job.data, "last_error": "MAINTENANCE_INTERRUPTED"}
                continue
            job.status = "running"
            job.data = {**job.data, "owner": owner, "attempts": attempts,
                        "available_at": (time + timedelta(seconds=300)).isoformat()}
            return job.id, job.data["intent"]


@observed("maintenance.execute")
async def once(tenant, owner=None):
    owner = owner or uid()
    leased = await asyncio.to_thread(claim, tenant, owner)
    if not leased:
        return False
    job_id, intent = leased
    error, result = None, None
    try:
        kind, payload = intent["kind"], intent["payload"]
        if kind == "containers":
            from .sandbox_manager import clean_containers

            result = await clean_containers(tenant, payload.get("apply", False))
        elif kind == "erasure":
            from .knowledge_erasure import finish

            result = await asyncio.to_thread(finish, tenant, payload["runs"], payload["erasure_key"])
        else:
            from .maintenance import clean_workspaces, collect

            def execute():
                with db.transaction(tenant) as s:
                    return (collect if kind == "objects" else clean_workspaces)(s, tenant, **payload)
            result = await asyncio.to_thread(execute)
    except Exception as exc:
        error = exc.code if isinstance(exc, Fault) else "MAINTENANCE_FAILED"

    def settle():
        with db.transaction(tenant) as s:
            job = db.get(s, db.PolicyVersion, tenant, job_id, True)
            if job.status != "running" or job.data.get("owner") != owner:
                return
            dead = bool(error and job.data["attempts"] >= 5)
            job.status = "dead_letter" if dead else "queued" if error else "completed"
            job.data = {**job.data, "last_error": error, "result": result,
                "available_at": (db.clock(s) + timedelta(seconds=min(3600, 30 * 2 ** job.data["attempts"]))).isoformat(),
                "history": [*job.data.get("history", []), {"at": db.clock(s).isoformat(), "status": job.status, "error": error}]}
    await asyncio.to_thread(settle)
    return True


def main():
    from .telemetry import configure

    configure()
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)

    def tenants():
        if settings.worker_tenants:
            return settings.worker_tenants.split(",")
        with db.Session() as s:
            return list(s.scalars(select(db.Tenant.id)))

    async def run():
        while not stopped:
            for tenant in await asyncio.to_thread(tenants):
                try:
                    await once(tenant)
                except Exception as exc:
                    logging.getLogger("forge.maintenance").error("Maintenance worker failure: %s", type(exc).__name__)
            await asyncio.sleep(1)
    asyncio.run(run())


if __name__ == "__main__":
    main()
