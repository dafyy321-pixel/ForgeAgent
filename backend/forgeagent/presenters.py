from . import db, service
from .domain import canonical, digest
from .storage import objects


def run_view(s, r):
    st = r.state
    account = db.get(s, db.BudgetAccount, r.tenant_id, r.root_id)
    actions = db.rows(s, db.Action, r.tenant_id, run_id=r.id)
    report = st.get("verification")
    elapsed = max(0, int((db.clock(s) - r.created_at).total_seconds()))
    semantic = st["semantic"]
    return {
        "id": r.id,
        "title": st["title"],
        "description": st["task"]["goal"],
        "project": r.project_id,
        "status": r.status,
        "phase": st.get("reason") if r.status == "PAUSED" else r.phase,
        "progress": 100 if r.status == "SUCCEEDED" else min(90, st["turn"] * 10),
        "model": semantic["model_id"] or "尚未配置",
        "cost": account.spent / 1_000_000,
        "reserved": account.reserved / 1_000_000,
        "budget": account.limit_micros / 1_000_000,
        "tokens": st["tokens"],
        "steps": len(actions),
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
                ("修改与执行", any(a.status == "SUCCEEDED" for a in actions)),
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
            <= set(db.get(s, db.Authorization, r.tenant_id, r.actor).data["capabilities"]),
            "environment": st.get("environment_ready", not st.get("reason", "").startswith("SANDBOX_UNAVAILABLE")),
        },
        "unknownEffect": any(a.status == "UNKNOWN" for a in actions),
        "cancelRequested": r.cancel_requested,
        "version": st["artifact_version"],
        "stateVersion": r.version,
        "deadline": None,
        "verification": {
            "status": "passed"
            if report and report["verdict"] == "PASS"
            else "failed"
            if report and report["verdict"] == "FAIL"
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


def action_view(s, a):
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
        "input_deliveries": [
            {"id": o.id, "status": o.status}
            for o in db.rows(s, db.Outbox, a.tenant_id)
            if o.data.get("action_id") == a.id
        ],
        "attempts": [
            {"id": p.id, "status": p.status, **p.data}
            for p in db.rows(s, db.Attempt, a.tenant_id, run_id=a.run_id)
            if p.data["action_id"] == a.id
        ],
    }


def workspace(s, tenant):
    runs = db.rows(s, db.Run, tenant)
    by_id = {r.id: r for r in runs}
    approvals = []
    for p in db.rows(s, db.Approval, tenant):
        a = db.get(s, db.Action, tenant, p.action_id)
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
    for a in db.rows(s, db.Artifact, tenant):
        content = objects.get(tenant, a.ref).decode("utf-8", errors="replace")
        artifacts.append(
            {
                "id": a.id,
                "runId": a.run_id,
                "name": a.name,
                "type": {"patch": "代码补丁", "test_report": "验证报告", "summary": "交付说明"}.get(a.kind, a.kind),
                "size": f"{a.ref['bytes']} bytes",
                "verified": a.verified,
                "content": content,
                "version": a.version,
                "digest": a.ref["digest"],
            }
        )
    events = []
    for e in db.rows(s, db.Event, tenant):
        kind = (
            "approval"
            if "APPROVAL" in e.type
            else "verification"
            if "VERIFICATION" in e.type
            else "cancel"
            if "CANCEL" in e.type
            else "recovery"
            if "LEASE" in e.type or "PAUSED" in e.type
            else "tool"
            if "ACTION" in e.type
            else "plan"
            if "DECISION" in e.type
            else "task"
        )
        events.append(
            {
                "id": e.id,
                "runId": e.run_id,
                "kind": kind,
                "title": e.payload["message"],
                "detail": canonical({k: v for k, v in e.payload.items() if k != "projection"}).decode(),
                "time": int(e.created_at.timestamp() * 1000),
            }
        )
    config = db.get(s, db.PolicyVersion, tenant, "settings")
    return {
        "schema": 2,
        "runs": [run_view(s, r) for r in reversed(runs)],
        "approvals": approvals,
        "artifacts": artifacts,
        "skills": [
            {"id": x.id, "enabled": x.status == "active", "calls": 0, **x.data}
            for x in db.rows(s, db.SkillVersion, tenant)
        ],
        "memories": [{"id": m.id, **m.data} for m in db.rows(s, db.Memory, tenant) if m.status == "active"],
        "events": sorted(events, key=lambda e: e["time"], reverse=True),
        "settings": config.data,
        "evalCompleted": any(e.status == "completed" for e in db.rows(s, db.Evaluation, tenant)),
        "projects": [
            {"id": p.id, "name": p.data.get("name", p.id), "acceptance": p.data.get("acceptance", {}).get("id")}
            for p in db.rows(s, db.Project, tenant)
        ],
    }
