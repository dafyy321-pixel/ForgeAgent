import json
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from . import db
from .auth import PROJECT_OPERATIONS, Identity, project_access, require_run_access
from .config import settings
from .domain import CAPABILITIES, TERMINAL, TOOL_INPUTS, UNSETTLED, CreateRun, Fault, digest, uid
from .storage import objects

TOOLS = {
    "repo.list": {"effect": "read", "capability": "repo.read"},
    "repo.read": {"effect": "read", "capability": "repo.read"},
    "repo.write": {"effect": "workspace_write", "capability": "workspace.write"},
    "tests.run": {"effect": "read", "capability": "tests.run"},
    "observation.read": {"effect": "read", "capability": "repo.read"},
    "child.integrate": {"effect": "workspace_write", "capability": "workspace.write"},
}
VERIFIER = "forge-verifier@1"


def model_profile():
    return {
        "provider": settings.model_provider,
        "model_id": settings.model_id,
        "endpoint": settings.model_base_url,
        "input_price": settings.input_price,
        "output_price": settings.output_price,
        "context_window": settings.context_window,
        "max_output": settings.max_output,
    }


def memory_valid(memory, time):
    expiry = memory.data.get("expires_at")
    if not expiry:
        return True
    try:
        parsed = datetime.fromisoformat(expiry)
        return parsed.tzinfo is not None and parsed > time
    except ValueError:
        return False


@lru_cache(maxsize=1)
def implementation_bindings():
    root = Path(__file__).parent
    names = sorted(
        p.name
        for p in root.glob("*.py")
        if p.name not in {"__init__.py", "presenters.py", "cli.py", "sdk.py", "telemetry.py"}
    )
    return {name: digest((root / name).read_bytes()) for name in names}


# Bind loaded code once per process, so edits on disk cannot relabel an older live worker.
implementation_bindings()


def provision(tenant="local", actor="local-user"):
    with db.Session.begin() as s:
        if not s.get(db.Tenant, tenant):
            s.add(db.Tenant(id=tenant))
    with db.transaction(tenant) as s:
        if not s.get(db.Project, (tenant, "runtime-lab")):
            s.add(
                db.Project(
                    tenant_id=tenant,
                    id="runtime-lab",
                    data={
                        "name": "Runtime Lab",
                        "baseline": {"src/calculator.py": "def add(left, right):\n    return left - right\n"},
                        "acceptance": {"id": "addition@1", "kind": "fixture_ast", "argv": [], "protected_tests": {}},
                        "description": "Deterministic contract fixture; no external model or host commands",
                    },
                )
            )
        if not s.get(db.Authorization, (tenant, actor)):
            s.add(db.Authorization(tenant_id=tenant, id=actor, data={
                "epoch": 1, "capabilities": sorted(CAPABILITIES),
                "project_permissions": {"runtime-lab": sorted(PROJECT_OPERATIONS)},
            }))
        if not s.get(db.PolicyVersion, (tenant, "settings")):
            s.add(
                db.PolicyVersion(
                    tenant_id=tenant,
                    id="settings",
                    data={"budget": 5, "concurrency": 2, "notifications": True, "redact": True},
                )
            )
        if not s.get(db.AgentVersion, (tenant, "coding@1.0.0")):
            s.add(db.AgentVersion(tenant_id=tenant, id="coding@1.0.0", data={"tools": TOOLS, "verifier": VERIFIER}))
        for name, spec in TOOLS.items():
            if not s.get(db.ToolVersion, (tenant, name + "@1")):
                s.add(db.ToolVersion(tenant_id=tenant, id=name + "@1", data={"name": name, "version": "1", **spec}))


def authorization(s, run, capability=None):
    current = db.get(s, db.Authorization, run.tenant_id, run.actor)
    caps = set(current.data.get("capabilities", [])) & set(run.state["capabilities"])
    if capability and capability not in caps:
        raise Fault("PERMISSION_REVOKED", f"Current permission does not allow {capability}", 403)
    return current


def create_run(s, tenant, actor, spec: CreateRun, key, parent=None, evaluation_id=None, admin=False):
    if not key or len(key) > 200:
        raise Fault("IDEMPOTENCY_REQUIRED", "A bounded Idempotency-Key is required", 422)
    if not parent and not evaluation_id and not project_access(s, Identity(tenant, actor, admin), spec.project_id, "create"):
        raise Fault("FORBIDDEN", "Project create permission is required", 403)
    payload = spec.model_dump(mode="json")
    request_digest = digest(payload)
    key = digest({"actor": actor, "key": key})
    # Serialize equal keys before checking their unique index (also for concurrent clients).
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": tenant + ":" + key})
    existing = s.scalar(select(db.Run).where(db.Run.tenant_id == tenant, db.Run.request_key == key))
    if existing:
        if existing.request_digest != request_digest:
            raise Fault("IDEMPOTENCY_CONFLICT", "This key was used with different parameters")
        return existing
    project = db.get(s, db.Project, tenant, spec.project_id)
    db.get(s, db.AgentVersion, tenant, spec.agent_version)
    auth = db.get(s, db.Authorization, tenant, actor)
    if not set(spec.capabilities) <= set(auth.data["capabilities"]):
        raise Fault("CAPABILITY_DENIED", "Task capabilities exceed current permissions", 403)
    if spec.model == "fixture" and (
        spec.project_id != "runtime-lab" or project.data["acceptance"]["kind"] != "fixture_ast"
    ):
        raise Fault("FIXTURE_SCOPE", "Fixture model is restricted to Runtime Lab", 422)
    if parent:
        if parent.parent_id or "delegate" not in parent.state["capabilities"]:
            raise Fault("DELEGATION_LIMIT", "Delegation depth or capability exceeded")
        if spec.project_id != parent.project_id or not set(spec.capabilities) <= set(parent.state["capabilities"]):
            raise Fault("CHILD_SCOPE", "Child capabilities and project must remain inside the parent scope", 403)
        for path in spec.task.allowed_paths:
            if not any(
                path == p or path.startswith(p.rstrip("/") + "/") for p in parent.state["task"]["allowed_paths"]
            ):
                raise Fault("CHILD_SCOPE", "Child file scope exceeds parent", 403)
        if len([r for r in db.rows(s, db.Run, tenant, parent_id=parent.id) if r.status not in TERMINAL]) >= 2:
            raise Fault("CHILD_LIMIT", "At most two child tasks may be outstanding", 429)
    run_id = uid()
    baseline = (
        json.loads(objects.get(tenant, parent.state["workspace_ref"])) if parent else project.data.get("baseline", {})
    )
    baseline_ref = objects.put(tenant, run_id, baseline)
    skill_data = []
    for skill_id in spec.skills:
        skill = db.get(s, db.SkillVersion, tenant, skill_id)
        if skill.status != "active" and not (evaluation_id and skill.status == "candidate"):
            raise Fault("SKILL_NOT_ACTIVE", "Only released skills may be selected")
        skill_data.append({"id": skill.id, **skill.data})
    memories = [
        {"id": m.id, **m.data}
        for m in db.rows(s, db.Memory, tenant)
        if m.status == "active" and m.data.get("project") in (None, spec.project_id) and memory_valid(m, db.clock(s))
    ]
    model = "fixture@1" if spec.model == "fixture" else settings.model_id
    acceptance = seal_acceptance(tenant, run_id, project.data.get("acceptance", {}))
    if spec.task.acceptance_profile and spec.task.acceptance_profile != acceptance.get("id"):
        raise Fault("ACCEPTANCE_PROFILE", "Acceptance profile does not match the registered project", 422)
    semantic = {
        "agent": spec.agent_version,
        "model_id": model,
        "model_provider": settings.model_provider,
        "model_profile": model_profile(),
        "implementation": implementation_bindings(),
        "tools_digest": digest(TOOLS),
        "skills_digest": digest(skill_data),
        "verifier": VERIFIER,
        "baseline_digest": baseline_ref["digest"],
        "acceptance_digest": digest(acceptance),
        "context_policy": "constraints-first-elision@1",
        "harness": spec.harness.model_dump(),
        "schema_version": 1,
    }
    task = db.TaskSpec(tenant_id=tenant, data=payload)
    s.add(task)
    s.flush()
    r = db.Run(
        tenant_id=tenant,
        id=run_id,
        project_id=spec.project_id,
        task_id=task.id,
        root_id=parent.root_id if parent else run_id,
        parent_id=parent.id if parent else None,
        request_key=key,
        request_digest=request_digest,
        actor=actor,
        version=1,
        seq=0,
        epoch=0,
        status="QUEUED",
        phase="INITIALIZING",
        cancel_requested=False,
        pause_requested=False,
        state={
            "title": spec.title or spec.task.goal[:120],
            "task": spec.task.model_dump(),
            "budget": spec.budget.model_dump(),
            "capabilities": spec.capabilities,
            "baseline_ref": baseline_ref,
            "workspace_ref": baseline_ref,
            "workspace_digest": digest(baseline),
            "semantic": semantic,
            "skills": skill_data,
            "memories": memories,
            "turn": 0,
            "tokens": 0,
            "artifact_version": 1,
            "acceptance": acceptance,
            "verification": None,
            "reason": "Waiting for worker",
            "fixture": spec.model == "fixture",
            "model_errors": 0,
            "evaluation_id": evaluation_id,
        },
    )
    s.add(r)
    s.flush()
    if not parent:
        s.add(
            db.BudgetAccount(
                tenant_id=tenant, id=run_id, limit_micros=int(Decimal(spec.budget.max_cost_usd) * 1_000_000)
            )
        )
    s.add(db.SemanticManifest(tenant_id=tenant, run_id=run_id, data={"bindings": semantic, "digest": digest(semantic)}))
    db.emit(s, r, "RUN_CREATED", "Task contract persisted", task_digest=request_digest)
    checkpoint(s, r)
    db.schedule(s, r)
    return r


def checkpoint(s, run):
    # The checkpoint cursor must name an event that includes every state change it stores.
    db.emit(s, run, "STATE_CHECKPOINTED", "Logical state committed before snapshot publication")
    manifest = {
        "schema_version": 2,
        "event_seq": run.seq,
        "workspace": run.state["workspace_ref"],
        "semantic_digest": digest(run.state["semantic"]),
        "state_digest": digest(run.state),
        "acceptance_digest": digest(run.state["acceptance"]),
        "state": run.state,
        "projection": db.projection(run),
    }
    ref = objects.put(run.tenant_id, run.id, manifest)
    cp = db.Checkpoint(tenant_id=run.tenant_id, run_id=run.id, status="READY", data={"manifest_ref": ref, **manifest})
    s.add(cp)
    s.flush()
    run.state = {**run.state, "checkpoint_id": cp.id}
    db.emit(
        s,
        run,
        "CHECKPOINT_READY",
        "Logical state and workspace snapshot persisted",
        checkpoint_id=cp.id,
        manifest_ref=ref,
    )
    return cp


def reserve(s, run, operation_id, amount, token_upper=0):
    account = db.get(s, db.BudgetAccount, run.tenant_id, run.root_id, True)
    existing = s.scalar(
        select(db.BudgetEntry).where(
            db.BudgetEntry.tenant_id == run.tenant_id, db.BudgetEntry.operation_id == operation_id
        )
    )
    if existing:
        return existing
    if amount < 0 or account.spent + account.reserved + amount > account.limit_micros:
        raise Fault("BUDGET_LIMIT", "Root budget has insufficient unreserved funds")
    if run.parent_id:
        entries = [
            e
            for e in db.rows(s, db.BudgetEntry, run.tenant_id, account_id=run.root_id)
            if e.data.get("run_id") == run.id
        ]
        allocated = sum(e.actual if e.status == "settled" else e.reserved for e in entries)
        if allocated + amount > int(Decimal(run.state["budget"]["max_cost_usd"]) * 1_000_000):
            raise Fault("CHILD_BUDGET_LIMIT", "Child allocation has insufficient funds")
    root = db.get(s, db.Run, run.tenant_id, run.root_id)
    resources = account.resources or {}
    used = resources.get("tokens_spent", 0) + resources.get("tokens_reserved", 0)
    if token_upper < 0 or used + token_upper > root.state["budget"].get("max_tokens", 500000):
        raise Fault("TOKEN_BUDGET_LIMIT", "Root token budget has insufficient unreserved capacity")
    account.resources = {**resources, "tokens_reserved": resources.get("tokens_reserved", 0) + token_upper}
    account.reserved += amount
    entry = db.BudgetEntry(
        tenant_id=run.tenant_id,
        account_id=run.root_id,
        operation_id=operation_id,
        reserved=amount,
        status="reserved",
        data={
            "pricing": {"input": settings.input_price, "output": settings.output_price},
            "run_id": run.id,
            "tokens_reserved": token_upper,
        },
    )
    s.add(entry)
    return entry


def settle(s, run, operation_id, actual, actual_tokens=None):
    account = db.get(s, db.BudgetAccount, run.tenant_id, run.root_id, True)
    entry = s.scalar(
        select(db.BudgetEntry)
        .where(db.BudgetEntry.tenant_id == run.tenant_id, db.BudgetEntry.operation_id == operation_id)
        .with_for_update()
    )
    if not entry or entry.status == "settled":
        return
    if actual is None:
        entry.status = "unknown"
        return
    account.reserved -= entry.reserved
    account.spent += actual
    tokens_reserved = entry.data.get("tokens_reserved", 0)
    tokens = tokens_reserved if actual_tokens is None else actual_tokens
    resources = account.resources or {}
    account.resources = {
        **resources,
        "tokens_reserved": resources.get("tokens_reserved", 0) - tokens_reserved,
        "tokens_spent": resources.get("tokens_spent", 0) + tokens,
    }
    entry.data = {**entry.data, "tokens_actual": tokens, "tokens_conservative": actual_tokens is None}
    entry.actual, entry.status = actual, "settled"
    if actual > entry.reserved or tokens > tokens_reserved:
        run.state = {**run.state, "reason": "Provider usage exceeded the configured price bound"}
        run.pause_requested = True


def consume_tool_slot(s, run):
    account = db.get(s, db.BudgetAccount, run.tenant_id, run.root_id, True)
    root = db.get(s, db.Run, run.tenant_id, run.root_id)
    resources = account.resources or {}
    calls = resources.get("tool_calls", 0)
    if calls >= root.state["budget"]["max_tool_calls"]:
        raise Fault("TOOL_BUDGET_LIMIT", "Root tool-call budget exhausted")
    account.resources = {**resources, "tool_calls": calls + 1}


def control(s, tenant, id, command, expected_version, reason):
    r = db.get(s, db.Run, tenant, id, True)
    if command == "cancel" and r.cancel_requested:
        if r.status not in TERMINAL:
            if r.status != "CANCELLING":
                r.status = "CANCELLING"
                db.emit(s, r, "CANCEL_REASSERTED", "Existing cancellation intent rescheduled")
            db.schedule(s, r)
        return r
    if r.version != expected_version:
        raise Fault("VERSION_CONFLICT", "Task changed; refresh before submitting")
    if r.status in TERMINAL:
        raise Fault("TERMINAL_RUN", "A finished run cannot be resumed; create a fork")
    if command == "cancel":
        r.cancel_requested = True
        r.status = "CANCELLING"
        for child in db.rows(s, db.Run, tenant, parent_id=id):
            if child.status not in TERMINAL:
                child.cancel_requested = True
                child.status = "CANCELLING"
                db.emit(s, child, "CANCEL_REQUESTED", "Parent task cancelled")
                db.schedule(s, child)
    elif command == "pause":
        r.pause_requested = True
        if not r.lease_owner:
            r.status = "PAUSED"
    elif command == "resume":
        if r.status != "PAUSED" or r.cancel_requested:
            raise Fault("INVALID_STATE", "Only paused tasks can resume")
        if any(a.status in UNSETTLED for a in db.rows(s, db.Action, tenant, run_id=id)):
            raise Fault("UNRESOLVED_EFFECT", "Reconcile pending effects first")
        require_settled_usage(s, r)
        authorization(s, r)
        if digest(TOOLS) != r.state["semantic"]["tools_digest"]:
            raise Fault("SEMANTIC_DRIFT", "Pinned tool implementation is not available")
        if r.state["semantic"].get("implementation") != implementation_bindings():
            raise Fault("SEMANTIC_DRIFT", "Pinned implementation changed; create a new fork using current code")
        objects.get(tenant, r.state["workspace_ref"])
        if r.state.get("checkpoint_id"):
            cp = db.get(s, db.Checkpoint, tenant, r.state["checkpoint_id"])
            manifest = json.loads(objects.get(tenant, cp.data["manifest_ref"]))
            if digest(manifest["state"]) != manifest["state_digest"]:
                raise Fault("CHECKPOINT_CORRUPT", "Checkpoint state digest mismatch")
            objects.get(tenant, manifest["workspace"])
        r.pause_requested = False
        r.status, r.wait_reason = "QUEUED", None
    else:
        raise Fault("INVALID_COMMAND", "Unknown task control", 422)
    r.state = {**r.state, "reason": reason}
    db.emit(s, r, command.upper() + "_REQUESTED", reason)
    db.schedule(s, r)
    return r


def prepare_action(s, run, call, logical_key):
    consume_tool_slot(s, run)
    error = None
    tool = TOOLS.get(call.tool, {"effect": "read", "capability": "repo.read"})
    try:
        if call.tool not in TOOLS:
            raise Fault("TOOL_UNAVAILABLE", "Tool is not in the pinned tool catalog", 422)
        TOOL_INPUTS[call.tool].model_validate(call.args)
        authorization(s, run, tool["capability"])
    except (Fault, ValidationError) as exc:
        error = str(exc)[:2000]
    a = db.Action(
        tenant_id=run.tenant_id,
        run_id=run.id,
        logical_key=logical_key,
        tool=call.tool,
        args=call.args,
        effect_class=tool["effect"],
        status="FAILED" if error else "READY",
        attempt=0,
        epoch=run.epoch,
        receipt={"exit_code": 1, "error": error} if error else None,
        effect_digest=digest(
            {
                "tool": call.tool,
                "version": 1,
                "args": call.args,
                "actor": run.actor,
                "scope": run.state["task"]["allowed_paths"],
            }
        ),
    )
    s.add(a)
    s.flush()
    db.emit(s, run, "ACTION_PREPARED", f"Prepared {call.tool}", action_id=a.id, effect_digest=a.effect_digest)
    return a


def approve(s, tenant, actor, approval_id, body, admin=False):
    approval = db.get(s, db.Approval, tenant, approval_id)
    action = db.get(s, db.Action, tenant, approval.action_id)
    run = db.get(s, db.Run, tenant, action.run_id, True)
    require_run_access(s, Identity(tenant, actor, admin), run, "approve")
    approval = db.get(s, db.Approval, tenant, approval_id, True)
    if run.version != body.expected_version:
        raise Fault("VERSION_CONFLICT", "Task changed; review its latest version")
    auth = authorization(s, run, "external.write")
    if approval.decision != "pending" or approval.expires_at <= db.clock(s) or run.cancel_requested:
        raise Fault("APPROVAL_EXPIRED", "Approval is no longer valid")
    if (
        approval.effect_digest != body.effect_digest
        or action.effect_digest != body.effect_digest
        or auth.data["epoch"] != approval.policy_epoch
    ):
        raise Fault("APPROVAL_STALE", "Effect or current authorization changed")
    if body.decision == "deny" and not body.reason.strip():
        raise Fault("REASON_REQUIRED", "A denial requires a reason", 422)
    approval.decision = "approved" if body.decision == "approve" else "denied"
    approval.reviewer, approval.reason = actor, body.reason
    action.status = "READY" if body.decision == "approve" else "FAILED"
    if action.status == "FAILED":
        action.receipt = {"error": "Approval denied", "exit_code": 1}
    run.status = "QUEUED" if body.decision == "approve" else "PAUSED"
    run.wait_reason = None
    db.emit(s, run, "APPROVAL_DECIDED", body.reason, approval_id=approval_id, decision=approval.decision)
    db.schedule(s, run)
    return run


def replay(s, tenant, id):
    from .reducer import rebuild

    db.get(s, db.Run, tenant, id)
    events = sorted(db.rows(s, db.Event, tenant, run_id=id), key=lambda e: e.seq)
    return {
        "events": [{"seq": e.seq, "type": e.type, "payload": e.payload} for e in events],
        "projection": rebuild(s, tenant, id) if events else None,
    }


def seal_acceptance(tenant, namespace, contract):
    contract = dict(contract)
    tests = contract.pop("protected_tests", None)
    if tests:
        contract["protected_tests_ref"] = objects.put(tenant, namespace, tests)
    return contract


def resolve_acceptance(tenant, contract):
    result = dict(contract)
    if result.get("protected_tests_ref"):
        result["protected_tests"] = json.loads(objects.get(tenant, result["protected_tests_ref"]))
    return result


def public_payload(value):
    """Legacy snapshots also pass through this boundary; no protected test or object locator escapes."""
    if isinstance(value, dict):
        result = {k: public_payload(v) for k, v in value.items() if k not in {"protected_tests", "protected_tests_ref"}}
        if value.get("acceptance") and value.get("verification"):
            result["verification"] = public_report(value["acceptance"], value["verification"])
        return result
    if isinstance(value, list):
        return [public_payload(v) for v in value]
    return value


def public_report(contract, value):
    value = public_payload(value)
    if contract.get("protected_tests") or contract.get("protected_tests_ref"):
        if value.get("result"):
            value["result"] = {**value["result"], "output": "Protected acceptance diagnostics are private"}
        value["checks"] = [
            {**c, "evidence": "Protected acceptance diagnostics are private"} if c.get("name") == "独立验收" else c
            for c in value.get("checks", [])
        ]
    return value


def require_settled_usage(s, run):
    # A root completion includes all descendant accounts, even zero-dollar/token-only reservations.
    run_ids = {r.id for r in db.rows(s, db.Run, run.tenant_id, root_id=run.root_id)} if not run.parent_id else {run.id}
    if any(c.run_id in run_ids and c.status in {"DISPATCHED", "UNKNOWN"}
           for c in db.rows(s, db.ModelCall, run.tenant_id)):
        raise Fault("UNKNOWN_USAGE", "Reconcile outstanding model usage before continuing")
    if any(e.status != "settled" and e.data.get("run_id") in run_ids
           for e in db.rows(s, db.BudgetEntry, run.tenant_id, account_id=run.root_id)):
        raise Fault("UNSETTLED_BUDGET", "All root budget reservations must settle before continuing")
    account = db.get(s, db.BudgetAccount, run.tenant_id, run.root_id)
    if not run.parent_id and (account.reserved or (account.resources or {}).get("tokens_reserved", 0)):
        raise Fault("UNSETTLED_BUDGET", "Root budget still contains reserved funds or tokens")


def require_finalizable(s, run):
    require_settled_usage(s, run)
    if any(c.data.get("receipt_state") == "received" for c in db.rows(s, db.ModelCall, run.tenant_id, run_id=run.id)):
        raise Fault("UNCONSUMED_RESPONSE", "Consume or supersede persisted model decisions before verification")
    if any(a.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}
           for a in db.rows(s, db.Action, run.tenant_id, run_id=run.id)):
        raise Fault("UNSETTLED_ACTION", "Pending actions must settle before verification")
    if any(c.status != "SUCCEEDED" for c in db.rows(s, db.Run, run.tenant_id, parent_id=run.id)):
        raise Fault("UNSETTLED_CHILD", "Required child runs must succeed before verification")
