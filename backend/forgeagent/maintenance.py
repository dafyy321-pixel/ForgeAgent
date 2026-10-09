"""Reference-aware reclamation. Reachable evidence never expires implicitly."""

import hashlib
import re
import shutil
import time

from sqlalchemy import JSON, select

from . import db
from .config import settings
from .domain import TERMINAL, UNSETTLED, Fault, digest
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


RETENTION = {
    "recovery": "Retain while referenced by any run/checkpoint; required for replay and restore",
    "deliverable": "Retain while referenced by artifacts or verification evidence",
    "knowledge": "Retain while referenced by memory, skills, datasets or evaluation",
    "audit": "Retain while referenced by events, calls, actions, controls or policies",
    "orphan": "Reclaim only settled task namespaces after at least 24 hours",
}


def reference_graph(s, tenant):
    graph = {}
    for table in db.Base.metadata.sorted_tables:
        if "tenant_id" not in table.c:
            continue
        columns = [c for c in table.c if isinstance(c.type, JSON)]
        category = ("recovery" if table.name in {"runs", "checkpoints", "workspace_snapshots", "projects", "semantic_manifests"}
                    else "deliverable" if table.name in {"artifacts", "verification_results"}
                    else "knowledge" if table.name in {"memories", "skill_versions", "evaluation_datasets", "evaluation_results"}
                    else "audit")
        if columns:
            for row in s.execute(select(table.c.id, *columns).where(table.c.tenant_id == tenant)):
                for column, value in zip(columns, row[1:], strict=True):
                    for key in reference_keys(value):
                        graph.setdefault(key, []).append({"table": table.name, "id": row[0],
                                                          "column": column.name, "class": category})
    return graph


def settled_runs(s, tenant):
    return [run for run in db.rows(s, db.Run, tenant)
            if run.status in TERMINAL and not run.lease_owner
            and not any(a.status in UNSETTLED for a in db.rows(s, db.Action, tenant, run_id=run.id))
            and not any(e.status != "settled" and e.data.get("run_id") == run.id
                        for e in db.rows(s, db.BudgetEntry, tenant))]


def collect(s, tenant, apply=False, min_age_hours=24):
    if min_age_hours < 24:
        raise Fault("GC_GRACE", "Object GC requires at least a 24-hour grace period", 422)
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "gc:" + tenant})
    from .knowledge import lock

    lock(s, tenant)
    # Only terminal run namespaces are eligible. New/active runs and failed creation namespaces are retained.
    terminal = {r.id for r in settled_runs(s, tenant)}
    references = reference_graph(s, tenant)
    scope = hashlib.sha256(tenant.encode()).hexdigest()
    candidates = []
    cutoff = time.time() - min_age_hours * 3600
    for run_id in sorted(terminal):
        for entry in objects.inventory(tenant, run_id):
            key, name = entry["key"], entry["key"].rsplit("/", 1)[-1]
            if (not key.startswith(f"{scope}/{run_id}/") or key in references or entry["modified"] > cutoff
                or not re.fullmatch(r"[a-f0-9]{64}(\.[a-f0-9-]+\.tmp)?|[a-f0-9-]{36}\.tmp", name)):
                continue
            candidates.append({k: v for k, v in entry.items() if k != "modified"})
    if apply:
        objects.delete(candidates)
    if apply:
        s.add(
            db.PolicyVersion(
                tenant_id=tenant,
                status="audit",
                data={
                    "kind": "object_gc",
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
        "retention": RETENTION,
        "reference_classes": {category: sum(any(link["class"] == category for link in links)
                                             for links in references.values()) for category in RETENTION if category != "orphan"},
    }


def clean_workspaces(s, tenant, apply=False, min_age_hours=24):
    if min_age_hours < 24:
        raise Fault("GC_GRACE", "Workspace cleanup requires a 24-hour grace period", 422)
    base = settings.data_dir.resolve() / "workspaces"
    scope = base / digest(tenant)[7:23]
    cutoff, candidates = time.time() - min_age_hours * 3600, []
    settled = {run.id for run in settled_runs(s, tenant)}
    for original in db.rows(s, db.Run, tenant):
        run = db.get(s, db.Run, tenant, original.id, True)
        directory = scope / run.id
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if not re.fullmatch(r"(?:\d+|verify-\d+)(?:\.(?:stage|backup)-[a-f0-9-]{36})?", path.name):
                continue
            epoch = int(path.name.split(".", 1)[0].removeprefix("verify-"))
            if run.id not in settled and epoch >= run.epoch:
                continue
            if any(p.is_symlink() or p.is_junction() for p in [path, directory, scope, base]):
                raise Fault("WORKSPACE_SCOPE", "Workspace cleanup refused a filesystem link", 403)
            if not path.resolve().is_relative_to(base) or path.stat().st_mtime > cutoff:
                continue
            candidates.append(str(path))
            if apply:
                if (path / ".git").is_file():
                    from .repository import mirror, remove_worktree

                    repository = run.state.get("repository")
                    if not repository:
                        raise Fault("WORKSPACE_REPOSITORY", "Worktree has no pinned repository")
                    remove_worktree(mirror(tenant, repository), path)
                else:
                    shutil.rmtree(path)
    if apply:
        s.add(db.PolicyVersion(tenant_id=tenant, status="audit", data={"kind": "workspace_gc", "paths": candidates}))
    return {"dry_run": not apply, "paths": candidates}
