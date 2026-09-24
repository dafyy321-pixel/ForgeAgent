import asyncio
import json
import math
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import AwareDatetime, Field
from sqlalchemy import select, text

from . import db, presenters, service
from . import evaluations as experiments
from .auth import Identity, identity, require_admin
from .config import settings
from .domain import (
    TERMINAL,
    ApprovalDecision,
    Control,
    CreateRun,
    Fault,
    Strict,
    Task,
    canonical,
    digest,
    uid,
)
from .remote import accept_callback, prepare_remote, validate_url
from .sandbox import safe_path, sandbox
from .storage import objects


@asynccontextmanager
async def lifespan(app):
    if settings.auth_mode == "local":
        service.provision()
    yield


app = FastAPI(title="ForgeAgent Runtime", version="0.2.0", lifespan=lifespan)
Actor = Annotated[Identity, Depends(identity)]


@app.middleware("http")
async def request_limits(request, call_next):
    correlation = uid()
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > 2 * 1024 * 1024):
        return JSONResponse(
            {"code": "BODY_LIMIT", "message": "Request body limit is 2 MiB", "correlation_id": correlation}, 413
        )
    if request.method in {"POST", "PUT", "PATCH"}:
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 2 * 1024 * 1024:
                return JSONResponse(
                    {"code": "BODY_LIMIT", "message": "Request body limit is 2 MiB", "correlation_id": correlation}, 413
                )
        request._body = bytes(body)
    request.state.correlation_id = correlation
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Fault)
async def fault_handler(request, exc):
    return JSONResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
            "run_id": request.path_params.get("id"),
            "action_id": request.path_params.get("action_id"),
            "correlation_id": getattr(request.state, "correlation_id", uid()),
        },
        status_code=exc.status,
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse(
        {
            "code": "VALIDATION_ERROR",
            "message": "Request does not match the API contract",
            "errors": [{"path": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
            "retryable": False,
            "correlation_id": request.state.correlation_id,
        },
        422,
    )


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    with db.engine.connect() as c:
        c.execute(text("SELECT 1"))
    return {"status": "ready", "database": "postgresql"}


@app.get("/v1/workspace")
def workspace(actor: Actor):
    with db.transaction(actor.tenant) as s:
        return presenters.workspace(s, actor.tenant)


@app.post("/v1/runs", status_code=202)
def create(body: CreateRun, actor: Actor, idempotency_key: Annotated[str, Header()]):
    with db.transaction(actor.tenant) as s:
        r = service.create_run(s, actor.tenant, actor.actor, body, idempotency_key)
        return presenters.run_view(s, r)


@app.get("/v1/runs")
def runs(
    actor: Actor, project: str | None = None, status: str | None = None, cursor: str | None = None, limit: int = 50
):
    with db.transaction(actor.tenant) as s:
        q = select(db.Run).where(db.Run.tenant_id == actor.tenant)
        if project:
            q = q.where(db.Run.project_id == project)
        if status:
            q = q.where(db.Run.status == status)
        if cursor:
            q = q.where(db.Run.id > cursor)
        values = list(s.scalars(q.order_by(db.Run.id).limit(min(max(limit, 1), 100) + 1)))
        more = len(values) > min(max(limit, 1), 100)
        values = values[: min(max(limit, 1), 100)]
        return {"items": [presenters.run_view(s, r) for r in values], "next_cursor": values[-1].id if more else None}


@app.get("/v1/runs/{id}")
def run(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        return presenters.run_view(s, db.get(s, db.Run, actor.tenant, id))


@app.post("/v1/runs/{id}/pause", status_code=202)
@app.post("/v1/runs/{id}/resume", status_code=202)
@app.post("/v1/runs/{id}/cancel", status_code=202)
def control(id: str, body: Control, request: Request, actor: Actor):
    with db.transaction(actor.tenant) as s:
        if body.checkpoint_id:
            cp = db.get(s, db.Checkpoint, actor.tenant, body.checkpoint_id)
            if cp.run_id != id:
                raise Fault("CHECKPOINT_SCOPE", "Checkpoint does not belong to this run", 404)
        r = service.control(s, actor.tenant, id, request.url.path.rsplit("/", 1)[1], body.expected_version, body.reason)
        return presenters.run_view(s, r)


@app.post("/v1/runs/{id}/fork", status_code=202)
def fork(id: str, body: Control, actor: Actor, idempotency_key: Annotated[str, Header()]):
    with db.transaction(actor.tenant) as s:
        old = db.get(s, db.Run, actor.tenant, id, True)
        if old.version != body.expected_version:
            raise Fault("VERSION_CONFLICT", "Task changed")
        original = db.get(s, db.TaskSpec, actor.tenant, old.task_id)
        spec = CreateRun.model_validate(original.data)
        spec.title = old.state["title"] + " (fork)"
        spec.capabilities = [c for c in spec.capabilities if c != "external.write"]
        r = service.create_run(s, actor.tenant, actor.actor, spec, idempotency_key)
        if not r.state.get("forked_from") and r.status == "QUEUED":
            source = old.state["workspace_ref"]
            if body.checkpoint_id:
                cp = db.get(s, db.Checkpoint, actor.tenant, body.checkpoint_id)
                if cp.run_id != id:
                    raise Fault("CHECKPOINT_SCOPE", "Checkpoint not in source run", 404)
                source = cp.data["workspace"]
            content = json.loads(objects.get(actor.tenant, source))
            r.state = {
                **r.state,
                "forked_from": id,
                "workspace_ref": objects.put(actor.tenant, r.id, content),
                "workspace_digest": digest(content),
            }
            db.emit(s, r, "RUN_FORKED", body.reason, source_run_id=id)
            service.checkpoint(s, r)
        return presenters.run_view(s, r)


@app.get("/v1/runs/{id}/replay")
def replay(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        return service.replay(s, actor.tenant, id)


@app.get("/v1/runs/{id}/events")
async def events(
    id: str, request: Request, actor: Actor, after: int = 0, last_event_id: Annotated[str | None, Header()] = None
):
    try:
        cursor = int(last_event_id or after)
        if cursor < 0:
            raise ValueError()
    except ValueError:
        raise Fault("INVALID_CURSOR", "Event cursor must be a nonnegative integer", 422)
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id)
        if cursor > r.seq:
            raise Fault("INVALID_CURSOR", "Cursor exceeds the latest committed event", 409)

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            with db.transaction(actor.tenant) as s:
                r = db.get(s, db.Run, actor.tenant, id)
                batch = list(
                    s.scalars(
                        select(db.Event)
                        .where(db.Event.tenant_id == actor.tenant, db.Event.run_id == id, db.Event.seq > cursor)
                        .order_by(db.Event.seq)
                        .limit(100)
                    )
                )
                done = r.status in TERMINAL
            for e in batch:
                cursor = e.seq
                value = {"seq": e.seq, "type": e.type, "payload": e.payload}
                yield f"id: {e.seq}\nevent: domain\ndata: {canonical(value).decode()}\n\n"
            if done and not batch:
                break
            if not batch:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/v1/runs/{id}/actions")
def actions(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        db.get(s, db.Run, actor.tenant, id)
        return [presenters.action_view(s, a) for a in db.rows(s, db.Action, actor.tenant, run_id=id)]


@app.get("/v1/actions/{action_id}")
def action(action_id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        return presenters.action_view(s, db.get(s, db.Action, actor.tenant, action_id))


class Reconcile(Control):
    outcome: Literal["occurred", "absent"]
    evidence: str = Field(min_length=10, max_length=10000)


@app.post("/v1/actions/{action_id}/reconcile")
def reconcile(action_id: str, body: Reconcile, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        a = db.get(s, db.Action, actor.tenant, action_id)
        r = db.get(s, db.Run, actor.tenant, a.run_id, True)
        if r.version != body.expected_version or a.status != "UNKNOWN":
            raise Fault("RECONCILE_CONFLICT", "Action is no longer awaiting reconciliation")
        a.status = "SUCCEEDED" if body.outcome == "occurred" else "FAILED"
        a.receipt = {"manual": True, "reviewer": actor.actor, "evidence": body.evidence, "outcome": body.outcome}
        for job in db.rows(s, db.Outbox, actor.tenant):
            if job.data.get("action_id") == a.id and job.status in {"unknown", "dispatching"}:
                job.status = "reconciled"
                job.data = {
                    **job.data,
                    "evidence": body.evidence,
                    "reviewer": actor.actor,
                    "whole_action_outcome": body.outcome,
                }
        r.status, r.wait_reason = "CANCELLING" if r.cancel_requested else "PAUSED", None
        db.emit(s, r, "ACTION_RECONCILED", body.reason, action_id=a.id, receipt=a.receipt)
        db.schedule(s, r)
        return presenters.action_view(s, a)


@app.post("/v1/outbox/{id}/reconcile")
def reconcile_outbox(id: str, body: Reconcile, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        job = db.get(s, db.Outbox, actor.tenant, id)
        action = db.get(s, db.Action, actor.tenant, job.data["action_id"])
        run = db.get(s, db.Run, actor.tenant, action.run_id, True)
        job = db.get(s, db.Outbox, actor.tenant, id, True)
        if run.version != body.expected_version or job.status != "unknown" or action.status != "UNKNOWN":
            raise Fault("RECONCILE_CONFLICT", "Input delivery no longer awaits reconciliation")
        job.status = "sent" if body.outcome == "occurred" else "absent"
        job.data = {**job.data, "evidence": body.evidence, "reviewer": actor.actor}
        action.status = "RUNNING"
        run.status, run.wait_reason = "WAITING", "TOOL"
        db.emit(s, run, "REMOTE_INPUT_RECONCILED", body.reason, outbox_id=id, outcome=body.outcome)
        db.schedule(s, run)
        return {"id": id, "status": job.status}


class RemoteTaskInput(Control):
    responses: dict


@app.post("/v1/actions/{action_id}/input", status_code=202)
def remote_input(action_id: str, body: RemoteTaskInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        a = db.get(s, db.Action, actor.tenant, action_id)
        r = db.get(s, db.Run, actor.tenant, a.run_id, True)
        if r.version != body.expected_version or r.cancel_requested or a.status != "RUNNING" or not a.receipt:
            raise Fault("REMOTE_INPUT_CONFLICT", "Remote task must be awaiting current reviewed input")
        connection = a.args.get("connection", {})
        if connection.get("kind") != "mcp" or connection.get("protocol") != "2026-07-28":
            raise Fault("REMOTE_INPUT_PROTOCOL", "Durable input responses are supported by MCP Tasks only", 422)
        requests = a.receipt.get("response", {}).get("inputRequests", {})
        if not body.responses or not set(body.responses) <= set(requests):
            raise Fault("REMOTE_INPUT_KEYS", "Responses must match outstanding input request keys", 422)
        if any(requests[key].get("method") != "elicitation/create" for key in body.responses):
            raise Fault(
                "REMOTE_INPUT_CAPABILITY",
                "Remote sampling or tool execution requests require a separate scoped action",
                403,
            )
        auth = service.authorization(s, r, "external.write")
        key = digest({"action_id": a.id, "responses": body.responses})[7:]
        existing = s.get(db.Outbox, (actor.tenant, key))
        if existing and existing.status == "absent":
            existing.status = "pending"
            existing.data = {**existing.data, "policy_epoch": auth.data["epoch"], "reviewer": actor.actor}
        elif existing:
            return {"id": key, "status": existing.status}
        else:
            s.add(
                db.Outbox(
                    tenant_id=actor.tenant,
                    id=key,
                    status="pending",
                    data={
                        "action_id": a.id,
                        "policy_epoch": auth.data["epoch"],
                        "reviewer": actor.actor,
                        "params": {"taskId": a.receipt["remote_task_id"], "inputResponses": body.responses},
                    },
                )
            )
        r.status, r.wait_reason = "WAITING", "TOOL"
        r.state = {**r.state, "input_required": False}
        db.emit(s, r, "REMOTE_INPUT_QUEUED", body.reason, outbox_id=key)
        db.schedule(s, r)
        return {"id": key, "status": "pending"}


@app.get("/v1/runs/{id}/checkpoints")
def checkpoints(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        db.get(s, db.Run, actor.tenant, id)
        return [{"id": c.id, "status": c.status, **c.data} for c in db.rows(s, db.Checkpoint, actor.tenant, run_id=id)]


@app.get("/v1/actions/{action_id}/observation")
def observation(action_id: str, actor: Actor, line_start: int = 0, max_lines: int = 100):
    with db.transaction(actor.tenant) as s:
        a = db.get(s, db.Action, actor.tenant, action_id)
        if not a.receipt or not a.receipt.get("ref"):
            raise Fault("NOT_FOUND", "No stored observation", 404)
        body = objects.get(actor.tenant, a.receipt["ref"]).decode("utf-8", errors="replace")
        lines = body.splitlines()
        start = max(0, line_start)
        return {
            "digest": a.receipt["ref"]["digest"],
            "line_start": start,
            "total_lines": len(lines),
            "text": "\n".join(lines[start : start + min(max(1, max_lines), 500)])[:65536],
        }


@app.post("/v1/runs/{id}/bind-model")
def bind_model(id: str, body: Control, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version or r.status != "PAUSED" or r.lease_owner or r.state["fixture"]:
            raise Fault("MODEL_BIND_CONFLICT", "Model changes require a paused real-model task at the current version")
        if not settings.model_id or not settings.model_api_key:
            raise Fault("MODEL_NOT_CONFIGURED", "Configure a model in the server environment first", 422)
        semantic = {
            **r.state["semantic"],
            "model_id": settings.model_id,
            "model_provider": settings.model_provider,
            "model_profile": service.model_profile(),
        }
        r.state = {**r.state, "semantic": semantic}
        s.add(
            db.SemanticManifest(
                tenant_id=actor.tenant,
                run_id=id,
                data={"bindings": semantic, "digest": digest(semantic), "reason": body.reason},
            )
        )
        db.emit(s, r, "MODEL_BINDING_CHANGED", body.reason, model=settings.model_id, provider=settings.model_provider)
        return presenters.run_view(s, r)


class BudgetReconciliation(Control):
    actual_micros: int = Field(ge=0)
    actual_tokens: int | None = Field(None, ge=0)
    evidence: str = Field(min_length=10)


@app.get("/v1/runs/{id}/budget")
def budget_ledger(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id)
        a = db.get(s, db.BudgetAccount, actor.tenant, r.root_id)
        return {
            "root_id": r.root_id,
            "limit_micros": a.limit_micros,
            "spent_micros": a.spent,
            "reserved_micros": a.reserved,
            "resources": a.resources,
            "limits": db.get(s, db.Run, actor.tenant, r.root_id).state["budget"],
            "entries": [
                {
                    "id": e.id,
                    "operation_id": e.operation_id,
                    "status": e.status,
                    "reserved": e.reserved,
                    "actual": e.actual,
                    **e.data,
                }
                for e in db.rows(s, db.BudgetEntry, actor.tenant, account_id=r.root_id)
            ],
        }


@app.post("/v1/runs/{id}/budget/{operation_id}/reconcile")
def reconcile_budget(id: str, operation_id: str, body: BudgetReconciliation, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version:
            raise Fault("VERSION_CONFLICT", "Task changed")
        call = db.get(s, db.ModelCall, actor.tenant, operation_id)
        if call.run_id != id or call.status != "UNKNOWN":
            raise Fault("BUDGET_CONFLICT", "Only this run's unknown model usage can be reconciled")
        service.settle(s, r, operation_id, body.actual_micros, body.actual_tokens)
        call.status = "RECONCILED"
        call.data = {**call.data, "billing_evidence": body.evidence, "reviewer": actor.actor}
        db.emit(s, r, "BUDGET_RECONCILED", body.reason, operation_id=operation_id, actual_micros=body.actual_micros)
        return {"status": "settled"}


@app.get("/v1/runs/{id}/artifacts")
def artifacts(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        db.get(s, db.Run, actor.tenant, id)
        return [
            {
                "id": a.id,
                "name": a.name,
                "kind": a.kind,
                "digest": a.ref["digest"],
                "bytes": a.ref["bytes"],
                "verified": a.verified,
                "download_url": f"/v1/artifacts/{a.id}/download",
            }
            for a in db.rows(s, db.Artifact, actor.tenant, run_id=id)
        ]


@app.get("/v1/artifacts/{artifact_id}/download")
def download(artifact_id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        a = db.get(s, db.Artifact, actor.tenant, artifact_id)
        body = objects.get(actor.tenant, a.ref)
        return Response(
            body,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{a.id}.txt"'},
        )


@app.post("/v1/approvals/{id}/decision")
def decide(id: str, body: ApprovalDecision, actor: Actor):
    with db.transaction(actor.tenant) as s:
        return presenters.run_view(s, service.approve(s, actor.tenant, actor.actor, id, body))


@app.get("/v1/runs/{id}/context/{turn}")
def context(id: str, turn: int, actor: Actor):
    with db.transaction(actor.tenant) as s:
        db.get(s, db.Run, actor.tenant, id)
        contexts = db.rows(s, db.ContextManifest, actor.tenant, run_id=id)
        chosen = next((c for c in contexts if c.data["turn"] == turn), None)
        if not chosen:
            raise Fault("NOT_FOUND", "Context turn not found", 404)
        return chosen.data


class UserInput(Control):
    content: str = Field(min_length=1, max_length=10000)


@app.post("/v1/runs/{id}/input")
def user_input(id: str, body: UserInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version or r.status != "PAUSED":
            raise Fault("INPUT_CONFLICT", "Input requires the current paused task version")
        r.state = {**r.state, "input": body.content, "input_required": False}
        db.emit(s, r, "USER_INPUT", body.content)
        return presenters.run_view(s, r)


@app.post("/v1/runs/{id}/verify", status_code=202)
def verify_run(id: str, body: Control, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if (
            r.version != body.expected_version
            or r.status not in {"ACTIVE", "PAUSED"}
            or r.cancel_requested
            or r.lease_owner
        ):
            raise Fault("VERIFY_CONFLICT", "Verification requires an idle active or paused task at the current version")
        if any(
            a.status not in {"SUCCEEDED", "FAILED", "CANCELLED"} for a in db.rows(s, db.Action, actor.tenant, run_id=id)
        ):
            raise Fault("UNSETTLED_ACTION", "Pending actions must settle before verification")
        r.state = {**r.state, "completion": "User requested independent verification"}
        r.status, r.phase, r.pause_requested = "QUEUED", "VERIFYING", False
        db.emit(s, r, "VERIFICATION_REQUESTED", body.reason)
        db.schedule(s, r)
        return presenters.run_view(s, r)


@app.post("/v1/runs/{id}/reapprove", status_code=202)
def reapprove(id: str, body: Control, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version or r.status != "PAUSED" or r.cancel_requested or r.lease_owner:
            raise Fault("APPROVAL_CONFLICT", "A new review requires the current paused task")
        actions = db.rows(s, db.Action, actor.tenant, run_id=id)
        if any(a.status in {"UNKNOWN", "RUNNING", "DISPATCHED", "WAITING_APPROVAL", "READY"} for a in actions):
            raise Fault("PENDING_ACTION", "Resolve existing pending actions before requesting a new review")
        old = next((a for a in reversed(actions) if a.tool == "remote.call" and a.status == "FAILED"), None)
        if not old:
            raise Fault("NO_REVIEWABLE_ACTION", "No denied or failed remote action available", 404)
        a = prepare_remote(s, r, old.args["connection_id"], old.args["operation"], old.args["arguments"])
        r.pause_requested = False
        db.emit(s, r, "NEW_REVIEW_REQUESTED", body.reason, prior_action=old.id, action_id=a.id)
        return presenters.run_view(s, r)


@app.post("/v1/runs/{id}/recheck")
async def recheck(id: str, body: Control, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id)
        fixture = r.state["fixture"]
    try:
        image = None if fixture else await sandbox.image_digest()
        available = True
    except Fault:
        image, available = None, False
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version or r.status in TERMINAL:
            raise Fault("VERSION_CONFLICT", "Task changed during environment check")
        r.state = {**r.state, "environment_ready": available, "checked_image": image}
        db.emit(s, r, "ENVIRONMENT_CHECKED", "Sandbox inspection completed", ready=available, image=image)
        return presenters.run_view(s, r)


class ProjectInput(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=100)
    baseline: dict[str, str]
    acceptance_id: str = Field(min_length=1, max_length=100)
    verification_argv: list[str] = Field(min_length=1, max_length=100)
    protected_tests: dict[str, str] = Field(default_factory=dict)


@app.get("/v1/projects")
def projects(actor: Actor):
    with db.transaction(actor.tenant) as s:
        return [{"id": p.id, **p.data} for p in db.rows(s, db.Project, actor.tenant)]


@app.post("/v1/projects", status_code=201)
def add_project(body: ProjectInput, actor: Actor):
    require_admin(actor)
    if len(canonical(body.model_dump())) > settings.max_object_bytes // 2:
        raise Fault("PROJECT_LIMIT", "Baseline is too large", 413)
    for path in {**body.baseline, **body.protected_tests}:
        safe_path(settings.data_dir.resolve() / "path-validation", path)
    with db.transaction(actor.tenant) as s:
        if s.get(db.Project, (actor.tenant, body.id)):
            raise Fault("PROJECT_EXISTS", "Register a new project revision rather than overwrite an existing baseline")
        data = {
            "name": body.name,
            "baseline": body.baseline,
            "baseline_digest": digest(body.baseline),
            "acceptance": {
                "id": body.acceptance_id,
                "kind": "command",
                "argv": body.verification_argv,
                "protected_tests": body.protected_tests,
            },
        }
        s.add(db.Project(tenant_id=actor.tenant, id=body.id, data=data))
        return {"id": body.id, "digest": data["baseline_digest"]}


class MemoryInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    kind: str = "项目约定"
    source: str = Field(min_length=1, max_length=2000)
    project: str | None = None
    expires_at: AwareDatetime | None = None


@app.post("/v1/memories", status_code=201)
def add_memory(body: MemoryInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        if body.project:
            db.get(s, db.Project, actor.tenant, body.project)
        m = db.Memory(tenant_id=actor.tenant, data={**body.model_dump(mode="json"), "confirmed_by": actor.actor})
        s.add(m)
        s.flush()
        return {"id": m.id, **m.data}


@app.delete("/v1/memories/{id}")
def delete_memory(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        m = db.get(s, db.Memory, actor.tenant, id, True)
        m.status = "deleted"
        # Existing task snapshots remain immutable audit records; new retrieval excludes this memory.
        return {"status": "deleted", "retained_in_historical_snapshots": True}


class SkillInput(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    description: str = Field(min_length=1, max_length=1000)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    content: str = Field(min_length=1, max_length=40000)
    category: str = "开发"
    source: str = Field(min_length=1)
    license: str = Field(min_length=1)


@app.post("/v1/skills", status_code=201)
def register_skill(body: SkillInput, actor: Actor):
    require_admin(actor)
    id = body.name + "@" + body.version
    with db.transaction(actor.tenant) as s:
        if s.get(db.SkillVersion, (actor.tenant, id)):
            raise Fault("IMMUTABLE_VERSION", "Skill version already exists")
        s.add(
            db.SkillVersion(
                tenant_id=actor.tenant,
                id=id,
                status="candidate",
                data={**body.model_dump(), "digest": digest(body.content)},
            )
        )
    return {"id": id, "status": "candidate"}


class SkillRelease(Strict):
    enabled: bool
    review: str = Field(min_length=5, max_length=2000)
    evaluation_id: str | None = None
    configuration: str | None = None


@app.post("/v1/skills/{id}/release")
def release_skill(id: str, body: SkillRelease, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        skill = db.get(s, db.SkillVersion, actor.tenant, id, True)
        evidence = None
        if body.enabled:
            if not body.evaluation_id or not body.configuration:
                raise Fault("SKILL_GATE", "Provide a paired evaluation and candidate configuration before release")
            evidence = experiments.release_gate(s, actor.tenant, id, body.evaluation_id, body.configuration)
        skill.status = "active" if body.enabled else "retired"
        skill.data = {
            **skill.data,
            "release_review": body.review,
            "reviewer": actor.actor,
            "evaluation_id": body.evaluation_id,
            "evaluation_digest": evidence,
        }
        return {"id": id, "status": skill.status}


class WorkspaceSettings(Strict):
    budget: float = Field(ge=0.1, le=100)
    concurrency: int = Field(ge=1, le=4)
    notifications: bool = True
    redact: Literal[True] = True


@app.put("/v1/settings")
def save_settings(body: WorkspaceSettings, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        config = db.get(s, db.PolicyVersion, actor.tenant, "settings", True)
        config.data = body.model_dump()
        return config.data


class RevokeInput(Strict):
    actor: str
    capabilities: list[str]


@app.post("/v1/authorization")
def revoke(body: RevokeInput, actor: Actor):
    require_admin(actor)
    if not set(body.capabilities) <= service.CAPABILITIES:
        raise Fault("CAPABILITIES", "Unknown capability", 422)
    with db.transaction(actor.tenant) as s:
        auth = db.get(s, db.Authorization, actor.tenant, body.actor, True)
        auth.data = {"capabilities": body.capabilities, "epoch": auth.data["epoch"] + 1}
        return auth.data


class ConnectionInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["mcp", "a2a"]
    protocol: Literal["2025-11-25", "2026-07-28", "0.3.0", "1.0"]
    url: str
    tasks_extension: bool = False


@app.get("/v1/connections")
def connections(actor: Actor):
    with db.transaction(actor.tenant) as s:
        return [
            {"id": c.id, "status": c.status, **c.data}
            for c in db.rows(s, db.ToolVersion, actor.tenant)
            if c.data.get("kind") in {"mcp", "a2a"}
        ]


@app.post("/v1/connections", status_code=201)
async def connect(body: ConnectionInput, actor: Actor):
    require_admin(actor)
    if (body.kind == "a2a") != (body.protocol in {"0.3.0", "1.0"}):
        raise Fault("PROTOCOL", "Protocol does not match adapter", 422)
    await validate_url(body.url)
    with db.transaction(actor.tenant) as s:
        c = db.ToolVersion(tenant_id=actor.tenant, data=body.model_dump())
        s.add(c)
        s.flush()
        return {"id": c.id, "status": c.status, **c.data}


class RemoteInput(Control):
    connection_id: str
    operation: str
    arguments: dict


@app.post("/v1/runs/{id}/remote-actions", status_code=202)
def remote_action(id: str, body: RemoteInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version or r.status in TERMINAL or r.cancel_requested or r.lease_owner:
            raise Fault("VERSION_CONFLICT", "Task must be idle at the reviewed version")
        a = prepare_remote(s, r, body.connection_id, body.operation, body.arguments)
        return presenters.action_view(s, a)


@app.post("/v1/callbacks/{provider}")
async def callback(
    provider: str,
    request: Request,
    actor: Actor,
    x_message_id: Annotated[str, Header()],
    x_timestamp: Annotated[int, Header()],
    x_signature: Annotated[str, Header()],
):
    body = await request.body()
    with db.transaction(actor.tenant) as s:
        return accept_callback(s, actor.tenant, provider, x_message_id, x_timestamp, body, x_signature)


@app.post("/v1/examples/smoke", status_code=202)
def smoke(actor: Actor, idempotency_key: Annotated[str, Header()]):
    with db.transaction(actor.tenant) as s:
        spec = CreateRun(
            project_id="runtime-lab",
            title="验证 Runtime 端到端流程",
            model="fixture",
            task=Task(
                goal="修复 add 函数，使其返回两个输入之和", allowed_paths=["src"], acceptance_profile="addition@1"
            ),
        )
        return presenters.run_view(s, service.create_run(s, actor.tenant, actor.actor, spec, idempotency_key))


class EvaluationInput(Strict):
    repetitions: int = Field(3, ge=1, le=30)
    seed: int = 42


@app.post("/v1/evaluation-datasets", status_code=201)
def register_dataset(body: experiments.DatasetInput, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        return experiments.register(s, actor.tenant, body)


@app.get("/v1/evaluation-datasets")
def datasets(actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        return [{"id": e.id, **e.data} for e in db.rows(s, db.EvaluationDataset, actor.tenant)]


@app.post("/v1/experiments", status_code=202)
def start_experiment(body: experiments.ExperimentInput, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        return experiments.start(s, actor.tenant, actor.actor, body)


@app.post("/v1/evaluations", status_code=202)
def evaluate(body: EvaluationInput, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        e = db.Evaluation(
            tenant_id=actor.tenant,
            status="running",
            data={
                "dataset": "runtime-addition-contract@1",
                **body.model_dump(),
                "model": "fixture@1",
                "harness": "forge@0.2.0",
                "dataset_digest": digest("addition-ast@1"),
                "runs": [],
            },
        )
        s.add(e)
        s.flush()
        ids = []
        for i in range(body.repetitions):
            spec = CreateRun(
                project_id="runtime-lab",
                title=f"Contract evaluation {i + 1}",
                model="fixture",
                task=Task(goal="Fix addition", allowed_paths=["src"]),
            )
            r = service.create_run(s, actor.tenant, actor.actor, spec, f"eval:{e.id}:{i}")
            ids.append(r.id)
        e.data = {**e.data, "runs": ids}
        return {"id": e.id, "status": e.status, **e.data}


def evaluation_view(s, e, tenant):
    if e.data.get("kind") == "paired":
        e = db.get(s, db.Evaluation, tenant, e.id, True)
        return experiments.report(s, e, tenant)
    runs = [db.get(s, db.Run, tenant, id) for id in e.data["runs"]]
    results = [
        {
            "id": r.id,
            "status": r.status,
            "cost": db.get(s, db.BudgetAccount, tenant, r.root_id).spent / 1e6,
            "verdict": r.state.get("verification", {}).get("verdict") if r.state.get("verification") else None,
        }
        for r in runs
    ]
    finished = all(r.status in TERMINAL for r in runs)
    n, successes = len(runs), sum(r.status == "SUCCEEDED" for r in runs)
    p, z = successes / n, 1.96
    center, width = (
        (p + z * z / (2 * n)) / (1 + z * z / n),
        z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n),
    )
    total = sum(x["cost"] for x in results)
    if finished:
        e.status = "completed"
        for r, result in zip(runs, results):
            result_id = digest({"evaluation": e.id, "run": r.id})[7:]
            if not s.get(db.EvaluationResult, (tenant, result_id)):
                s.add(
                    db.EvaluationResult(
                        tenant_id=tenant, id=result_id, run_id=r.id, data={"evaluation_id": e.id, **result}
                    )
                )
    return {
        "id": e.id,
        "status": e.status,
        **e.data,
        "results": results,
        "successes": successes,
        "total": n,
        "success_rate": p,
        "wilson_95_ci": [max(0, center - width), min(1, center + width)],
        "cost_per_success": total / successes if successes else None,
        "limitations": "Repeated deterministic fixture, not independent software tasks or a model-quality benchmark.",
    }


@app.get("/v1/evaluations")
def evaluations(actor: Actor):
    with db.transaction(actor.tenant) as s:
        return [evaluation_view(s, e, actor.tenant) for e in db.rows(s, db.Evaluation, actor.tenant)]


@app.get("/v1/evaluations/{id}")
def evaluation(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        return evaluation_view(s, db.get(s, db.Evaluation, actor.tenant, id), actor.tenant)


@app.get("/v1/diagnostics")
async def diagnostics(actor: Actor):
    require_admin(actor)
    try:
        image = await sandbox.image_digest()
        sandbox_status = {"ready": True, "image_digest": image}
    except Fault as exc:
        sandbox_status = {"ready": False, "reason": exc.message}
    return {
        "sandbox": sandbox_status,
        "model": {
            "provider": settings.model_provider,
            "id": settings.model_id,
            "configured": bool(settings.model_api_key and settings.model_id),
            "pricing_configured": settings.input_price > 0 and settings.output_price > 0,
        },
        "object_store": "s3" if settings.s3_endpoint else "local-content-addressed",
        "auth": settings.auth_mode,
        "protocols": {
            "mcp": ["2025-11-25 (SDK)", "2026-07-28 + Tasks draft (experimental adapter)"],
            "a2a": ["0.3.0 (SDK)", "1.0 JSON-RPC (experimental adapter)"],
        },
    }


@app.get("/v1/metrics")
def metrics(actor: Actor):
    with db.transaction(actor.tenant) as s:
        runs = db.rows(s, db.Run, actor.tenant)
        actions = db.rows(s, db.Action, actor.tenant)
        accounts = db.rows(s, db.BudgetAccount, actor.tenant)
        return {
            "runs": len(runs),
            "succeeded": sum(r.status == "SUCCEEDED" for r in runs),
            "waiting": sum(r.status == "WAITING" for r in runs),
            "unknown_actions": sum(a.status == "UNKNOWN" for a in actions),
            "cost_usd": sum(a.spent for a in accounts) / 1e6,
            "reserved_usd": sum(a.reserved for a in accounts) / 1e6,
            "tokens": sum(r.state["tokens"] for r in runs),
            "actions": len(actions),
        }


@app.get("/v1/operations/health")
def operations_health(actor: Actor):
    require_admin(actor)
    from .operations import health

    with db.transaction(actor.tenant) as s:
        return health(s, actor.tenant)


class GarbageCollection(Strict):
    apply: bool = False
    min_age_hours: int = Field(24, ge=24, le=87600)


@app.post("/v1/operations/objects/gc")
def collect_objects(body: GarbageCollection, actor: Actor):
    require_admin(actor)
    from .maintenance import collect

    with db.transaction(actor.tenant) as s:
        return collect(s, actor.tenant, body.apply, body.min_age_hours)


@app.get("/metrics")
def prometheus(actor: Actor):
    values = metrics(actor)
    return Response(
        "".join(f"# TYPE forge_{k} gauge\nforge_{k} {v}\n" for k, v in values.items()),
        media_type="text/plain; version=0.0.4",
    )
