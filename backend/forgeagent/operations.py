"""Tenant-scoped operational diagnostics; never replay uncertain side effects."""

from . import db
from .domain import TERMINAL


def health(s, tenant):
    time = db.clock(s)
    runs = db.rows(s, db.Run, tenant)
    actions = db.rows(s, db.Action, tenant)
    approvals = db.rows(s, db.Approval, tenant)
    entries = db.rows(s, db.BudgetEntry, tenant)
    outbox = db.rows(s, db.Outbox, tenant)
    checks = [
        (
            "expired_leases",
            [r.id for r in runs if r.lease_until and r.lease_until < time and r.status not in TERMINAL],
            "Check worker and database health. The next claim fences the old epoch; do not redispatch remote writes.",
        ),
        (
            "unknown_effects",
            [a.run_id for a in actions if a.status == "UNKNOWN"],
            "Query the effect provider, record evidence in the action reconciliation form, then resume.",
        ),
        (
            "expired_approvals",
            [a.action_id for a in approvals if a.decision == "pending" and a.expires_at <= time],
            "Allow the worker to expire the action, then request a fresh review at the current task version.",
        ),
        (
            "unknown_billing",
            [e.data.get("run_id") for e in entries if e.status == "unknown"],
            "Reconcile provider billing with a receipt. Reserved funds remain unavailable until settlement.",
        ),
        (
            "outbox_attention",
            [o.id for o in outbox if o.status in {"unknown", "failed", "dispatching"}],
            "Inspect remote input delivery status. Never repeat an uncertain submission without provider evidence.",
        ),
        (
            "paused_runs",
            [r.id for r in runs if r.status == "PAUSED"],
            "Inspect the persisted reason and task recovery checks. Changed runtime versions require a fork.",
        ),
    ]
    return {
        "checked_at": time.isoformat(),
        "checks": [
            {"name": name, "count": len(ids), "resource_ids": sorted(set(ids)), "runbook": runbook}
            for name, ids, runbook in checks
        ],
    }
