import asyncio
import json
import math
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Integer, String, cast, func, select, text

from . import db, domain, presenters, service
from . import evaluations as experiments
from .auth import Identity, authorized_identity, project_access, require_admin, require_run_access
from .config import settings
from .contracts import CatalogPage, RunPage, RunView, WorkspaceIndex
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
from .repository import RepositoryInput
from .sandbox import safe_path, sandbox
from .storage import objects
from .workspace import FileEntry, validate_manifest


@asynccontextmanager
async def lifespan(app):
    from .telemetry import configure

    configure()
    if settings.auth_mode == "local":
        service.provision()
    yield


app = FastAPI(title="ForgeAgent Runtime", version="0.2.0", lifespan=lifespan)
Actor = Annotated[Identity, Depends(authorized_identity)]


@app.middleware("http")
async def request_limits(request, call_next):
    correlation = uid()
    length = request.headers.get("content-length")
    upload = request.method == "PUT" and request.url.path.startswith("/v1/repository-bundles/")
    maximum = min(settings.max_object_bytes, 16 * 1024 * 1024) if upload else 2 * 1024 * 1024
    if length and (not length.isdigit() or int(length) > maximum):
        return JSONResponse(
            {"code": "BODY_LIMIT", "message": f"Request body limit is {maximum} bytes", "correlation_id": correlation}, 413
        )
    if request.method in {"POST", "PUT", "PATCH"} and not upload:
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


@app.get("/v1/auth/config")
def auth_config():
    if settings.auth_mode == "local":
        return {"mode": "local"}
    if settings.auth_mode != "oidc" or not settings.oidc_issuer.startswith("https://") or not settings.oidc_client_id:
        raise Fault("AUTH_CONFIG", "Configure an HTTPS OIDC authority and public browser client ID", 503)
    return {"mode": "oidc", "authority": settings.oidc_issuer, "client_id": settings.oidc_client_id,
            "scope": settings.oidc_scope, "audience": settings.oidc_audience}


@app.get("/health/ready")
def ready():
    with db.engine.connect() as c:
        c.execute(text("SELECT 1"))
        if c.scalar(text("SELECT version_num FROM alembic_version")) != "0008":
            raise Fault("SCHEMA_VERSION", "Database migration is not ready for this runtime", 503)
    return {"status": "ready", "database": "postgresql"}


@app.get("/v1/workspace", response_model=WorkspaceIndex)
def workspace(actor: Actor, project: str | None = None, status: str | None = None, q: str = "",
              cursor: str | None = None, limit: int = 50, run_id: str | None = None):
    with db.transaction(actor.tenant) as s:
        return presenters.workspace(s, actor.tenant, actor, project, status, q, cursor, limit, run_id)


@app.get("/v1/workspace/revision")
def workspace_revision(actor: Actor):
    from .pagination import visible

    with db.transaction(actor.tenant) as s:
        run_revision = s.execute(select(func.count(), func.coalesce(func.sum(db.Run.version), 0)).where(visible(s, actor))).one()
        metadata = []
        for cls in [db.Approval, db.Artifact, db.SkillVersion, db.Memory, db.Evaluation, db.Project]:
            status_column = cls.decision if cls == db.Approval else cls.status if hasattr(cls, "status") else cls.verified
            from sqlalchemy.dialects.postgresql import aggregate_order_by

            metadata.append(s.scalar(select(func.md5(func.string_agg(
                cls.id + ":" + cast(status_column, String), aggregate_order_by(",", cls.id)
            ))).where(cls.tenant_id == actor.tenant)))
        config = s.get(db.PolicyVersion, (actor.tenant, "settings"))
        authorization = s.get(db.Authorization, (actor.tenant, actor.actor))
        return {"revision": digest([list(run_revision), metadata, config.data if config else {}, authorization.data if authorization else {}])}


@app.get("/v1/workspace/summary")
def workspace_summary(actor: Actor, project: str | None = None):
    from .dashboard import summary

    with db.transaction(actor.tenant) as s:
        return summary(s, actor, project)


@app.get("/v1/catalog/{collection}", response_model=CatalogPage)
def catalog(collection: str, actor: Actor, cursor: str | None = None, limit: int = 50,
            status: str | None = None, run_id: str | None = None, project: str | None = None, verified: bool | None = None):
    from .catalog import page

    with db.transaction(actor.tenant) as s:
        return page(s, actor, collection, cursor, limit, status, run_id, project, verified)


@app.post("/v1/runs", status_code=202, response_model=RunView)
def create(body: CreateRun, actor: Actor, idempotency_key: Annotated[str, Header()]):
    with db.transaction(actor.tenant) as s:
        if not project_access(s, actor, body.project_id, "create"):
            raise Fault("FORBIDDEN", "An explicit project create permission is required", 403)
        r = service.create_run(s, actor.tenant, actor.actor, body, idempotency_key, admin=actor.admin)
        return presenters.run_view(s, r)


@app.get("/v1/runs", response_model=RunPage)
def runs(
    actor: Actor, project: str | None = None, status: str | None = None, cursor: str | None = None, limit: int = 50, q: str = ""
):
    with db.transaction(actor.tenant) as s:
        from .pagination import runs as page_runs

        values, next_cursor = page_runs(s, actor, project, status, q, cursor, limit)
        cache = presenters.run_cache(s, values)
        return {"items": [presenters.run_view(s, r, cache) for r in values], "next_cursor": next_cursor}


@app.get("/v1/runs/{id}", response_model=RunView)
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
            if request.url.path.endswith("/resume"):
                raise Fault("CHECKPOINT_FORK_REQUIRED", "Resume continues current state; use fork to restore a checkpoint", 422)
        r = service.control(s, actor.tenant, id, request.url.path.rsplit("/", 1)[1], body.expected_version, body.reason,
                            executor=body.executor)
        return presenters.run_view(s, r)


@app.post("/v1/runs/{id}/migrate")
def migrate(id: str, body: Control, actor: Actor):
    with db.transaction(actor.tenant) as s:
        run = db.get(s, db.Run, actor.tenant, id)
        require_run_access(s, actor, run, "edit")
        return presenters.run_view(s, service.migrate(s, actor.tenant, id, body))


@app.post("/v1/runs/{id}/fork", status_code=202)
def fork(id: str, body: Control, actor: Actor, idempotency_key: Annotated[str, Header()]):
    with db.transaction(actor.tenant) as s:
        from .knowledge import lock

        lock(s, actor.tenant)
        old = db.get(s, db.Run, actor.tenant, id, True)
        if old.state.get("knowledge_erased"):
            raise Fault("KNOWLEDGE_ERASED", "Erased history cannot be forked")
        if old.version != body.expected_version:
            raise Fault("VERSION_CONFLICT", "Task changed")
        original = db.get(s, db.TaskSpec, actor.tenant, old.task_id)
        spec = CreateRun.model_validate(original.data)
        spec.title = old.state["title"] + " (fork)"
        spec.capabilities = [c for c in spec.capabilities if c != "external.write"]
        r = service.create_run(s, actor.tenant, actor.actor, spec, idempotency_key, admin=actor.admin)
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
        return service.public_payload(service.replay(s, actor.tenant, id))


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
    def authorize_stream():
        with db.transaction(actor.tenant) as s:
            r = db.get(s, db.Run, actor.tenant, id)
            require_run_access(s, actor, r)
            if cursor > r.seq:
                raise Fault("INVALID_CURSOR", "Cursor exceeds the latest committed event", 409)
    await asyncio.to_thread(authorize_stream)

    async def stream():
        nonlocal cursor
        opened = time.monotonic()
        while not await request.is_disconnected():
            if actor.expires_at is not None and time.time() >= actor.expires_at:
                yield 'event: auth_expired\ndata: {"code":"UNAUTHORIZED"}\n\n'
                break
            if time.monotonic() - opened > 300:
                break
            def read_event_batch():
                with db.transaction(actor.tenant) as s:
                    authorization = s.get(db.Authorization, (actor.tenant, actor.actor))
                    if not authorization or authorization.status != "active":
                        raise Fault("FORBIDDEN", "Stream identity was revoked", 403)
                    r = db.get(s, db.Run, actor.tenant, id)
                    require_run_access(s, actor, r)
                    batch = list(
                        s.scalars(
                            select(db.Event)
                            .where(db.Event.tenant_id == actor.tenant, db.Event.run_id == id, db.Event.seq > cursor)
                            .order_by(db.Event.seq)
                            .limit(100)
                        )
                    )
                    done = r.status in TERMINAL
                return batch, done
            try:
                batch, done = await asyncio.to_thread(read_event_batch)
            except Fault as exc:
                yield f"event: authorization_error\ndata: {canonical({'code': exc.code}).decode()}\n\n"
                break
            for e in batch:
                cursor = e.seq
                value = service.public_payload({"seq": e.seq, "type": e.type, "payload": e.payload})
                yield f"id: {e.seq}\nevent: domain\ndata: {canonical(value).decode()}\n\n"
            if done and not batch:
                yield 'event: end\ndata: {"terminal":true}\n\n'
                break
            if not batch:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/v1/runs/{id}/actions")
def actions(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        db.get(s, db.Run, actor.tenant, id)
        values = db.list_rows(s, db.Action, actor.tenant, run_id=id)
        cache = presenters.action_cache(s, values)
        return [presenters.action_view(s, a, cache) for a in values]


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
        cancel = job.data.get("kind") == "cancel"
        if run.version != body.expected_version or job.status != "unknown" or action.status not in ({"RUNNING"} if cancel else {"UNKNOWN"}):
            raise Fault("RECONCILE_CONFLICT", "Input delivery no longer awaits reconciliation")
        job.status = "sent" if body.outcome == "occurred" else "absent"
        job.data = {**job.data, "evidence": body.evidence, "reviewer": actor.actor}
        if cancel:
            action.receipt = {**action.receipt, "cancel_sent": body.outcome == "occurred"}
            # A proven absent cancellation can be retried explicitly with a new control ordinal.
            if body.outcome == "absent":
                job.status = "pending"
        action.status = "RUNNING"
        run.status, run.wait_reason = "CANCELLING" if run.cancel_requested else "WAITING", "TOOL"
        db.emit(s, run, "REMOTE_CONTROL_RECONCILED" if cancel else "REMOTE_INPUT_RECONCILED", body.reason, outbox_id=id, outcome=body.outcome)
        db.schedule(s, run)
        return {"id": id, "status": job.status}


class RemoteTaskInput(Control):
    responses: dict


@app.post("/v1/actions/{action_id}/input", status_code=202)
def remote_input(action_id: str, body: RemoteTaskInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        a = db.get(s, db.Action, actor.tenant, action_id)
        r = db.get(s, db.Run, actor.tenant, a.run_id, True)
        require_run_access(s, actor, r, "approve")
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
        from .remote_contracts import validate_arguments, validate_schema

        if len(canonical(body.responses)) > 65536:
            raise Fault("REMOTE_INPUT_LIMIT", "Reviewed input exceeds 64 KiB", 413)
        for key, response in body.responses.items():
            result = response.get("result", {}) if isinstance(response, dict) else {}
            if result.get("action") not in {"accept", "decline", "cancel"}:
                raise Fault("REMOTE_INPUT_RESULT", "Elicitation response requires a supported action", 422)
            if result["action"] == "accept":
                schema = requests[key].get("params", {}).get("requestedSchema")
                if not schema:
                    raise Fault("REMOTE_INPUT_SCHEMA", "Provider did not supply a reviewable form schema", 422)
                try:
                    validate_schema(schema)
                except ValueError:
                    raise Fault("REMOTE_INPUT_SCHEMA", "Provider form schema is unsupported", 422) from None
                validate_arguments(schema, result.get("content", {}))
        auth = service.authorization(s, r, "external.write")
        input_digest = digest(requests)
        key = digest({"action_id": a.id, "input_digest": input_digest, "responses": body.responses})[7:]
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
                        "input_digest": input_digest,
                        "params": {"taskId": a.receipt["remote_task_id"], "inputResponses": body.responses},
                    },
                )
            )
        r.status, r.wait_reason = ("PAUSED", None) if r.pause_requested else ("WAITING", "TOOL")
        r.state = {**r.state, "input_required": False}
        db.emit(s, r, "REMOTE_INPUT_QUEUED", body.reason, outbox_id=key)
        db.schedule(s, r)
        return {"id": key, "status": "pending"}


@app.get("/v1/runs/{id}/checkpoints")
def checkpoints(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        db.get(s, db.Run, actor.tenant, id)
        return service.public_payload([
            {"id": c.id, "status": c.status, **c.data} for c in db.list_rows(s, db.Checkpoint, actor.tenant, run_id=id)
        ])


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


@app.post("/v1/runs/{id}/budget/{operation_id}/query")
async def query_budget(id: str, operation_id: str, body: Control, actor: Actor):
    require_admin(actor)
    def prepare_query():
        with db.transaction(actor.tenant) as s:
            run = db.get(s, db.Run, actor.tenant, id)
            if run.version != body.expected_version:
                raise Fault("VERSION_CONFLICT", "Task changed")
            call = db.get(s, db.ModelCall, actor.tenant, operation_id)
            if call.run_id != id or call.status != "UNKNOWN":
                raise Fault("BUDGET_CONFLICT", "Only this run's unknown model usage can be queried")
            response_id = (call.data.get("protocol_receipt") or {}).get("response_id")
            if not response_id:
                raise Fault("PROVIDER_QUERY_UNAVAILABLE", "Original response ID was not received; review provider billing evidence")
            return json.loads(objects.get(actor.tenant, call.data["messages_ref"])), response_id
    request, response_id = await asyncio.to_thread(prepare_query)
    from .models import query_billing

    return await query_billing(request, response_id)


@app.get("/v1/runs/{id}/context-cost")
def context_economics(id: str, actor: Actor):
    from .context_cost import report

    with db.transaction(actor.tenant) as s:
        run = db.get(s, db.Run, actor.tenant, id)
        return report([{**c.data, "fixture": run.state["fixture"]} for c in db.rows(s, db.ModelCall, actor.tenant, run_id=id)],
                      [m.data for m in db.rows(s, db.ContextManifest, actor.tenant, run_id=id)])


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
        entry = s.scalar(select(db.BudgetEntry).where(db.BudgetEntry.tenant_id == actor.tenant,
                                                    db.BudgetEntry.operation_id == operation_id))
        call.status = "RECONCILED"
        call.data = {**call.data, "billing_evidence": body.evidence, "reviewer": actor.actor,
                     "reconciled_tokens": entry.data["tokens_actual"],
                     "reconciled_tokens_conservative": entry.data["tokens_conservative"]}
        db.emit(s, r, "BUDGET_RECONCILED", body.reason, operation_id=operation_id, actual_micros=body.actual_micros)
        if r.cancel_requested and r.status not in TERMINAL:
            r.status, r.wait_reason = "CANCELLING", None
            db.emit(s, r, "CANCELLATION_RESUMED", "Billing reconciled; resume cancellation settlement")
            db.schedule(s, r)
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
            for a in db.list_rows(s, db.Artifact, actor.tenant, run_id=id)
        ]


@app.get("/v1/artifacts/{artifact_id}/download")
def download(artifact_id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        a = db.get(s, db.Artifact, actor.tenant, artifact_id)
        body = objects.get(actor.tenant, a.ref)
        if a.kind == "test_report":
            r = db.get(s, db.Run, actor.tenant, a.run_id)
            body = canonical(service.public_report(r.state["acceptance"], json.loads(body)))
        return Response(
            body,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{a.id}.txt"'},
        )


@app.post("/v1/approvals/{id}/decision")
def decide(id: str, body: ApprovalDecision, actor: Actor):
    with db.transaction(actor.tenant) as s:
        return presenters.run_view(s, service.approve(s, actor.tenant, actor.actor, id, body, admin=actor.admin))


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
        if r.cancel_requested:
            raise Fault("INPUT_CONFLICT", "A cancelling task cannot accept new input")
        r.state = {**r.state, "input": body.content, "input_required": False, "completion": None,
                   "verification": None, "input_revision": r.state.get("input_revision", 0) + 1,
                   "artifact_version": r.state["artifact_version"] + 1}
        for artifact in db.rows(s, db.Artifact, actor.tenant, run_id=id):
            artifact.verified = False
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
        service.require_finalizable(s, r)
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
        arguments = {**old.args["arguments"]}
        arguments.pop(old.args.get("contract", {}).get("idempotency_field"), None)
        a = prepare_remote(s, r, old.args["connection_id"], old.args["operation"], arguments)
        r.pause_requested = False
        db.emit(s, r, "NEW_REVIEW_REQUESTED", body.reason, prior_action=old.id, action_id=a.id)
        return presenters.run_view(s, r)


@app.post("/v1/runs/{id}/recheck")
async def recheck(id: str, body: Control, actor: Actor):
    def inspect_run():
        with db.transaction(actor.tenant) as s:
            r = db.get(s, db.Run, actor.tenant, id)
            fixture = r.state["fixture"]
        return fixture
    fixture = await asyncio.to_thread(inspect_run)
    try:
        image = None if fixture else await sandbox.image_digest()
        available = True
    except Fault:
        image, available = None, False
    def save_environment_check():
        with db.transaction(actor.tenant) as s:
            r = db.get(s, db.Run, actor.tenant, id, True)
            if r.version != body.expected_version or r.status in TERMINAL:
                raise Fault("VERSION_CONFLICT", "Task changed during environment check")
            r.state = {**r.state, "environment_ready": available, "checked_image": image}
            db.emit(s, r, "ENVIRONMENT_CHECKED", "Sandbox inspection completed", ready=available, image=image)
            return presenters.run_view(s, r)
    return await asyncio.to_thread(save_environment_check)


class ProjectInput(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=100)
    baseline: dict[str, str | FileEntry] = Field(default_factory=dict)
    repository: RepositoryInput | None = None
    acceptance_id: str = Field(min_length=1, max_length=100)
    verification_argv: list[str] = Field(min_length=1, max_length=100)
    protected_tests: dict[str, str] = Field(default_factory=dict)
    acceptance: domain.AcceptanceContract | None = None
    build: domain.BuildContract = Field(default_factory=domain.BuildContract)


@app.put("/v1/repository-bundles/{checksum}")
async def upload_repository(checksum: str, request: Request, actor: Actor):
    import hashlib
    import re
    import tempfile

    require_admin(actor)
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise Fault("UPLOAD_DIGEST", "Expected lowercase SHA256 hex digest", 422)
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise Fault("UPLOAD_TYPE", "Upload a raw Git bundle as application/octet-stream", 415)
    with tempfile.SpooledTemporaryFile(max_size=settings.object_spool_bytes) as spool:
        count, hasher = 0, hashlib.sha256()
        async for chunk in request.stream():
            count += len(chunk)
            if count > min(settings.max_object_bytes, 16 * 1024 * 1024):
                raise Fault("BODY_LIMIT", "Repository upload exceeds 16 MiB or configured object limit", 413)
            hasher.update(chunk)
            await asyncio.to_thread(spool.write, chunk)
        if not count or hasher.hexdigest() != checksum:
            raise Fault("UPLOAD_DIGEST", "Upload size or SHA256 differs from the declared content", 422)
        spool.seek(0)
        reference = await asyncio.to_thread(objects.put_stream, actor.tenant, "repository-upload", spool)
    return {"bundle_digest": reference["digest"], "bundle_bytes": reference["bytes"]}


@app.get("/v1/projects")
def projects(actor: Actor):
    from .catalog import page

    with db.transaction(actor.tenant) as s:
        ids = [row["id"] for row in page(s, actor, "projects", limit=100)["items"]]
        return service.public_payload([
            {"id": p.id, **p.data} for p in s.scalars(select(db.Project).where(db.Project.tenant_id == actor.tenant,
                db.Project.id.in_(ids)).order_by(db.Project.created_at.desc(), db.Project.id.desc()))
        ])


def prepare_project(body: ProjectInput, actor: Identity):
    if len(canonical(body.model_dump())) > settings.max_object_bytes // 2:
        raise Fault("PROJECT_LIMIT", "Baseline is too large", 413)
    for path in {**body.baseline, **body.protected_tests}:
        safe_path(settings.data_dir.resolve() / "path-validation", path)
    baseline = body.model_dump()["baseline"]
    repository = None
    if body.repository:
        if baseline:
            raise Fault("PROJECT_BASELINE", "A repository baseline is derived from its fixed commit", 422)
        from .repository import register

        repository, baseline = register(actor.tenant, "repository-" + body.id, body.repository)
    validate_manifest(baseline)
    build = body.build.model_dump()
    if build["ecosystem"] != "none":
        from .workspace import body as file_body

        if build["lockfile"] not in baseline or digest(file_body(baseline[build["lockfile"]])) != build["lock_digest"]:
            raise Fault("DEPENDENCY_LOCK_MISMATCH", "Build lockfile does not match the fixed baseline", 422)
    acceptance = body.acceptance.model_dump() if body.acceptance else domain.AcceptanceContract(
        id=body.acceptance_id, argv=body.verification_argv, protected_tests=body.protected_tests).model_dump()
    if acceptance["id"] != body.acceptance_id:
        raise Fault("ACCEPTANCE_PROFILE", "Acceptance IDs must agree", 422)
    if acceptance["kind"] != "command":
        raise Fault("FIXTURE_SCOPE", "Fixture verification is reserved for the built-in runtime lab", 422)
    if body.acceptance and (body.protected_tests or body.verification_argv != acceptance["argv"]):
        raise Fault("ACCEPTANCE_AMBIGUOUS", "Use a single consistent acceptance contract", 422)
    for path in [*acceptance["protected_tests"], *acceptance["protected_paths"], build["lockfile"]]:
        if path:
            safe_path(settings.data_dir.resolve() / "path-validation", path)
    for condition in acceptance["conditions"]:
        if condition["path"]:
            safe_path(settings.data_dir.resolve() / "path-validation", condition["path"])
    return {"name": body.name, "baseline": baseline, "baseline_digest": digest(baseline),
            "repository": repository, "build": build, "acceptance": acceptance}


@app.post("/v1/projects/preflight")
def project_preflight(body: ProjectInput, actor: Actor):
    require_admin(actor)
    data = prepare_project(body, actor)
    return {"commit": (data["repository"] or {}).get("commit"), "files": len(data["baseline"]),
            "baseline_digest": data["baseline_digest"], "build": data["build"],
            "acceptance": {"id": data["acceptance"]["id"], "argv": data["acceptance"]["argv"]},
            "validation": "paths, expanded size, fixed commit and dependency lock validated",
            "acceptance_execution": "not_run", "next_step": "Register this reviewed project, then run independent acceptance in the configured sandbox."}


@app.post("/v1/projects", status_code=201)
def add_project(body: ProjectInput, actor: Actor):
    require_admin(actor)
    data = prepare_project(body, actor)
    data["acceptance"] = service.seal_acceptance(actor.tenant, "project-" + body.id, data["acceptance"])
    with db.transaction(actor.tenant) as s:
        if s.get(db.Project, (actor.tenant, body.id)):
            raise Fault("PROJECT_EXISTS", "Register a new project revision rather than overwrite an existing baseline")
        s.add(db.Project(tenant_id=actor.tenant, id=body.id, data=data))
        return {"id": body.id, "digest": data["baseline_digest"]}


class MemoryInput(Strict):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    kind: str = "项目约定"
    source: str = Field(min_length=1, max_length=2000)
    project: str | None = None
    expires_at: AwareDatetime | None = None
    valid_from: AwareDatetime | None = None
    subject: str = Field("", max_length=200)
    repository_revision: str | None = Field(None, max_length=100)
    supersedes_id: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=20)


@app.post("/v1/memories", status_code=201)
def add_memory(body: MemoryInput, actor: Actor):
    if body.valid_from and body.expires_at and body.valid_from >= body.expires_at:
        raise Fault("MEMORY_VALIDITY", "Memory expiration must follow its valid-from instant", 422)
    with db.transaction(actor.tenant) as s:
        from .knowledge import lock

        lock(s, actor.tenant)
        if body.project:
            db.get(s, db.Project, actor.tenant, body.project)
            if not project_access(s, actor, body.project, "edit"):
                raise Fault("FORBIDDEN", "Project edit permission is required", 403)
        else:
            require_admin(actor)
        data = body.model_dump(mode="json")
        for run_id in body.source_refs:
            source_run = db.get(s, db.Run, actor.tenant, run_id, True)
            require_run_access(s, actor, source_run)
            if source_run.state.get("knowledge_erased"):
                raise Fault("KNOWLEDGE_ERASED", "Erased traces cannot publish derived memory")
            evaluation_id = source_run.state.get("evaluation_id")
            evaluation = s.get(db.Evaluation, (actor.tenant, evaluation_id)) if evaluation_id else None
            if evaluation and evaluation.data.get("split") == "held_out":
                raise Fault("HOLDOUT_LEAKAGE", "Held-out traces cannot publish development memory", 403)
        if body.supersedes_id:
            previous = db.get(s, db.Memory, actor.tenant, body.supersedes_id, True)
            if previous.status != "active":
                raise Fault("MEMORY_CONFLICT", "Only an active memory version can be superseded", 409)
            if previous.data.get("project") != body.project or previous.data.get("subject") != body.subject or not body.subject:
                raise Fault("MEMORY_CONFLICT", "Supersession must bind the same project and explicit subject", 422)
            from .knowledge import withdraw

            withdraw(s, previous)
            previous.status = "superseded"
        version = previous.data.get("version", 1) + 1 if body.supersedes_id else 1
        m = db.Memory(tenant_id=actor.tenant, data={**data, "confirmed_by": actor.actor, "version": version, "digest": digest(data)})
        s.add(m)
        s.flush()
        return {"id": m.id, **m.data}


@app.delete("/v1/memories/{id}")
def delete_memory(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        from .knowledge import lock

        lock(s, actor.tenant)
        m = db.get(s, db.Memory, actor.tenant, id, True)
        if not actor.admin and not project_access(s, actor, m.data.get("project"), "edit"):
            raise Fault("FORBIDDEN", "Project edit permission is required", 403)
        if m.status == "erased":
            return {"status": "erased", "retained_in_historical_snapshots": False}
        from .knowledge import withdraw

        affected = withdraw(s, m)
        return {"status": "withdrawn", "affected_runs": affected, "retained_in_historical_snapshots": True,
                "purge_endpoint": f"/v1/memories/{id}/purge"}


class MemoryPurge(Strict):
    erase_derived_runs: Literal[True]


@app.post("/v1/memories/{id}/purge")
async def purge_memory(id: str, body: MemoryPurge, actor: Actor):
    require_admin(actor)
    from .knowledge_erasure import finish, prepare

    def begin():
        with db.transaction(actor.tenant) as s:
            return prepare(s, actor.tenant, id, actor.actor)
    ids, key = await asyncio.to_thread(begin)
    return await asyncio.to_thread(finish, actor.tenant, ids, key)


class SkillInput(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    description: str = Field(min_length=1, max_length=1000)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    content: str = Field(min_length=1, max_length=40000)
    category: str = "开发"
    source: str = Field(min_length=1)
    license: str = Field(min_length=1)
    resources: dict[str, str | FileEntry] = Field(default_factory=dict)
    source_runs: list[str] = Field(default_factory=list, max_length=100)


@app.post("/v1/skills", status_code=201)
def register_skill(body: SkillInput, actor: Actor):
    require_admin(actor)
    id = body.name + "@" + body.version
    if len(canonical(body.model_dump())) > 1024 * 1024 or len(body.resources) > 100:
        raise Fault("SKILL_LIMIT", "Skill package is limited to 1 MiB and 100 resources", 413)
    for path in body.resources:
        safe_path(settings.data_dir.resolve() / "skill-validation", path)
        from pathlib import PurePosixPath

        if str(PurePosixPath(path)) != path or "\\" in path or path.casefold() in {"skill.md", "forge-manifest.json"}:
            raise Fault("SKILL_RESOURCE", "Use canonical resource paths; SKILL.md and FORGE-MANIFEST.json are reserved", 422)
        if isinstance(body.resources[path], FileEntry) and body.resources[path].mode == "120000":
            raise Fault("SKILL_RESOURCE", "Skill resources cannot be symbolic links", 422)
    if len({path.casefold() for path in body.resources}) != len(body.resources):
        raise Fault("SKILL_RESOURCE", "Resource paths cannot collide on a case-insensitive filesystem", 422)
    with db.transaction(actor.tenant) as s:
        from .knowledge import lock

        lock(s, actor.tenant)
        if s.get(db.SkillVersion, (actor.tenant, id)):
            raise Fault("IMMUTABLE_VERSION", "Skill version already exists")
        for run_id in body.source_runs:
            run = db.get(s, db.Run, actor.tenant, run_id)
            evaluation = s.get(db.Evaluation, (actor.tenant, run.state.get("evaluation_id"))) if run.state.get("evaluation_id") else None
            if evaluation and evaluation.data.get("split") == "held_out":
                raise Fault("HOLDOUT_LEAKAGE", "Candidate packages cannot derive from held-out trajectories", 403)
        s.add(
            db.SkillVersion(
                tenant_id=actor.tenant,
                id=id,
                status="candidate",
                data={**body.model_dump(), "digest": digest({"content": body.content, "resources": body.model_dump()["resources"]})},
            )
        )
    return {"id": id, "status": "candidate"}


class SkillRelease(Strict):
    enabled: bool
    review: str = Field(min_length=5, max_length=2000)
    evaluation_id: str | None = None
    configuration: str | None = None
    rollout_percent: int = Field(100, ge=0, le=100)


@app.get("/v1/skills/{id}/content")
def skill_content(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        skill = db.get(s, db.SkillVersion, actor.tenant, id)
        if skill.status == "erased":
            raise Fault("SKILL_ERASED", "Skill content has been erased", 410)
        return {"content": skill.data.get("content", "")}


@app.get("/v1/skills/{id}/release-options")
def skill_release_options(id: str, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        db.get(s, db.SkillVersion, actor.tenant, id)
        options = []
        for evaluation in db.rows(s, db.Evaluation, actor.tenant):
            if evaluation.data.get("kind") != "paired":
                continue
            report = experiments.report(s, evaluation, actor.tenant)
            for config in report["configurations"][1:]:
                if id not in config["skills"]:
                    continue
                try:
                    evidence = experiments.release_gate(s, actor.tenant, id, evaluation.id, config["name"])
                    reason = None
                except Fault as exc:
                    evidence, reason = None, exc.message
                options.append({"evaluation_id": evaluation.id, "configuration": config["name"],
                                "eligible": evidence is not None, "reason": reason,
                                "evidence_digest": evidence, "split": report["split"],
                                "independent_cases": report["independent_cases"],
                                "repetitions": report["repetitions"],
                                "comparison": report["comparisons"].get(config["name"])})
        return options


@app.post("/v1/skills/{id}/release")
def release_skill(id: str, body: SkillRelease, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        from .knowledge import lock

        lock(s, actor.tenant)
        skill = db.get(s, db.SkillVersion, actor.tenant, id, True)
        if skill.status == "erased":
            raise Fault("KNOWLEDGE_ERASED", "An erased skill cannot be released")
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
            "rollout_percent": body.rollout_percent,
            "release_history": [*skill.data.get("release_history", []), {"at": db.clock(s).isoformat(),
                "enabled": body.enabled, "reviewer": actor.actor, "review": body.review,
                "rollout_percent": body.rollout_percent, "evaluation_digest": evidence}],
        }
        return {"id": id, "status": skill.status}


@app.get("/v1/skills/{id}/package")
def skill_package(id: str, actor: Actor):
    require_admin(actor)
    from .knowledge import export_package

    with db.transaction(actor.tenant) as s:
        skill = db.get(s, db.SkillVersion, actor.tenant, id)
        if skill.status == "erased":
            raise Fault("KNOWLEDGE_ERASED", "An erased package cannot be exported")
        data = export_package({"id": skill.id, **skill.data})
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="skill.zip"'})


class SkillProposal(Strict):
    run_ids: list[str] = Field(min_length=1, max_length=100)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


@app.post("/v1/skills/proposals", status_code=201)
def propose_skill(body: SkillProposal, actor: Actor):
    require_admin(actor)
    from .skill_evolution import propose

    with db.transaction(actor.tenant) as s:
        return propose(s, actor.tenant, body.run_ids, body.name, body.version)


class SkillRollback(Strict):
    target_id: str
    review: str = Field(min_length=5, max_length=2000)


@app.post("/v1/skills/{id}/rollback")
def rollback_skill(id: str, body: SkillRollback, actor: Actor):
    require_admin(actor)
    from .skill_evolution import rollback

    with db.transaction(actor.tenant) as s:
        return rollback(s, actor.tenant, id, body.target_id, actor.actor, body.review)


class WorkspaceSettings(Strict):
    budget: float = Field(ge=0.1, le=100)
    concurrency: int = Field(ge=1, le=4)
    notifications: bool = True
    redact: Literal[True] = True
    expected_revision: int = Field(ge=1)


@app.put("/v1/settings")
def save_settings(body: WorkspaceSettings, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        config = db.get(s, db.PolicyVersion, actor.tenant, "settings", True)
        revision = config.data.get("_revision", 1)
        if body.expected_revision != revision:
            raise Fault("SETTINGS_CONFLICT", "Settings changed; reload before saving your draft")
        config.data = {**body.model_dump(exclude={"expected_revision"}), "_revision": revision + 1}
        return config.data


class RevokeInput(Strict):
    actor: str
    capabilities: list[str]
    project_permissions: dict[str, list[str]] | None = None


@app.post("/v1/authorization")
def revoke(body: RevokeInput, actor: Actor):
    require_admin(actor)
    if not set(body.capabilities) <= service.CAPABILITIES:
        raise Fault("CAPABILITIES", "Unknown capability", 422)
    if body.project_permissions is not None:
        from .auth import PROJECT_OPERATIONS
        if any(not set(ops) <= PROJECT_OPERATIONS for ops in body.project_permissions.values()):
            raise Fault("PROJECT_PERMISSIONS", "Unknown project operation", 422)
    with db.transaction(actor.tenant) as s:
        auth = s.get(db.Authorization, (actor.tenant, body.actor), with_for_update=True)
        if not auth:
            auth = db.Authorization(tenant_id=actor.tenant, id=body.actor, data={"epoch": 0})
            s.add(auth)
        auth.data = {**auth.data, "capabilities": body.capabilities, "epoch": auth.data["epoch"] + 1,
                     "project_permissions": body.project_permissions if body.project_permissions is not None
                     else auth.data.get("project_permissions", {})}
        return auth.data


class ConnectionInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["mcp", "a2a"]
    protocol: Literal["2025-11-25", "2026-07-28", "0.3.0", "1.0"]
    url: str
    tasks_extension: bool = False
    credential_ref: str | None = Field(None, max_length=200)
    callback_key_ref: str | None = Field(None, max_length=200)
    discovery_url: str | None = None
    tools: list[dict] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def contracts(self):
        from .remote_contracts import RemoteTool

        self.tools = [RemoteTool.model_validate(t).model_dump() for t in self.tools]
        if self.tasks_extension and (self.kind != "mcp" or self.protocol != "2026-07-28"):
            raise ValueError("Pinned Tasks extension is supported only by the stateless MCP adapter")
        operations = [t["operation"] for t in self.tools]
        if len(set(operations)) != len(operations):
            raise ValueError("Remote operations must be unique")
        if self.kind == "a2a" and any(t["operation"] != "SendMessage" or t["effect"] == "read" for t in self.tools):
            raise ValueError("A2A SendMessage is an external effect; other operations are control/query adapters")
        return self


@app.get("/v1/connections")
def connections(actor: Actor):
    with db.transaction(actor.tenant) as s:
        return [
            {"id": c.id, "status": c.status, **c.data}
            for c in db.list_rows(s, db.ToolVersion, actor.tenant)
            if c.data.get("kind") in {"mcp", "a2a"}
        ]


@app.post("/v1/connections", status_code=201)
async def connect(body: ConnectionInput, actor: Actor):
    require_admin(actor)
    if (body.kind == "a2a") != (body.protocol in {"0.3.0", "1.0"}):
        raise Fault("PROTOCOL", "Protocol does not match adapter", 422)
    await validate_url(body.url)
    def register_connection():
        with db.transaction(actor.tenant) as s:
            c = db.ToolVersion(tenant_id=actor.tenant, status="pending",
                               data={**body.model_dump(), "tenant_id": actor.tenant})
            s.add(c)
            s.flush()
            return {"id": c.id, "status": c.status, **c.data}
    return await asyncio.to_thread(register_connection)


@app.post("/v1/connections/{id}/discover")
async def discover_connection(id: str, actor: Actor):
    from .remote import discover

    require_admin(actor)
    def load():
        with db.transaction(actor.tenant) as s:
            value = db.get(s, db.ToolVersion, actor.tenant, id)
            return value.data, digest(value.data)
    data, before = await asyncio.to_thread(load)
    result = await discover(data)
    if len(canonical(result)) > 1024 * 1024:
        raise Fault("REMOTE_LIMIT", "Discovery evidence exceeds 1 MiB")
    if data["kind"] == "mcp":
        advertised = {t["name"]: t for t in result["tools"]}
        for tool in data["tools"]:
            found = advertised.get(tool["operation"])
            if not found or digest(found["inputSchema"]) != digest(tool["input_schema"]):
                raise Fault("REMOTE_SCHEMA_DRIFT", "Advertised tool/schema differs from the administrator-reviewed contract")
        reviewed = {t["operation"]: t for t in data["tools"]}
        for tool in data["tools"]:
            if tool.get("reconcile_operation"):
                query = reviewed.get(tool["reconcile_operation"])
                if not query or query["effect"] != "read" or tool["reconcile_key_field"] not in query["input_schema"].get("properties", {}):
                    raise Fault("REMOTE_RECONCILER", "Reconciler must be a reviewed, discovered read tool with a lookup key")
    def commit():
        with db.transaction(actor.tenant) as s:
            value = db.get(s, db.ToolVersion, actor.tenant, id, True)
            if digest(value.data) != before:
                raise Fault("CONNECTION_CHANGED", "Connection changed during discovery")
            value.data = {**data, "negotiated": {"digest": digest(result), "evidence": result,
                                                "time": db.clock(s).isoformat(), "reviewer": actor.actor}}
            value.status = "active"
            return {"id": id, "status": value.status, **value.data}
    return await asyncio.to_thread(commit)


@app.post("/v1/connections/{id}/disable")
def disable_connection(id: str, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        value = db.get(s, db.ToolVersion, actor.tenant, id, True)
        value.status = "disabled"
        return {"id": id, "status": value.status}


@app.post("/v1/actions/{action_id}/reconcile-query")
async def reconcile_query(action_id: str, body: Control, actor: Actor):
    from . import remote, remote_contracts, resources

    require_admin(actor)
    def prepare_query():
        with db.transaction(actor.tenant) as s:
            action = db.get(s, db.Action, actor.tenant, action_id)
            run = db.get(s, db.Run, actor.tenant, action.run_id)
            if run.version != body.expected_version or action.status != "UNKNOWN":
                raise Fault("RECONCILE_CONFLICT", "Unknown action and current version required")
            contract = action.args.get("contract", {})
            if not contract.get("reconcile_operation"):
                raise Fault("REMOTE_RECONCILER", "This tool has no reviewed reconciliation query")
            connection = remote_contracts.current(s, run, action.args["connection_id"])
            service.authorization(s, run, "external.write")
            query = next(t for t in connection["tools"] if t["operation"] == contract["reconcile_operation"])
            arguments = {contract["reconcile_key_field"]: action.args["business_key"]}
            remote_contracts.validate_arguments(query["input_schema"], arguments)
            return run.id, connection, query, arguments
    run_id, connection, query, arguments = await asyncio.to_thread(prepare_query)
    operation = query["operation"]
    value = await remote.dispatch_remote(actor.tenant, run_id, uid(), {"connection": connection, "operation": operation,
                                                                     "arguments": arguments, "contract": query})
    def record_query():
        with db.transaction(actor.tenant) as s:
            run = db.get(s, db.Run, actor.tenant, run_id, True)
            action = db.get(s, db.Action, actor.tenant, action_id, True)
            if run.version != body.expected_version or action.status != "UNKNOWN":
                raise Fault("RECONCILE_CONFLICT", "Action changed while the evidence query was running")
            ref = resources.put_reviewed(s, run, value)
            action.receipt = {**(action.receipt or {}), "reconciliation_query": {"ref": ref, "operation": operation,
                                                                                 "trust": "untrusted_remote_output"}}
            db.emit(s, run, "REMOTE_RECONCILIATION_QUERIED", body.reason, action_id=action_id, evidence_ref=ref)
            return {"ref": ref, "result": value, "requires_review": True, "state_version": run.version}
    return await asyncio.to_thread(record_query)


class RemoteArtifactInput(Control):
    index: int = Field(ge=0, le=100)
    expected_digest: str | None = Field(None, pattern=r"^sha256:[a-f0-9]{64}$")


@app.post("/v1/actions/{action_id}/artifacts", status_code=201)
async def download_remote_artifact(action_id: str, body: RemoteArtifactInput, actor: Actor):
    from . import remote, resources

    def prepare_download():
        with db.transaction(actor.tenant) as s:
            action = db.get(s, db.Action, actor.tenant, action_id)
            run = db.get(s, db.Run, actor.tenant, action.run_id)
            service.authorization(s, run, "external.read")
            links = remote.artifact_links(action.receipt or {})
            if run.version != body.expected_version or run.state.get("knowledge_erased") or run.lease_owner:
                raise Fault("VERSION_CONFLICT", "Task changed before artifact capture")
            if body.index >= len(links):
                raise Fault("REMOTE_ARTIFACT", "Artifact index is not present in the stored response", 422)
            return run.id, action.args["connection"], links[body.index]
    run_id, connection, url = await asyncio.to_thread(prepare_download)
    data = await remote.get_document(connection, url)
    if body.expected_digest and digest(data) != body.expected_digest:
        raise Fault("REMOTE_ARTIFACT_DIGEST", "Downloaded artifact differs from the reviewed checksum")
    def record_download():
        with db.transaction(actor.tenant) as s:
            run = db.get(s, db.Run, actor.tenant, run_id, True)
            if run.version != body.expected_version or run.state.get("knowledge_erased") or run.lease_owner:
                raise Fault("VERSION_CONFLICT", "Task changed during artifact capture")
            ref = resources.put_reviewed(s, run, data)
            artifact = db.Artifact(tenant_id=actor.tenant, run_id=run_id, kind="remote", ref=ref,
                                   name="remote-" + ref["digest"][7:23], verified=False,
                                   version=run.state["artifact_version"])
            s.add(artifact)
            s.flush()
            db.emit(s, run, "REMOTE_ARTIFACT_CAPTURED", body.reason, artifact_id=artifact.id, digest=ref["digest"],
                    source_url=url, action_id=action_id, trust="untrusted_remote_output")
            return {"id": artifact.id, "verified": False, "ref": ref, "trust": "untrusted_remote_output"}
    return await asyncio.to_thread(record_download)


class RemoteInput(Control):
    connection_id: str
    operation: str
    arguments: dict
    idempotency_key: str = Field(min_length=1, max_length=200)


@app.post("/v1/runs/{id}/remote-actions", status_code=202)
def remote_action(id: str, body: RemoteInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        logical_key = "remote-api:" + digest(body.idempotency_key)[7:]
        prior = next((a for a in db.rows(s, db.Action, actor.tenant, run_id=id) if a.logical_key == logical_key), None)
        if prior:
            args = {**prior.args["arguments"]}
            args.pop(prior.args.get("contract", {}).get("idempotency_field"), None)
            if (prior.args["connection_id"] != body.connection_id or prior.args["operation"] != body.operation or digest(args) != digest(body.arguments)):
                raise Fault("IDEMPOTENCY_CONFLICT", "Remote key was used with a different effect")
            return presenters.action_view(s, prior)
        if r.version != body.expected_version or r.status in TERMINAL or r.cancel_requested or r.lease_owner:
            raise Fault("VERSION_CONFLICT", "Task must be idle at the reviewed version")
        a = prepare_remote(s, r, body.connection_id, body.operation, body.arguments, logical_key)
        return presenters.action_view(s, a)


@app.post("/v1/callbacks/{provider}")
async def callback(
    provider: str,
    request: Request,
    x_tenant_id: Annotated[str, Header()],
    x_message_id: Annotated[str, Header()],
    x_timestamp: Annotated[int, Header()],
    x_signature: Annotated[str, Header()],
):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 65536:
            raise Fault("CALLBACK_LIMIT", "Callback payload exceeds 64 KiB", 413)
    body = bytes(chunks)
    def receive_callback():
        with db.transaction(x_tenant_id) as s:
            return accept_callback(s, x_tenant_id, provider, x_message_id, x_timestamp, body, x_signature)
    return await asyncio.to_thread(receive_callback)


@app.post("/v1/examples/smoke", status_code=202)
def smoke(actor: Actor, idempotency_key: Annotated[str, Header()]):
    with db.transaction(actor.tenant) as s:
        if not project_access(s, actor, "runtime-lab", "create"):
            raise Fault("FORBIDDEN", "Runtime Lab create permission is required", 403)
        spec = CreateRun(
            project_id="runtime-lab",
            title="验证 Runtime 端到端流程",
            model="fixture",
            task=Task(
                goal="修复 add 函数，使其返回两个输入之和", allowed_paths=["src"], acceptance_profile="addition@1"
            ),
        )
        return presenters.run_view(s, service.create_run(s, actor.tenant, actor.actor, spec, idempotency_key, admin=actor.admin))


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
        return [{"id": e.id, **({"split": "held_out", "digest": e.data["digest"], "status": e.status,
                                "case_count": len(e.data["cases"])} if e.data.get("split") == "held_out" else e.data)}
                for e in db.list_rows(s, db.EvaluationDataset, actor.tenant)]


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
            r = service.create_run(s, actor.tenant, actor.actor, spec, f"eval:{e.id}:{i}", admin=True)
            ids.append(r.id)
        e.data = {**e.data, "runs": ids}
        return {"id": e.id, "status": e.status, **e.data}


def evaluation_view(s, e, tenant):
    if e.status == "erased":
        return {"id": e.id, "status": "erased", "limitations": "Derived evidence was erased."}
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
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        return [evaluation_view(s, e, actor.tenant) for e in db.list_rows(s, db.Evaluation, actor.tenant)]


@app.get("/v1/evaluations/{id}")
def evaluation(id: str, actor: Actor):
    require_admin(actor)
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
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        runs = s.execute(select(func.count(), func.count().filter(db.Run.status == "SUCCEEDED"),
            func.count().filter(db.Run.status == "WAITING"),
            func.coalesce(func.sum(cast(db.Run.state["tokens"].as_string(), Integer)), 0)).where(db.Run.tenant_id == actor.tenant)).one()
        actions = s.execute(select(func.count(), func.count().filter(db.Action.status == "UNKNOWN")).where(db.Action.tenant_id == actor.tenant)).one()
        accounts = s.execute(select(func.coalesce(func.sum(db.BudgetAccount.spent), 0),
            func.coalesce(func.sum(db.BudgetAccount.reserved), 0)).where(db.BudgetAccount.tenant_id == actor.tenant)).one()
        return {
            "runs": runs[0], "succeeded": runs[1], "waiting": runs[2], "tokens": runs[3],
            "actions": actions[0], "unknown_actions": actions[1],
            "cost_usd": float(accounts[0]) / 1e6, "reserved_usd": float(accounts[1]) / 1e6,
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


@app.get("/v1/operations/objects/references")
def object_references(actor: Actor):
    require_admin(actor)
    from .maintenance import RETENTION, reference_graph

    with db.transaction(actor.tenant) as s:
        return {"policy": RETENTION, "references": reference_graph(s, actor.tenant)}


@app.post("/v1/operations/workspaces/gc")
def collect_workspaces(body: GarbageCollection, actor: Actor):
    require_admin(actor)
    from .maintenance import clean_workspaces

    with db.transaction(actor.tenant) as s:
        return clean_workspaces(s, actor.tenant, body.apply, body.min_age_hours)


@app.post("/v1/operations/runs/{id}/purge")
def purge_run(id: str, actor: Actor):
    require_admin(actor)
    from .knowledge_erasure import finish, prepare

    with db.transaction(actor.tenant) as s:
        ids, key = prepare(s, actor.tenant, None, actor.actor, run_id=id)
    return finish(actor.tenant, ids, key)


class MaintenanceRequest(GarbageCollection):
    kind: Literal["objects", "workspaces", "containers"]
    idempotency_key: str = Field(min_length=1, max_length=200)


@app.post("/v1/operations/jobs")
def create_maintenance(body: MaintenanceRequest, actor: Actor):
    require_admin(actor)
    from .maintenance_jobs import enqueue

    payload = {"apply": body.apply}
    if body.kind != "containers":
        payload["min_age_hours"] = body.min_age_hours
    with db.transaction(actor.tenant) as s:
        job = enqueue(s, actor.tenant, body.kind, payload, body.idempotency_key)
        return {"id": job.id, "status": job.status, "data": job.data}


@app.get("/v1/operations/jobs")
def maintenance_jobs(actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        return [{"id": job.id, "status": job.status, "data": job.data}
                for job in db.list_rows(s, db.PolicyVersion, actor.tenant) if job.data.get("kind") == "maintenance_job"]


@app.post("/v1/operations/jobs/{id}/retry")
def retry_maintenance(id: str, actor: Actor):
    require_admin(actor)
    with db.transaction(actor.tenant) as s:
        job = db.get(s, db.PolicyVersion, actor.tenant, id, True)
        if job.data.get("kind") != "maintenance_job" or job.status != "dead_letter":
            raise Fault("MAINTENANCE_RETRY", "Only dead-letter maintenance jobs can be retried")
        job.status = "queued"
        job.data = {**job.data, "attempts": 0, "available_at": db.clock(s).isoformat(),
                    "history": [*job.data["history"], {"at": db.clock(s).isoformat(), "reviewer": actor.actor, "status": "retry"}]}
        return {"id": job.id, "status": job.status}


@app.get("/metrics")
def prometheus(actor: Actor):
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    values = metrics(actor)
    return Response(
        generate_latest() + "".join(f"# TYPE forge_{k} gauge\nforge_{k} {v}\n" for k, v in values.items()).encode(),
        media_type=CONTENT_TYPE_LATEST,
    )
