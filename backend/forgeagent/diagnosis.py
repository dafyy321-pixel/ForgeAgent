"""Evidence custody and bounded, explicitly uncertain failure diagnosis."""

from . import db
from .domain import digest, uid

VERSION = "evidence-diagnosis@1"


def enabled(state):
    return state.get("semantic", {}).get("harness", {}).get("evidence_recovery", False)


def binding(run):
    state = run.state
    return {"task_id": run.task_id, "input_revision": state.get("input_revision", 0),
            "workspace_digest": state["workspace_digest"], "artifact_version": state["artifact_version"],
            "acceptance_digest": digest(state["acceptance"]), "semantic_digest": digest(state["semantic"])}


def evidence(s, run, source, ref, facts, source_id=None):
    row = db.EvidenceRecord(tenant_id=run.tenant_id, run_id=run.id, id=uid(), data={
        "version": 1, "source": source, "source_id": source_id, "ref": ref, "binding": binding(run),
        "observed_at": db.clock(s).isoformat(), "facts": facts, "trust": "untrusted_tool_output"})
    s.add(row)
    s.flush()
    db.emit(s, run, "EVIDENCE_RECORDED", "Evidence retained with its task and workspace binding", evidence_id=row.id)
    return row


def queue(s, run, source, ref, facts, source_id=None):
    if not enabled(run.state) or run.cancel_requested or run.pause_requested:
        return
    row = evidence(s, run, source, ref, facts, source_id)
    # Stable failure identity ignores timestamps, object locations and observation envelopes.
    signature = digest([source, facts, run.state["workspace_digest"], run.state.get("input_revision", 0),
                        run.state.get("progress", {}).get("evidence", [])])
    run.state = {**run.state, "diagnosis_pending": {"evidence_id": row.id, "signature": signature},
                 "recovery_completion": run.state.get("completion"), "completion": None}
    run.status, run.phase = "ACTIVE", "DIAGNOSING"
    db.emit(s, run, "DIAGNOSIS_REQUESTED", "Classify failure before choosing another action", evidence_id=row.id)
    db.schedule(s, run)


def classify(source, facts):
    code = facts.get("error_code")
    if facts.get("failure_class") == "hard_constraint":
        return "constraint", "supported", "Protected constraints failed", "stop"
    if code in {"SANDBOX_BACKPRESSURE", "SANDBOX_UNAVAILABLE", "SANDBOX_MANAGER_REQUIRED", "REA_BACKPRESSURE"}:
        return "environment", "supported", "Execution infrastructure did not admit the operation", "retry_observation"
    if facts.get("inconclusive_reason") in {"timed_out", "truncated", "unregistered_exit", "not_run"}:
        return "unknown", "unknown", "Acceptance could not establish an outcome; root cause is unknown", "stop"
    if code in {"TOOL_TIMEOUT", "SESSION_EXPIRED", "REA_TIMEOUT"}:
        return "tool", "supported", "The observer session failed; target behavior remains unknown", "retry_observation"
    if source == "verification" and facts.get("verdict") == "FAIL":
        return "unknown", "hypothesis", "Acceptance failed; implementation, target understanding and specification remain candidates", "inspect_then_repair"
    if facts.get("tool") == "tests.run" and facts.get("exit_code") == 1 and not code:
        return "unknown", "hypothesis", "Public test command failed; inspect evidence before editing", "inspect_then_repair"
    return "unknown", "unknown", "No supported recovery is established by the available evidence", "stop"
