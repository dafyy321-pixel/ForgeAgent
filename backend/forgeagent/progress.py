"""Durable evidence progress; changing write arguments alone cannot reset the stall budget."""

from . import db
from .domain import digest


def failure_class(code):
    if code in {"UNKNOWN_USAGE", "UNSETTLED_BUDGET", "UNRESOLVED_EFFECT", "MODEL_CALL_FAILED"}:
        return "reconciliation"
    if code in {"SANDBOX_UNAVAILABLE", "MODEL_NOT_CONFIGURED", "MODEL_BINDING_CHANGED", "SEMANTIC_DRIFT"}:
        return "environment"
    if code in {"MODEL_RATE_LIMIT", "SANDBOX_BACKPRESSURE"}:
        return "backpressure"
    if "LIMIT" in code or code in {"CONTEXT_OVERFLOW", "NO_PROGRESS"}:
        return "resource"
    if code.endswith(("DENIED", "REVOKED")) or code.startswith("APPROVAL"):
        return "authorization"
    if code in {"PRECONDITION_FAILED", "MERGE_CONFLICT", "VERIFICATION_STALE"}:
        return "conflict"
    if code in {"MODEL_FORMAT_ERROR", "TOOL_UNAVAILABLE", "INVALID_COMMAND"}:
        return "contract"
    return "execution"


def record(s, run, kind, **facts):
    progress = run.state.get("progress", {})
    journal = [*progress.get("journal", []), {"at": db.clock(s).isoformat(), "turn": run.state["turn"],
                                            "kind": kind, **facts}][-64:]
    run.state = {**run.state, "progress": {**progress, "journal": journal}}
    db.emit(s, run, "PROGRESS_RECORDED", kind, **facts)


def failed(s, run, code, source):
    category = failure_class(code)
    failures = run.state.get("failure_counts", {})
    run.state = {**run.state, "failure_counts": {**failures, category: failures.get(category, 0) + 1},
                 "last_failure": {"code": code, "class": category, "source": source}}
    record(s, run, "failure", code=code, failure_class=category, source=source, error_signature=digest([code, source]))


def observed(s, run, tool, receipt, status):
    if status in {"RUNNING", "CANCELLED"}:
        record(s, run, "pending" if status == "RUNNING" else "cancelled", tool=tool)
        return
    if status != "SUCCEEDED":
        failed(s, run, "UNRESOLVED_EFFECT" if status == "UNKNOWN" else receipt.get("error_code", "TOOL_FAILED"), tool)
        return
    progress = run.state.get("progress", {})
    evidence = None
    if tool in {"repo.read", "observation.read"}:
        evidence = digest([tool, receipt.get("source_digest") or receipt.get("digest")])
    elif tool == "tests.run":
        evidence = digest([tool, run.state["workspace_digest"], receipt.get("exit_code")])
    elif tool == "child.integrate":
        evidence = digest([tool, receipt["child_id"]])
    seen = progress.get("evidence", [])
    novel = evidence is not None and evidence not in seen
    if novel:
        run.state = {**run.state, "progress": {**progress, "stalled_turns": 0,
                                               "last_evidence_turn": run.state["turn"],
                                               "evidence": [*seen, evidence][-256:]}, "fingerprint": None, "repeats": 0}
    record(s, run, "observation", tool=tool, novel_evidence=novel,
           workspace_digest=run.state["workspace_digest"])


def decided(s, run):
    progress = run.state.get("progress", {})
    revision = run.state.get("input_revision", 0)
    stalled = 1 if progress.get("input_revision", 0) != revision else progress.get("stalled_turns", 0) + 1
    run.state = {**run.state, "progress": {**progress, "stalled_turns": stalled, "input_revision": revision}}
    record(s, run, "decision", stalled_turns=stalled)
    if stalled >= run.state["budget"].get("max_no_progress_turns", 12):
        run.status, run.wait_reason = "PAUSED", None
        run.state = {**run.state, "reason": "No new successful evidence within the configured decision budget"}
        failed(s, run, "NO_PROGRESS", "runtime")
        db.emit(s, run, "NO_PROGRESS", run.state["reason"])
        return False
    return True
