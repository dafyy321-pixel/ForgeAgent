"""Conservative local object reclamation for settled tasks only."""

import hashlib
import re
import time

from sqlalchemy import JSON, select

from . import db
from .domain import TERMINAL, Fault
from .storage import objects


def reference_keys(value):
    if isinstance(value, dict):
        if {"key", "digest", "bytes"} <= value.keys():
            yield value["key"]
        for nested in value.values():
            yield from reference_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from reference_keys(nested)


def collect(s, tenant, apply=False, min_age_hours=24):
    if objects.s3:
        raise Fault("GC_STORAGE", "Local GC does not delete S3 objects; configure a reviewed S3 retention job", 422)
    if min_age_hours < 24:
        raise Fault("GC_GRACE", "Object GC requires at least a 24-hour grace period", 422)
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "gc:" + tenant})
    # Only terminal run namespaces are eligible. New/active runs and failed creation namespaces are retained.
    terminal = {r.id for r in db.rows(s, db.Run, tenant) if r.status in TERMINAL and not r.lease_owner}
    references = set()
    for table in db.Base.metadata.sorted_tables:
        if "tenant_id" not in table.c:
            continue
        columns = [c for c in table.c if isinstance(c.type, JSON)]
        if columns:
            for row in s.execute(select(*columns).where(table.c.tenant_id == tenant)):
                for value in row:
                    references.update(reference_keys(value))
    scope = hashlib.sha256(tenant.encode()).hexdigest()
    root = objects.root / scope
    candidates = []
    cutoff = time.time() - min_age_hours * 3600
    for run_id in sorted(terminal):
        directory = root / run_id
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id) or not directory.is_dir():
            continue
        for path in directory.iterdir():
            key = f"{scope}/{run_id}/{path.name}"
            if key in references or not re.fullmatch(r"[a-f0-9]{64}(\.[a-f0-9-]+\.tmp)?", path.name):
                continue
            parents = [path, directory, root, objects.root]
            if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in parents):
                continue
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_mtime > cutoff:
                continue
            size = path.stat().st_size
            candidates.append({"key": key, "bytes": size})
            if apply:
                path.unlink()
    if apply:
        s.add(
            db.PolicyVersion(
                tenant_id=tenant,
                status="audit",
                data={
                    "kind": "local_gc",
                    "objects": candidates,
                    "min_age_hours": min_age_hours,
                    "reclaimed_bytes": sum(x["bytes"] for x in candidates),
                },
            )
        )
    return {
        "dry_run": not apply,
        "objects": candidates,
        "bytes": sum(x["bytes"] for x in candidates),
        "referenced_objects": len(references),
        "terminal_namespaces": len(terminal),
        "retention": "All referenced audit evidence is retained. Active and unregistered namespaces are excluded.",
    }
