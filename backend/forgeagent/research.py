"""Auditable measured quantities and explicitly incomplete full-cost estimates."""

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field
from sqlalchemy import func, select

from . import db
from .domain import Strict, digest


class CostAssumptions(Strict):
    cpu_usd_per_second: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    storage_usd_per_gib_hour: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    tool_usd_per_call: dict[str, Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    environment_usd_per_run: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    human_usd_per_hour: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    human_seconds_by_run: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)


def snapshot(s, run, assumptions=None):
    rates = CostAssumptions.model_validate(assumptions or {})
    account = db.get(s, db.BudgetAccount, run.tenant_id, run.root_id)
    family = db.rows(s, db.Run, run.tenant_id, root_id=run.root_id)
    ids = {member.id for member in family}
    actions = s.execute(select(db.Action.tool, db.Action.attempt).where(db.Action.tenant_id == run.tenant_id, db.Action.run_id.in_(ids))).all()
    events = s.execute(select(db.Event.type, db.Event.run_id, db.Event.created_at,
        db.Event.payload["recovered"].as_boolean().label("recovered"),
        db.Event.payload["recovery_version"].as_integer().label("recovery_version"),
        db.Event.payload["expired_at"].as_string().label("expired_at")).where(
            db.Event.tenant_id == run.tenant_id, db.Event.run_id.in_(ids)).order_by(db.Event.created_at, db.Event.seq)).all()
    calls = s.scalar(select(func.count()).select_from(db.ModelCall).where(db.ModelCall.tenant_id == run.tenant_id, db.ModelCall.run_id.in_(ids)))
    entries = db.rows(s, db.BudgetEntry, run.tenant_id, account_id=run.root_id)
    usage = account.resources or {}
    unknown = any(entry.status != "settled" for entry in entries) or bool(account.reserved or usage.get("tokens_reserved", 0))
    seconds = max(0, (run.updated_at - run.created_at).total_seconds())
    missing = []
    components = {"model_usd": account.spent / 1e6}
    if unknown:
        missing.append("unsettled_model_usage")
    def price(name, quantity, tariff):
        if quantity == 0:
            components[name] = 0.0
        elif tariff is None:
            components[name] = None
            missing.append(name)
        else:
            components[name] = float(Decimal(str(quantity)) * tariff)
    price("cpu_usd", usage.get("cpu_seconds", 0), rates.cpu_usd_per_second)
    price("storage_usd", usage.get("storage_bytes", 0) / (1024 ** 3) * seconds / 3600, rates.storage_usd_per_gib_hour)
    price("environment_usd", 1, rates.environment_usd_per_run)
    tool_counts = Counter()
    for action in actions:
        tool_counts[action.tool] += action.attempt
    tool_counts = +tool_counts
    components["tools_usd"] = 0.0
    for tool, count in tool_counts.items():
        tariff = rates.tool_usd_per_call.get(tool)
        if tariff is None or not tariff.is_finite() or tariff < 0:
            missing.append("tool:" + tool)
            components["tools_usd"] = None
        elif components["tools_usd"] is not None:
            components["tools_usd"] += float(tariff * count)
    manual = sum(event.type in {"APPROVAL_DECIDED", "ACTION_RECONCILED", "MODEL_USAGE_RECONCILED"} for event in events)
    human_seconds = rates.human_seconds_by_run.get(run.id, None if manual else 0)
    if human_seconds is None or human_seconds < 0:
        components["human_usd"] = None
        missing.append("human_seconds")
    else:
        price("human_usd", human_seconds / 3600, rates.human_usd_per_hour)
    recovery, pickup, pending, legacy = [], [], {}, 0
    for event in events:
        if event.type == "LEASE_CLAIMED" and event.recovered:
            if event.recovery_version != 2:
                legacy += 1
                continue
            pending[event.run_id] = event.created_at
            if event.expired_at:
                pickup.append(max(0, (event.created_at - datetime.fromisoformat(event.expired_at)).total_seconds()))
        elif event.run_id in pending and event.type in {"ACTION_RESULT", "DECISION_APPLIED", "VERIFICATION_COMPLETED", "RUN_CANCELLED"}:
            recovery.append(max(0, (event.created_at - pending.pop(event.run_id)).total_seconds()))
    failure = (run.state.get("last_failure") or {}).get("category") or run.state.get("reason", "").split(":", 1)[0] or run.status
    return {"version": 1, "run_id": run.id, "assumptions_digest": digest(rates.model_dump(mode="json")),
        "components": components, "model_usage_settled": not unknown,
        "known_cost_usd": sum(value for value in components.values() if value is not None),
        "total_cost_usd": None if missing else sum(components.values()), "complete": not missing,
        "missing": sorted(set(missing)), "model_calls": calls, "tool_calls": dict(tool_counts),
        "cpu_seconds_upper_bound": usage.get("cpu_seconds", 0), "stored_bytes_charged": usage.get("storage_bytes", 0),
        "wall_seconds": seconds, "failure_category": failure, "recovery_to_progress_seconds": recovery,
        "recovery_pickup_seconds": pickup, "legacy_ambiguous_recoveries": legacy,
        "pending_recoveries": len(pending), "manual_decisions": manual,
        "limitations": ["Storage byte-hours use the charged byte peak over task wall time; CPU uses conservative sandbox charges.",
            "Infrastructure, external tool and human tariffs are declared assumptions, not provider invoices.",
            "Model ledger includes all family calls; unknown usage keeps the total incomplete."]}


def aggregate(rows):
    values = [row["research"] for row in rows]
    complete = all(value["complete"] for value in values)
    recoveries = [sample for value in values for sample in value["recovery_to_progress_seconds"]]
    successes = sum(row["status"] == "SUCCEEDED" for row in rows)
    total = sum(value["total_cost_usd"] for value in values) if complete else None
    return {"full_cost_complete": complete, "full_cost_usd": total,
        "known_cost_usd": sum(value["known_cost_usd"] for value in values),
        "full_cost_per_success_usd": total / successes if total is not None and successes else None,
        "failure_distribution": dict(Counter(value["failure_category"] for row, value in zip(rows, values, strict=True) if row["status"] != "SUCCEEDED")),
        "recovery_samples_seconds": recoveries, "pending_recoveries": sum(value["pending_recoveries"] for value in values),
        "recovery_pickup_samples_seconds": [sample for value in values for sample in value["recovery_pickup_seconds"]],
        "legacy_ambiguous_recoveries": sum(value["legacy_ambiguous_recoveries"] for value in values),
        "missing_cost_components": sorted({item for value in values for item in value["missing"]})}
