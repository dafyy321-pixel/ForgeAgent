"""Deterministic phase summaries retain original evidence references and constraint bindings."""

from .domain import Fault, digest


def summarize(item):
    source = item["content"]
    if not isinstance(source, dict) or source.get("status") in {"FAILED", "UNKNOWN"}:
        return None
    ref = source.get("ref")
    if not ref or not ref.get("digest"):
        return None
    return {"type": "observation_summary", "trust": item["trust"], "content": {
        "action_id": source.get("action_id"), "tool": source.get("tool"), "status": source.get("status"),
        "exit_code": source.get("exit_code"), "paths": source.get("paths", []), "source_ref": ref,
        "source_digest": ref["digest"], "original_digest": digest(source),
        "limitations": "Body omitted; recall the source before deciding from its contents."}}


def phase_summary(task, state, selected):
    return {"version": 1, "method": "deterministic", "phase": state.get("phase", "decision"),
            "task_digest": digest(task), "workspace_digest": state.get("workspace_digest"),
            "input_revision": state.get("input_revision", 0), "last_failure": state.get("last_failure"),
            "unresolved": state.get("unresolved"), "sources": [digest(i) for i in selected], "auxiliary_cost_micros": 0}


def validate(summary, task, state, selected):
    expected = phase_summary(task, state, selected)
    if summary != expected:
        raise Fault("SUMMARY_INVALID", "Summary lost a constraint, unresolved fact, or evidence binding")
    return summary
