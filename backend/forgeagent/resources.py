"""Atomic root quotas. Interrupted executions retain their conservative CPU charge."""

import asyncio
import math

from . import db
from .domain import Fault, canonical, digest
from .storage import objects


def admit_cpu(tenant, run_id, operation, timeout):
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id, True)
        root = db.get(s, db.Run, tenant, run.root_id)
        remaining = root.state["budget"]["max_wall_seconds"] - (db.clock(s) - root.created_at).total_seconds()
        if remaining < 1:
            raise Fault("ROOT_WALL_LIMIT", "Root wall-clock quota exhausted")
        upper = min(timeout, 300, math.floor(remaining))
        account = db.get(s, db.BudgetAccount, tenant, run.root_id, True)
        usage = account.resources or {}
        charges = usage.get("cpu_charges", {})
        if operation in charges:
            raise Fault("EXECUTION_ALREADY_ADMITTED", "Use a new attempt after an interrupted execution")
        spent = usage.get("cpu_seconds", 0)
        if spent + upper > root.state["budget"].get("max_cpu_seconds", 3600):
            raise Fault("ROOT_CPU_LIMIT", "Root CPU quota cannot cover this bounded execution")
        account.resources = {**usage, "cpu_seconds": spent + upper,
                             "cpu_charges": {**charges, operation: {"upper": upper, "settled": False}}}
        db.emit(s, run, "RESOURCE_ADMITTED", "CPU upper bound charged before sandbox dispatch", operation=operation, seconds=upper)
        return upper


def settle_cpu(tenant, run_id, operation, result):
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id, True)
        account = db.get(s, db.BudgetAccount, tenant, run.root_id, True)
        usage = account.resources or {}
        charges = usage.get("cpu_charges", {})
        charge = charges[operation]
        if charge["settled"]:
            return
        # One CPU is enforced by the manager. Elapsed wall time is a conservative
        # CPU upper bound; missing trusted timing retains the full reservation.
        elapsed = charge["upper"] if result.get("session") else result.get("elapsed_seconds", charge["upper"])
        actual = min(charge["upper"], max(0, math.ceil(elapsed)))
        account.resources = {**usage, "cpu_seconds": usage["cpu_seconds"] - charge["upper"] + actual,
                             "cpu_charges": {**charges, operation: {**charge, "settled": True, "actual": actual}}}
        db.emit(s, run, "RESOURCE_SETTLED", "Sandbox CPU upper bound settled", operation=operation, seconds=actual)


async def execute(tenant, run_id, operation, root, argv, image, timeout=120, readonly=True, build=None):
    from .sandbox import sandbox

    admitted = await asyncio.to_thread(admit_cpu, tenant, run_id, operation, timeout)
    result = await sandbox.execute(root, argv, image, admitted, readonly, build=build)
    await asyncio.to_thread(settle_cpu, tenant, run_id, operation, result)
    return result


def put(tenant, run_id, value):
    data = value if isinstance(value, bytes) else canonical(value)
    checksum = digest(data)
    with db.transaction(tenant) as s:
        run = db.get(s, db.Run, tenant, run_id, True)
        root = db.get(s, db.Run, tenant, run.root_id)
        account = db.get(s, db.BudgetAccount, tenant, run.root_id, True)
        usage = account.resources or {}
        charges = usage.get("storage_charges", {})
        key = run_id + ":" + checksum
        if key not in charges:
            spent = usage.get("storage_bytes", 0)
            if spent + len(data) > root.state["budget"].get("max_storage_bytes", 134217728):
                raise Fault("ROOT_STORAGE_LIMIT", "Root stored snapshot/evidence quota exhausted")
            account.resources = {**usage, "storage_bytes": spent + len(data),
                                 "storage_charges": {**charges, key: len(data)}}
            db.emit(s, run, "STORAGE_ADMITTED", "Object bytes charged before publication", digest=checksum, bytes=len(data))
    # Failed uploads retain the charge; a retry of the same bytes is not charged twice.
    return objects.put(tenant, run_id, value)
