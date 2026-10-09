"""Permission-scoped totals independent of the visible page; root costs counted once."""

from sqlalchemy import func, select

from . import db
from .pagination import attention, runs, visible
from .presenters import run_cache, run_view


def summary(s, actor, project=None):
    scope = [visible(s, actor)]
    if project:
        scope.append(db.Run.project_id == project)
    totals = s.execute(select(func.count(), func.count().filter(attention()),
        func.count().filter(db.Run.status.in_(["ACTIVE", "QUEUED", "WAITING", "CANCELLING"]) & ~attention()),
        func.count().filter(db.Run.status == "SUCCEEDED")).where(*scope)).one()
    roots = select(db.Run.root_id).where(*scope).distinct()
    spent, reserved = s.execute(select(func.coalesce(func.sum(db.BudgetAccount.spent), 0),
        func.coalesce(func.sum(db.BudgetAccount.reserved), 0)).where(
            db.BudgetAccount.tenant_id == actor.tenant, db.BudgetAccount.id.in_(roots))).one()
    previews = {}
    for key, status, limit in [("attention_runs", "attention", 3), ("active_runs", "active", 4), ("completed_runs", "done", 2)]:
        rows, _ = runs(s, actor, project=project, status=status, limit=limit)
        if key == "active_runs":
            rows = list(s.scalars(select(db.Run).where(*scope, ~attention(),
                db.Run.status.in_(["ACTIVE", "QUEUED", "WAITING", "CANCELLING"])).order_by(
                    db.Run.created_at.desc(), db.Run.id.desc()).limit(limit)))
        cache = run_cache(s, rows)
        previews[key] = [run_view(s, row, cache) for row in rows]
    return {"total": totals[0], "attention": totals[1], "active": totals[2], "succeeded": totals[3],
            "cost_usd": float(spent) / 1e6, "reserved_usd": float(reserved) / 1e6,
            "cost_scope": "distinct visible root accounts, including their child spending", **previews}
