"""Durable erasure of memory-derived run families. Financial audit retains only non-text facts."""

import shutil

from . import db, service
from .config import settings
from .domain import Fault, digest
from .storage import objects


def prepare(s, tenant, memory_id, actor, run_id=None):
    from .knowledge import lock

    lock(s, tenant)
    if memory_id:
        db.get(s, db.Memory, tenant, memory_id, True)
    initial = db.get(s, db.Run, tenant, run_id, True) if run_id else None
    key = "run-erasure-" + run_id if run_id else "memory-erasure-" + memory_id
    existing = s.get(db.PolicyVersion, (tenant, key))
    if existing:
        return existing.data["runs"], key
    roots, memory_ids, derived_ids = ({initial.root_id} if initial else set()), ({memory_id} if memory_id else set()), set()
    all_runs, all_memories = db.rows(s, db.Run, tenant), db.rows(s, db.Memory, tenant)
    all_skills = db.rows(s, db.SkillVersion, tenant)
    skill_ids = set()
    previous = None
    while previous != (len(roots), len(memory_ids), len(skill_ids)):
        previous = len(roots), len(memory_ids), len(skill_ids)
        for run in all_runs:
            lineage = set(run.state.get("memory_lineage", [])) | {m["id"] for m in run.state.get("memories", [])}
            if lineage & memory_ids or {item["id"] for item in run.state.get("skills", [])} & skill_ids:
                roots.add(run.root_id)
        derived_ids = {r.id for r in all_runs if r.root_id in roots}
        for linked in all_memories:
            if linked.data.get("supersedes_id") in memory_ids or set(linked.data.get("source_refs", [])) & derived_ids:
                memory_ids.add(linked.id)
        for skill in all_skills:
            if set(skill.data.get("source_runs", [])) & derived_ids:
                skill_ids.add(skill.id)
    runs = [db.get(s, db.Run, tenant, r.id, True) for r in db.rows(s, db.Run, tenant) if r.root_id in roots]
    for run in runs:
        service.require_settled_usage(s, run)
        if run.lease_until and run.lease_until > db.clock(s):
            raise Fault("ERASURE_BUSY", "Pause derived tasks and wait for the active worker lease to end")
        if any(a.status in {"RUNNING", "DISPATCHED", "UNKNOWN"} for a in db.rows(s, db.Action, tenant, run_id=run.id)):
            raise Fault("ERASURE_UNSETTLED", "Reconcile in-flight external effects before erasing their evidence")
    ids = [r.id for r in runs]
    s.add(db.PolicyVersion(tenant_id=tenant, id=key, status="pending", data={"runs": ids, "reviewer": actor, "memory_id": memory_id}))
    s.flush()
    s.execute(db.text("SELECT set_config('forge.erasure_request', :key, true)"), {"key": key})
    for run in runs:
        old_digest = digest(run.state)
        old_seq = run.seq
        run.status, run.phase, run.wait_reason = "CANCELLED", "ERASED", None
        run.cancel_requested, run.pause_requested = True, True
        run.epoch += 1
        run.lease_owner = run.lease_until = None
        st = run.state
        run.state = {"knowledge_erased": True, "title": "Erased derived task", "task": {"goal": "[erased]", "allowed_paths": []},
                     "budget": st["budget"], "semantic": {"model_id": st["semantic"]["model_id"], "tools_digest": "erased"},
                     "skills": [], "memories": [], "capabilities": [], "tokens": st["tokens"], "turn": st["turn"],
                     "artifact_version": st["artifact_version"], "reason": "KNOWLEDGE_ERASED", "fixture": st["fixture"],
                     "acceptance": {}, "verification": None}
        db.get(s, db.TaskSpec, tenant, run.task_id).data = {"erased": True}
        s.flush()
        for cls in [db.Event, db.Checkpoint, db.ContextManifest, db.SemanticManifest, db.Turn, db.VerificationResult,
                    db.WorkspaceSnapshot, db.SandboxRecord, db.Artifact, db.Attempt, db.EvaluationResult]:
            for row in db.rows(s, cls, tenant, run_id=run.id):
                s.delete(row)
        for action in db.rows(s, db.Action, tenant, run_id=run.id):
            action.args, action.receipt = {}, None
            if action.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                action.status = "CANCELLED"
            for approval in db.rows(s, db.Approval, tenant, action_id=action.id):
                approval.reason = "[erased]"
            for outbox in db.rows(s, db.Outbox, tenant):
                if outbox.data.get("action_id") == action.id:
                    outbox.data, outbox.status = {"action_id": action.id, "erased": True}, "cancelled"
            for inbox in db.rows(s, db.Inbox, tenant):
                if inbox.data.get("payload", {}).get("action_id") == action.id:
                    inbox.data, inbox.status = {"erased": True, "payload": {"action_id": action.id}}, "erased"
        for call in db.rows(s, db.ModelCall, tenant, run_id=run.id):
            call.data = {"erased": True, "usage": call.data.get("usage"), "request_digest": call.data.get("request_digest")}
        job = s.get(db.Job, (tenant, run.id))
        if job:
            job.status, job.data = "DONE", {"erased": True}
        s.flush()
        run.seq = 0
        s.info.get("event_projections", {}).pop((tenant, run.id), None)
        db.emit(s, run, "KNOWLEDGE_ERASED", "Memory-derived family erased", prior_state_digest=old_digest, prior_seq=old_seq)
    for evaluation in db.rows(s, db.Evaluation, tenant):
        if set(evaluation.data.get("runs", [])) & set(ids):
            evaluation.status, evaluation.data = "erased", {"erased": True, "runs": [], "lineage_digest": digest(evaluation.data)}
    for skill in db.rows(s, db.SkillVersion, tenant):
        if skill.id in skill_ids:
            skill.status, skill.data = "erased", {"name": skill.data.get("name"), "digest": skill.data.get("digest"), "erased": True}
    for linked in db.rows(s, db.Memory, tenant):
        if linked.id in memory_ids:
            linked.status, linked.data = "erased", {"erased": True, "digest": digest(linked.data)}
    from .maintenance_jobs import enqueue

    enqueue(s, tenant, "erasure", {"runs": ids, "erasure_key": key}, key)
    return ids, key


def finish(tenant, ids, key):
    from .knowledge import lock

    with db.transaction(tenant) as s:
        lock(s, tenant)
        job = db.get(s, db.PolicyVersion, tenant, key, True)
        if job.status not in {"pending", "completed"} or job.data.get("runs") != ids:
            raise Fault("ERASURE_SCOPE", "Physical erasure requires the exact registered task family", 403)
        for run_id in ids:
            if not db.get(s, db.Run, tenant, run_id).state.get("knowledge_erased"):
                raise Fault("ERASURE_SCOPE", "Task has not committed its registered erasure", 403)
        purge_family(tenant, ids)
        job.status = "completed"
    return {"status": "erased", "derived_runs": ids, "limitations": "Externally retained provider data and backups require their own retention/erasure process."}


def purge_family(tenant, ids):
    from .sandbox import sandbox

    for run_id in ids:
        objects.purge_scope(tenant, run_id)
        scope = digest(tenant)[7:23]
        for base, requested in [(settings.data_dir.resolve() / "workspaces", sandbox.root(tenant, run_id, 0).parent),
                                (settings.data_dir.resolve() / "verification", settings.data_dir.resolve() / "verification" / scope / run_id)]:
            path = requested.resolve()
            if path != requested.absolute() or not path.is_relative_to(base) or path == base:
                raise Fault("ERASURE_SCOPE", "Deletion escaped derived data root")
            if path.exists():
                shutil.rmtree(path)
