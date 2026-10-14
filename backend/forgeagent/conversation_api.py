"""Conversation messages, follow-up and reviewed execution admission."""

import json
from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import Field

from . import db, presenters, service
from .api_dependencies import Actor
from .auth import require_run_access
from .contracts import ConversationView, RunView
from .domain import Control, CreateRun, Fault, digest
from .storage import objects

router = APIRouter()


class UserInput(Control):
    content: str = Field(min_length=1, max_length=10000)


@router.post("/v1/runs/{id}/input")
def user_input(id: str, body: UserInput, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        if r.version != body.expected_version or r.status != "PAUSED":
            raise Fault("INPUT_CONFLICT", "Input requires the current paused task version")
        if r.cancel_requested:
            raise Fault("INPUT_CONFLICT", "A cancelling task cannot accept new input")
        from .recovery import invalidate

        invalidate(s, r)
        r.state = {**r.state, "input": body.content, "input_required": False, "completion": None,
                   "verification": None, "input_revision": r.state.get("input_revision", 0) + 1,
                   "artifact_version": r.state["artifact_version"] + 1}
        for artifact in db.rows(s, db.Artifact, actor.tenant, run_id=id):
            artifact.verified = False
        db.emit(s, r, "USER_INPUT", body.content)
        service.append_message(s, r, "user", body.content)
        return presenters.run_view(s, r)


@router.get("/v1/runs/{id}/conversation", response_model=ConversationView)
def conversation(id: str, actor: Actor):
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id)
        return {"messages": [{k: v for k, v in m.items() if k != "key"} for m in r.state.get("dialogue", [])],
                "initial": r.state["task"]["goal"], "mode": r.state["task"].get("mode", "execute"),
                "plan_ready": bool(r.state.get("plan_ready")), "execution_id": r.state.get("execution_id"),
                "version": r.version, "plan_digest": service.discussion_plan_digest(r)}


@router.post("/v1/runs/{id}/messages", response_model=RunView)
def conversation_message(id: str, body: UserInput, actor: Actor, idempotency_key: Annotated[str, Header()]):
    if not idempotency_key or len(idempotency_key) > 200:
        raise Fault("IDEMPOTENCY_REQUIRED", "A bounded message key is required", 422)
    key = digest({"actor": actor.actor, "key": idempotency_key})
    with db.transaction(actor.tenant) as s:
        r = db.get(s, db.Run, actor.tenant, id, True)
        previous = next((m for m in r.state.get("dialogue", []) if m.get("key") == key), None)
        if previous:
            if previous["content"] != body.content:
                raise Fault("IDEMPOTENCY_CONFLICT", "Message key was already used with different content")
            return presenters.run_view(s, r)
        if r.version != body.expected_version or r.status != "PAUSED" or r.cancel_requested:
            raise Fault("INPUT_CONFLICT", "Reply requires the current paused task; pause running work first")
        if r.state.get("execution_id"):
            raise Fault("DISCUSSION_CLOSED", "This plan has started; send follow-up input to its execution task")
        service.require_settled_usage(s, r)
        from .recovery import invalidate

        invalidate(s, r)
        r.state = {**r.state, "input": body.content, "input_required": False, "completion": None,
                   "plan_ready": False, "verification": None, "input_revision": r.state.get("input_revision", 0) + 1,
                   "artifact_version": r.state["artifact_version"] + 1}
        for artifact in db.rows(s, db.Artifact, actor.tenant, run_id=id):
            artifact.verified = False
        service.append_message(s, r, "user", body.content, key)
        service.control(s, actor.tenant, id, "resume", r.version, "用户发送对话消息")
        return presenters.run_view(s, r)


class FollowupDiscussion(Control):
    spec: CreateRun


class ConfirmExecution(FollowupDiscussion):
    plan_digest: str = Field(min_length=1, max_length=100)


@router.post("/v1/runs/{id}/followup", status_code=202, response_model=RunView)
def followup_discussion(id: str, body: FollowupDiscussion, actor: Actor, idempotency_key: Annotated[str, Header()]):
    from .knowledge import lock
    from .resources import put_reviewed

    with db.transaction(actor.tenant) as s:
        service.lock_request_key(s, actor.tenant, actor.actor, idempotency_key)
        lock(s, actor.tenant)
        old = db.get(s, db.Run, actor.tenant, id, True)
        if old.state.get("knowledge_erased"):
            raise Fault("KNOWLEDGE_ERASED", "Erased history cannot be restored")
        if body.spec.project_id != old.project_id or body.spec.task.mode != "discuss":
            raise Fault("DISCUSSION_SCOPE", "Follow-up must discuss the same project in read-only mode", 422)
        r = service.create_run(s, actor.tenant, actor.actor, body.spec, idempotency_key, admin=actor.admin, request_context=f"followup:{id}")
        if r.state.get("source_run_id") == id:
            require_run_access(s, actor, r)
            return presenters.run_view(s, r)
        if r.state.get("source_run_id") or r.status != "QUEUED":
            raise Fault("IDEMPOTENCY_CONFLICT", "This key belongs to another conversation")
        if old.version != body.expected_version or old.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise Fault("FOLLOWUP_CONFLICT", "Follow-up requires the current finished task")
        service.require_finalizable(s, old)
        content = json.loads(objects.get(actor.tenant, old.state["workspace_ref"]))
        r.state = {**r.state, "source_run_id": id, "workspace_ref": put_reviewed(s, r, content),
                   "workspace_digest": digest(content)}
        db.emit(s, r, "FOLLOWUP_CREATED", "基于已保留的工作区继续只读讨论", source_run_id=id)
        service.checkpoint(s, r)
        return presenters.run_view(s, r)


@router.post("/v1/runs/{id}/execute", status_code=202, response_model=RunView)
def execute_plan(id: str, body: ConfirmExecution, actor: Actor, idempotency_key: Annotated[str, Header()]):
    from .knowledge import lock

    with db.transaction(actor.tenant) as s:
        service.lock_request_key(s, actor.tenant, actor.actor, idempotency_key)
        lock(s, actor.tenant)
        r = db.get(s, db.Run, actor.tenant, id, True)
        requested = digest(body.spec.model_dump(mode="json"))
        if r.state.get("execution_id"):
            if (requested != r.state.get("execution_request_digest")
                or body.plan_digest != r.state.get("execution_plan_digest")):
                raise Fault("PLAN_ALREADY_STARTED", "This plan already started with different execution parameters")
            execution = db.get(s, db.Run, actor.tenant, r.state["execution_id"])
            require_run_access(s, actor, execution)
            return presenters.run_view(s, execution)
        if (r.version != body.expected_version or body.plan_digest != service.discussion_plan_digest(r)
            or r.status != "PAUSED" or r.cancel_requested
            or r.state["task"].get("mode") != "discuss" or not r.state.get("plan_ready")):
            raise Fault("PLAN_CONFLICT", "Confirm the current completed discussion plan before execution")
        if body.spec.project_id != r.project_id or body.spec.task.mode != "execute":
            raise Fault("PLAN_SCOPE", "Execution must use the discussed project", 422)
        service.require_finalizable(s, r)
        execution = service.create_run(s, actor.tenant, actor.actor, body.spec, idempotency_key, admin=actor.admin, request_context=f"execute:{id}")
        if execution.state.get("source_discussion_id") not in {None, id} or execution.status != "QUEUED":
            raise Fault("IDEMPOTENCY_CONFLICT", "This key belongs to another task")
        if r.state.get("source_run_id"):
            from .resources import put_reviewed

            content = json.loads(objects.get(actor.tenant, r.state["workspace_ref"]))
            execution.state = {**execution.state, "workspace_ref": put_reviewed(s, execution, content),
                               "workspace_digest": digest(content)}
        # The confirmed history is frozen into the new run; the discussion never gains write authority.
        execution.state = {**execution.state, "source_discussion_id": id, "dialogue": r.state.get("dialogue", []),
                           "confirmed_plan_digest": body.plan_digest, "confirmed_plan_version": body.expected_version}
        db.emit(s, execution, "PLAN_CONFIRMED", "用户确认方案，创建独立执行任务", discussion_id=id,
                plan_digest=body.plan_digest, reviewed_version=body.expected_version)
        service.checkpoint(s, execution)
        r.state = {**r.state, "execution_id": execution.id, "execution_request_digest": requested,
                   "execution_plan_digest": body.plan_digest,
                   "input_required": False, "reason": "方案已确认，执行任务已创建"}
        db.emit(s, r, "EXECUTION_CREATED", "方案已确认", execution_id=execution.id)
        return presenters.run_view(s, execution)


