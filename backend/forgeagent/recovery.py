"""Deterministic recovery policy; no model can grant authority or change acceptance."""

from typing import cast

from . import db, diagnosis, service
from .domain import Fault, ToolCall, uid
from .state_types import RunState

VERSION = "bounded-recovery@1"
PROBES = {"repo.read", "repo.search", "repo.symbols", "observation.read", "rea.analyze", "rea.view", "rea.trace"}


def invalidate(s, run, status="SUPERSEDED"):
    if run.state.get("active_recovery"):
        db.get(s, db.RecoveryAttempt, run.tenant_id, run.state["active_recovery"]).status = status
    run.state = {**run.state, "diagnosis_pending": None, "active_recovery": None,
                 "recovery_guard": None, "recovery_completion": None, "diagnosis_summary": None,
                 "verification_feedback": None}


def advance(s, run):
    pending = run.state.get("diagnosis_pending")
    if not pending:
        return
    service.authorization(s, run)
    service.require_settled_usage(s, run)
    source = db.get(s, db.EvidenceRecord, run.tenant_id, pending["evidence_id"])
    if source.run_id != run.id:
        raise Fault("EVIDENCE_SCOPE", "Evidence must belong to this task")
    prior = run.state.get("active_recovery")
    if prior:
        db.get(s, db.RecoveryAttempt, run.tenant_id, prior).status = "FAILED"
    root, status, reason, strategy = diagnosis.classify(source.data["source"], source.data["facts"])
    counts = run.state.get("diagnostic_signatures", {})
    repeated = counts.get(pending["signature"], 0)
    used = run.state.get("diagnoses_used", 0)
    budget = run.state["budget"]
    db.get(s, db.BudgetAccount, run.tenant_id, run.root_id, True)
    root_run = db.get(s, db.Run, run.tenant_id, run.root_id)
    family = db.rows(s, db.Run, run.tenant_id, root_id=run.root_id)
    root_budget = root_run.state["budget"]
    root_diagnoses = sum(member.state.get("diagnoses_used", 0) for member in family)
    root_repairs = sum(member.state.get("repair_attempts", 0) for member in family)
    root_recoveries = sum(member.state.get("recoveries_used", 0) for member in family)
    if source.data["binding"] != diagnosis.binding(run):
        strategy, reason = "stop", "Evidence binding is stale; collect evidence for the current task revision"
    elif used >= budget.get("max_diagnoses", 6) or root_diagnoses >= root_budget.get("max_diagnoses", 6):
        strategy, reason = "stop", "Diagnosis budget exhausted"
    elif repeated:
        strategy, reason = "stop", "Same failure recurred without changed evidence or workspace"
    elif run.cancel_requested or run.pause_requested:
        strategy, reason = "stop", "User control supersedes recovery"
    elif strategy == "inspect_then_repair" and (run.state.get("repair_attempts", 0) >= budget.get("max_repair_attempts", 2)
                                               or root_repairs >= root_budget.get("max_repair_attempts", 2)):
        strategy, reason = "stop", "Code repair budget exhausted"
    elif strategy == "retry_observation" and (run.state.get("recoveries_used", 0) >= budget.get("max_recovery_attempts", 2)
                                              or root_recoveries >= root_budget.get("max_recovery_attempts", 2)):
        strategy, reason = "stop", "Tool/environment recovery budget exhausted"
    record = db.Diagnosis(tenant_id=run.tenant_id, run_id=run.id, id=uid(), data={
        "version": diagnosis.VERSION, "policy_version": VERSION, "evidence_ids": [source.id],
        "binding": source.data["binding"], "root_class": root, "conclusion": status, "reason": reason,
        "strategy": strategy, "signature": pending["signature"],
        "hypotheses": ["implementation", "target_understanding", "specification"] if status == "hypothesis" else [],
        "unknowns": ["Root cause has not been verified"] if root == "unknown" else [],
        "contradicting_evidence_ids": []})
    s.add(record)
    s.flush()
    run.state = {**run.state, "diagnosis_pending": None, "diagnosis_id": record.id,
                 "diagnoses_used": used + 1, "diagnostic_signatures": {**counts, pending["signature"]: repeated + 1},
                 "diagnosis_summary": {"root_class": root, "conclusion": status, "reason": reason, "strategy": strategy},
                 "active_recovery": None, "recovery_guard": None}
    db.emit(s, run, "DIAGNOSIS_RECORDED", reason, diagnosis_id=record.id)
    # Withdraw unexecuted fused proposals; do not run writes after an environment/tool failure.
    for action in db.rows(s, db.Action, run.tenant_id, run_id=run.id):
        if action.status == "READY":
            action.status = "CANCELLED"
    if strategy == "stop":
        run.status, run.wait_reason = "PAUSED", None
        run.state = {**run.state, "reason": reason, "input_required": root == "unknown"}
        db.emit(s, run, "RECOVERY_STOPPED", reason)
        return
    intent = db.RecoveryAttempt(tenant_id=run.tenant_id, run_id=run.id, id=uid(), status="SELECTED", data={
        "diagnosis_id": record.id, "strategy": strategy, "binding": diagnosis.binding(run), "policy_version": VERSION})
    s.add(intent)
    s.flush()
    run.state = {**run.state, "active_recovery": intent.id}
    run.status, run.phase = "ACTIVE", "RECOVERING"
    if strategy == "inspect_then_repair":
        run.state = {**run.state, "repair_attempts": cast(RunState, run.state).get("repair_attempts", 0) + 1,
                     "recovery_guard": {"recovery_id": intent.id, "requires_probe": True},
                     "verification_feedback": {"diagnosis_id": record.id, "evidence_id": source.id,
                         "verdict": source.data["facts"].get("verdict", "FAIL"),
                         "instruction": "Inspect public failure evidence or relevant source before editing. Root cause remains a hypothesis."}}
    else:
        run.state = {**run.state, "recoveries_used": cast(RunState, run.state).get("recoveries_used", 0) + 1}
        if source.data["source"] == "verification":
            run.state = {**run.state, "completion": run.state.get("recovery_completion") or "Reverify the unchanged artifact"}
        else:
            action = db.get(s, db.Action, run.tenant_id, source.data["source_id"])
            if action.run_id != run.id or action.effect_class != "read" or action.tool not in PROBES | {"tests.run"}:
                raise Fault("RECOVERY_EFFECT_DENIED", "Only reviewed local observations may be retried")
            service.prepare_action(s, run, ToolCall(tool=action.tool, args=action.args), "recovery:" + intent.id)
    db.emit(s, run, "RECOVERY_SELECTED", strategy, recovery_id=intent.id, diagnosis_id=record.id)
    db.schedule(s, run)


def observed(s, run, action, receipt):
    guard = run.state.get("recovery_guard")
    if guard and action.status == "SUCCEEDED" and action.tool in PROBES and not receipt.get("truncated"):
        run.state = {**run.state, "recovery_guard": {**guard, "requires_probe": False}}
        db.emit(s, run, "RECOVERY_PROBE_COMPLETED", "Public evidence inspected before code repair", action_id=action.id)
    active = cast(RunState, run.state).get("active_recovery")
    if active and action.logical_key == "recovery:" + active and action.status == "SUCCEEDED":
        db.get(s, db.RecoveryAttempt, run.tenant_id, active).status = "APPLIED"
        run.state = {**run.state, "active_recovery": None}
        db.emit(s, run, "RECOVERY_POSTCONDITION", "Observation completed; final acceptance remains independent", recovery_id=active)
