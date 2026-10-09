"""Paginated metadata catalog; content and privileged detail have dedicated endpoints."""

from sqlalchemy import func, or_, select

from . import db
from .auth import require_admin
from .domain import Fault
from .pagination import records, visible

COLLECTIONS = {"projects": db.Project, "connections": db.ToolVersion, "evaluations": db.Evaluation,
               "skills": db.SkillVersion, "memories": db.Memory, "artifacts": db.Artifact,
               "approvals": db.Approval, "events": db.Event, "actions": db.Action, "checkpoints": db.Checkpoint,
               "maintenance-jobs": db.PolicyVersion, "datasets": db.EvaluationDataset}


def page(s, actor, collection, cursor=None, limit=50, status=None, run_id=None, project=None, verified=None):
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
    if project:
        if hasattr(cls, "run_id") or collection == "approvals":
            query = query.where(db.Run.project_id == project)
        elif collection == "memories":
            query = query.where(or_(cls.data["project"].as_string().is_(None), cls.data["project"].as_string() == project))
        elif collection == "projects":
            query = query.where(cls.id == project)
    if verified is not None:
        if cls != db.Artifact:
            raise Fault("CATALOG_FILTER", "Verified filter is only supported for artifacts", 422)
        query = query.where(cls.verified == verified)
    if collection == "skills":
        query = query.where(cls.status != "erased")
    if status:
        if not hasattr(cls, "status"):
            if cls != db.Approval:
                raise Fault("CATALOG_FILTER", "This collection has no status filter", 422)
            query = query.where(cls.decision == status)
        else:
            query = query.where(cls.status == status)
    values, next_cursor = records(s, actor, cls, query, [collection, status, run_id, project, verified], cursor, limit)
    calls = skill_calls(s, actor, [row.id for row in values]) if cls == db.SkillVersion else {}
    actions = {row.id: row for row in s.scalars(select(db.Action).where(db.Action.tenant_id == actor.tenant,
        db.Action.id.in_([row.action_id for row in values])))} if cls == db.Approval else {}
    def view(row):
        result = {"id": row.id, "created_at": row.created_at.isoformat()}
        if hasattr(row, "status"):
            result["status"] = row.status
        if hasattr(row, "run_id"):
            result["run_id"] = row.run_id
        if cls == db.Artifact:
            result.update(name=row.name, kind=row.kind, digest=row.ref["digest"], bytes=row.ref["bytes"], verified=row.verified, version=row.version)
        elif cls == db.Action:
            result.update(tool=row.tool, effect_class=row.effect_class, attempt=row.attempt)
        elif cls == db.Event:
            from .presenters import event_view

            result.update(event_view(row))
            result.update(seq=row.seq, type=row.type, message=row.payload.get("message"))
        elif cls == db.Approval:
            action = actions[row.action_id]
            result.update(action_id=row.action_id, run_id=action.run_id, tool=action.tool,
                          status=row.decision, expires_at=row.expires_at.isoformat(), reason=row.reason)
        elif cls == db.Memory:
            result.update({key: row.data[key] for key in {"title", "content", "kind", "source", "project"} & row.data.keys()})
        elif cls == db.SkillVersion:
            result.update({key: row.data[key] for key in {"name", "version", "description", "category", "digest"} & row.data.keys()},
                          enabled=row.status == "active", calls=calls.get(row.id, 0))
        else:
            allowed = {"name", "title", "description", "version", "project", "kind", "protocol", "url", "split", "dataset_id", "digest"}
            result.update({key: row.data[key] for key in allowed & row.data.keys()})
        return result
    return {"items": [view(row) for row in values], "next_cursor": next_cursor}


def skill_calls(s, actor, ids):
    if not ids:
        return {}
    skill_id = db.Action.args["skill_id"].as_string()
    return dict(s.execute(select(skill_id, func.count()).join(db.Run,
        (db.Run.tenant_id == db.Action.tenant_id) & (db.Run.id == db.Action.run_id)).where(
            visible(s, actor), db.Action.tool == "skill.read", db.Action.status == "SUCCEEDED",
            skill_id.in_(ids)).group_by(skill_id)).all())
