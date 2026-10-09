"""Paginated metadata catalog; content and privileged detail have dedicated endpoints."""

from sqlalchemy import select

from . import db
from .auth import require_admin
from .domain import Fault
from .pagination import records, visible

COLLECTIONS = {"projects": db.Project, "connections": db.ToolVersion, "evaluations": db.Evaluation,
               "skills": db.SkillVersion, "memories": db.Memory, "artifacts": db.Artifact,
               "approvals": db.Approval, "events": db.Event, "actions": db.Action, "checkpoints": db.Checkpoint,
               "maintenance-jobs": db.PolicyVersion, "datasets": db.EvaluationDataset}


def page(s, actor, collection, cursor=None, limit=50, status=None, run_id=None):
    cls = COLLECTIONS.get(collection)
    if not cls:
        raise Fault("CATALOG_COLLECTION", "Unknown catalog collection", 404)
    if collection in {"connections", "evaluations", "maintenance-jobs", "datasets"}:
        require_admin(actor)
    query = select(cls).where(cls.tenant_id == actor.tenant)
    if collection == "connections":
        query = query.where(cls.data["kind"].as_string().in_(["mcp", "a2a"]))
    if collection == "maintenance-jobs":
        query = query.where(cls.data["kind"].as_string() == "maintenance_job")
    if hasattr(cls, "run_id"):
        query = query.join(db.Run, (db.Run.tenant_id == cls.tenant_id) & (db.Run.id == cls.run_id)).where(visible(s, actor))
        if run_id:
            query = query.where(cls.run_id == run_id)
    elif collection == "approvals":
        query = query.join(db.Action, (db.Action.tenant_id == cls.tenant_id) & (db.Action.id == cls.action_id))
        query = query.join(db.Run, (db.Run.tenant_id == cls.tenant_id) & (db.Run.id == db.Action.run_id)).where(visible(s, actor, "approve"))
        if run_id:
            query = query.where(db.Run.id == run_id)
    elif collection in {"projects", "memories"} and not actor.admin:
        authorization = s.get(db.Authorization, (actor.tenant, actor.actor))
        grants = authorization.data.get("project_permissions", {}) if authorization else {}
        if "read" not in grants.get("*", []):
            allowed = [key for key, operations in grants.items() if "read" in operations]
            query = query.where(cls.id.in_(allowed) if collection == "projects" else cls.data["project"].as_string().in_(allowed))
    if status:
        if not hasattr(cls, "status"):
            if cls != db.Approval:
                raise Fault("CATALOG_FILTER", "This collection has no status filter", 422)
            query = query.where(cls.decision == status)
        else:
            query = query.where(cls.status == status)
    values, next_cursor = records(s, actor, cls, query, [collection, status, run_id], cursor, limit)
    def view(row):
        result = {"id": row.id, "created_at": row.created_at.isoformat()}
        if hasattr(row, "status"):
            result["status"] = row.status
        if hasattr(row, "run_id"):
            result["run_id"] = row.run_id
        if cls == db.Artifact:
            result.update(name=row.name, kind=row.kind, digest=row.ref["digest"], bytes=row.ref["bytes"], verified=row.verified)
        elif cls == db.Action:
            result.update(tool=row.tool, effect_class=row.effect_class, attempt=row.attempt)
        elif cls == db.Event:
            from .presenters import event_view

            result.update(event_view(row))
            result.update(seq=row.seq, type=row.type, message=row.payload.get("message"))
        elif cls == db.Approval:
            result.update(action_id=row.action_id, status=row.decision, expires_at=row.expires_at.isoformat())
        else:
            allowed = {"name", "title", "description", "version", "project", "kind", "protocol", "url", "split", "dataset_id", "digest"}
            result.update({key: row.data[key] for key in allowed & row.data.keys()})
        return result
    return {"items": [view(row) for row in values], "next_cursor": next_cursor}
