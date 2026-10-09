"""Bounded tenant diagnostics: aggregate counts and at most 100 IDs per check."""

from sqlalchemy import func, select

from . import db
from .domain import TERMINAL


def health(s, tenant):
    time = db.clock(s)
    rules = [
        ("expired_leases", db.Run, db.Run.id, (db.Run.lease_until < time) & db.Run.status.not_in(TERMINAL),
         "Check worker and database health. The next claim fences the old epoch; do not redispatch remote writes."),
        ("unknown_effects", db.Action, db.Action.run_id, db.Action.status == "UNKNOWN",
         "Query the effect provider, record evidence in the action reconciliation form, then resume."),
        ("expired_approvals", db.Approval, db.Approval.action_id, (db.Approval.decision == "pending") & (db.Approval.expires_at <= time),
         "Allow the worker to expire the action, then request a fresh review at the current task version."),
        ("unknown_billing", db.BudgetEntry, db.BudgetEntry.data["run_id"].as_string(), db.BudgetEntry.status == "unknown",
         "Reconcile provider billing with a receipt. Reserved funds remain unavailable until settlement."),
        ("outbox_attention", db.Outbox, db.Outbox.id, db.Outbox.status.in_(["unknown", "failed", "dispatching"]),
         "Inspect remote input delivery status. Never repeat an uncertain submission without provider evidence."),
        ("paused_runs", db.Run, db.Run.id, db.Run.status == "PAUSED",
         "Inspect the persisted reason and task recovery checks. Changed runtime versions require a fork."),
        ("maintenance_dead_letters", db.PolicyVersion, db.PolicyVersion.id,
         (db.PolicyVersion.data["kind"].as_string() == "maintenance_job") & (db.PolicyVersion.status == "dead_letter"),
         "Inspect the persisted error code, repair the dependency, and explicitly retry the maintenance job."),
    ]
    checks = []
    for name, cls, identifier, condition, runbook in rules:
        where = [cls.tenant_id == tenant, condition]
        count = s.scalar(select(func.count()).select_from(cls).where(*where))
        ids = list(s.scalars(select(identifier).where(*where).distinct().order_by(identifier).limit(100)))
        checks.append({"name": name, "count": count, "resource_ids": ids, "ids_truncated": count > len(ids), "runbook": runbook})
    oldest = s.scalar(select(func.min(db.Action.created_at)).where(db.Action.tenant_id == tenant, db.Action.status == "UNKNOWN"))
    completed, succeeded = s.execute(select(func.count(), func.count().filter(db.Run.status == "SUCCEEDED")).where(
        db.Run.tenant_id == tenant, db.Run.status.in_(TERMINAL),
        func.coalesce(db.Run.state["knowledge_erased"].as_boolean(), False).is_(False))).one()
    return {"checked_at": time.isoformat(), "checks": checks,
            "slo": {"unknown_effect_max_age_seconds": max(0, (time - oldest).total_seconds()) if oldest else 0,
                    "unknown_effect_target_seconds": 900, "settled_success_rate": succeeded / completed if completed else None,
                    "sample_count": completed},
            "alerts": [{"code": check["name"].upper(),
                "severity": "critical" if check["name"] in {"unknown_effects", "unknown_billing"} else "warning",
                **{key: value for key, value in check.items() if key != "name"}}
                for check in checks if check["count"] and check["name"] != "paused_runs"]}
