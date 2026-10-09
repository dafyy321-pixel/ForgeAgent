from datetime import timedelta

from sqlalchemy import func, select

from . import db, service
from .auth import run_access
from .domain import canonical, digest


def run_cache(s, runs):
    if not runs:
        return {}
    tenant, ids = runs[0].tenant_id, [r.id for r in runs]
    roots = {r.root_id for r in runs}
    actions = {row.run_id: row for row in s.execute(select(db.Action.run_id, func.count().label("count"),
        func.bool_or(db.Action.status == "SUCCEEDED").label("succeeded"),
        func.bool_or(db.Action.status == "UNKNOWN").label("unknown")).where(
            db.Action.tenant_id == tenant, db.Action.run_id.in_(ids)).group_by(db.Action.run_id))}
    return {"accounts": {a.id: a for a in s.scalars(select(db.BudgetAccount).where(db.BudgetAccount.tenant_id == tenant, db.BudgetAccount.id.in_(roots)))},
            "actions": actions,
            "auth": {a.id: a for a in s.scalars(select(db.Authorization).where(db.Authorization.tenant_id == tenant, db.Authorization.id.in_({r.actor for r in runs})))},
            "roots": {r.id: r for r in s.scalars(select(db.Run).where(db.Run.tenant_id == tenant, db.Run.id.in_(roots)))},
            "unknown_billing": set(s.scalars(select(db.BudgetEntry.account_id).where(db.BudgetEntry.tenant_id == tenant, db.BudgetEntry.account_id.in_(roots), db.BudgetEntry.status == "unknown"))),
            "time": db.clock(s)}


def run_view(s, r, cache=None):
    st = r.state
    cache = cache or run_cache(s, [r])
    account = cache["accounts"][r.root_id]
    action_summary = cache["actions"].get(r.id)
    authorization = cache["auth"].get(r.actor)
    root = cache["roots"][r.root_id]
    deadline = root.created_at + timedelta(seconds=root.state["budget"]["max_wall_seconds"])
    if st.get("deadline"):
        from datetime import datetime

        deadline = min(deadline, datetime.fromisoformat(st["deadline"]))
    report = service.public_report(st["acceptance"], st["verification"]) if st.get("verification") else None
    elapsed = max(0, int(((r.updated_at if r.status in {"SUCCEEDED", "FAILED", "CANCELLED"} else cache["time"]) - r.created_at).total_seconds()))
    semantic = st["semantic"]
    return {
        "id": r.id,
        "title": st["title"],
        "description": st["task"]["goal"],
        "project": r.project_id,
        "status": r.status,
        "phase": st.get("reason") if r.status == "PAUSED" else r.phase,
        "progress": 100 if r.status == "SUCCEEDED" else 0,
        "progress_kind": "verified" if r.status == "SUCCEEDED" else "indeterminate",
        "progress_facts": st.get("progress", {}),
        "resources": account.resources,
        "budget_unknown": r.root_id in cache["unknown_billing"],
        "model": semantic["model_id"] or "尚未配置",
        "cost": account.spent / 1_000_000,
        "reserved": account.reserved / 1_000_000,
        "budget": account.limit_micros / 1_000_000,
        "tokens": st["tokens"],
        "steps": action_summary.count if action_summary else 0,
        "started": r.created_at.isoformat(),
        "duration": f"{elapsed // 60}m {elapsed % 60}s",
        "updatedAt": int(r.updated_at.timestamp() * 1000),
        "waitReason": r.wait_reason,
        "scope": st["task"].get("scope") or ", ".join(st["task"]["allowed_paths"]),
        "criteria": st["task"].get("criteria")
        or ["Registered acceptance contract passes", "Artifact digest matches evidence"],
        "milestones": [
            {"label": label, "done": done}
            for label, done in [
                ("任务契约", True),
                ("定位与规划", st["turn"] > 0),
                ("修改与执行", bool(action_summary and action_summary.succeeded)),
                ("独立验证", r.status == "SUCCEEDED"),
            ]
        ],
        "snapshot": {
            "model": semantic["model_id"] or "尚未配置",
            "skills": st["skills"],
            "memories": st["memories"],
            "time": int(r.created_at.timestamp() * 1000),
        },
        "checkpoint": {
            "id": st.get("checkpoint_id", ""),
            "workspace": bool(st.get("workspace_ref")),
            "compatible": semantic.get("implementation") == service.implementation_bindings()
            and semantic["tools_digest"] == digest(service.TOOLS),
            "permission": set(st["capabilities"])
            <= set(authorization.data.get("capabilities", []) if authorization else []),
            "environment": st.get("environment_ready") is True,
            "environment_status": "ready" if st.get("environment_ready") is True else "unavailable" if st.get("environment_ready") is False else "unknown",
        },
        "unknownEffect": bool(action_summary and action_summary.unknown),
        "cancelRequested": r.cancel_requested,
        "version": st["artifact_version"],
        "stateVersion": r.version,
        "deadline": int(deadline.timestamp() * 1000),
        "verification": {
            "status": "passed"
            if report and report["verdict"] == "PASS"
            else "failed"
            if report and report["verdict"] == "FAIL"
            else "inconclusive"
            if report and report["verdict"] == "INCONCLUSIVE"
            else "running"
            if r.phase == "VERIFYING" and r.status == "ACTIVE"
            else "not_run",
            "version": st["artifact_version"],
            "checks": report["checks"] if report else [],
        },
        "root_id": r.root_id,
        "parent_id": r.parent_id,
        "lease_epoch": r.epoch,
        "semantic_manifest": semantic,
        "inputRequired": st.get("input_required", False),
    }


def action_cache(s, actions):
    result = {a.id: {"input_deliveries": [], "attempts": []} for a in actions}
    if not actions:
        return result
    tenant = actions[0].tenant_id
    for cls, key in [(db.Outbox, "input_deliveries"), (db.Attempt, "attempts")]:
        for row in s.scalars(select(cls).where(cls.tenant_id == tenant, cls.data["action_id"].as_string().in_(result))):
            value = {"id": row.id, "status": row.status}
            if cls == db.Attempt:
                value.update(row.data)
            result[row.data["action_id"]][key].append(value)
    return result


def action_view(s, a, cache=None):
    cache = cache if cache is not None else action_cache(s, [a])
    return {
        "id": a.id,
        "run_id": a.run_id,
        "tool": a.tool,
        "args": a.args,
        "effect_class": a.effect_class,
        "effect_digest": a.effect_digest,
        "status": a.status,
        "idempotency_key": a.id,
        "receipt": a.receipt,
        **cache[a.id],
    }


def event_view(e):
    kind = next((kind for marker, kind in [("APPROVAL", "approval"), ("VERIFICATION", "verification"),
        ("CANCEL", "cancel"), ("LEASE", "recovery"), ("PAUSED", "recovery"),
        ("ACTION", "tool"), ("DECISION", "plan")] if marker in e.type), "task")
    return {"id": e.id, "runId": e.run_id, "kind": kind, "title": e.payload["message"],
            "detail": canonical(service.public_payload({k: v for k, v in e.payload.items()
                if k not in {"projection", "transition", "prior_digest", "projection_digest"}})).decode(),
            "time": int(e.created_at.timestamp() * 1000)}


def workspace(s, tenant, actor=None, project=None, status=None, q="", cursor=None, limit=50, run_id=None):
    from .catalog import page as catalog_page
    from .pagination import runs as page_runs

    runs, next_cursor = page_runs(s, actor, project, status, q, cursor, limit)
    if run_id and all(run.id != run_id for run in runs):
        focused = db.get(s, db.Run, tenant, run_id)
        if run_access(s, actor, focused):
            runs.append(focused)
    cache = run_cache(s, runs)
    authorizations = s.get(db.Authorization, (tenant, actor.actor))
    grants = authorizations.data.get("project_permissions", {}) if authorizations else {}
    def permitted(project_id, operation):
        return actor.admin or operation in grants.get(project_id, []) or operation in grants.get("*", [])
    by_id = {r.id: r for r in runs}
    approvals = []
    for p, a in s.execute(select(db.Approval, db.Action).join(db.Action,
        (db.Action.tenant_id == db.Approval.tenant_id) & (db.Action.id == db.Approval.action_id)).where(
            db.Approval.tenant_id == tenant, db.Action.run_id.in_([r.id for r in runs if permitted(r.project_id, "approve")])).order_by(
                db.Approval.created_at.desc(), db.Approval.id.desc()).limit(200)):
        if a.run_id not in by_id or (actor and not permitted(by_id[a.run_id].project_id, "approve")):
            continue
        r = by_id[a.run_id]
        approvals.append(
            {
                "id": p.id,
                "runId": r.id,
                "title": a.tool,
                "tool": a.tool,
                "target": a.args.get("connection", {}).get("url", r.project_id),
                "risk": a.effect_class,
                "status": p.decision,
                "digest": p.effect_digest,
                "expiresAt": int(p.expires_at.timestamp() * 1000),
                "version": r.state["artifact_version"],
                "reason": p.reason,
                "impact": "Execute exactly the parameters below once",
                "diff": canonical(a.args).decode(),
            }
        )
    artifacts = []
    for a in s.scalars(select(db.Artifact).where(db.Artifact.tenant_id == tenant, db.Artifact.run_id.in_(by_id)).order_by(db.Artifact.created_at.desc(), db.Artifact.id.desc()).limit(200)):
        artifacts.append(
            {
                "id": a.id,
                "runId": a.run_id,
                "name": a.name,
                "type": {"patch": "代码补丁", "test_report": "验证报告", "summary": "交付说明"}.get(a.kind, a.kind),
                "size": f"{a.ref['bytes']} bytes",
                "verified": a.verified,
                "content": "",
                "download_url": f"/v1/artifacts/{a.id}/download",
                "version": a.version,
                "digest": a.ref["digest"],
            }
        )
    event_page = catalog_page(s, actor, "events", limit=100, run_id=run_id) if run_id else None
    events = event_page["items"] if event_page else [event_view(e) for e in s.scalars(select(db.Event).where(
        db.Event.tenant_id == tenant, db.Event.run_id.in_(by_id)).order_by(db.Event.created_at.desc(), db.Event.id.desc()).limit(100))]
    projects = catalog_page(s, actor, "projects", limit=100)["items"]
    memory_query = select(db.Memory).where(db.Memory.tenant_id == tenant, db.Memory.status == "active")
    if not actor.admin and "read" not in grants.get("*", []):
        memory_query = memory_query.where(db.Memory.data["project"].as_string().in_([key for key, ops in grants.items() if "read" in ops]))
    config = db.get(s, db.PolicyVersion, tenant, "settings")
    return {
        "schema": 2,
        "runs": [run_view(s, r, cache) for r in runs],
        "next_cursor": next_cursor,
        "event_next_cursor": event_page["next_cursor"] if event_page else None,
        "window": {"limit": limit, "events": 200, "artifacts": 200, "approvals": 200},
        "approvals": approvals,
        "artifacts": artifacts,
        "skills": catalog_page(s, actor, "skills", limit=100)["items"],
        "memories": [{"id": m.id, **m.data} for m in s.scalars(memory_query.order_by(db.Memory.created_at.desc(), db.Memory.id.desc()).limit(100))],
        "events": sorted(events, key=lambda e: e["time"], reverse=True),
        "settings": {k: v for k, v in config.data.items() if k != "_revision"},
        "settings_revision": config.data.get("_revision", 1),
        "evalCompleted": bool(s.scalar(select(db.Evaluation.id).where(db.Evaluation.tenant_id == tenant, db.Evaluation.status == "completed").limit(1))),
        "projects": [
            {"id": p["id"], "name": p.get("name", p["id"])} for p in projects
        ],
    }
