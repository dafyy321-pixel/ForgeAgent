import asyncio
import json
import logging
import shlex
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import cast

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from . import billing, db, models, observation, progress, resources, service
from .config import settings
from .context import compile_context
from .domain import TERMINAL, Fault, canonical, digest, uid
from .sandbox import files, sandbox
from .state_types import ModelReceipt, RunState
from .storage import objects
from .telemetry import observed, tracer
from .verification import verify
from .workspace import clear_tree, write_tree

log = logging.getLogger("forge.worker")


def ordered_actions(actions):
    def order(action):
        parts = action.logical_key.split(":")
        ordinal = tuple(map(int, parts)) if len(parts) == 2 and all(part.isdigit() for part in parts) else (10 ** 9, 0)
        return action.created_at, ordinal, action.id
    return sorted(actions, key=order)


class Worker:
    def __init__(self, owner=None, model=None, target_run=None):
        self.owner = owner or uid()
        self.model = model or models.generate
        self.stopping = False
        self.target_run = target_run
        self.provider_slots = asyncio.Semaphore(settings.provider_concurrency)

    @observed("runtime.claim_recover")
    def claim(self, tenant):
        with db.transaction(tenant) as s:
            time = db.clock(s)
            # Per-tenant scheduling quota is serialized. No network call is inside this transaction.
            s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": "claim:" + tenant})
            config = s.get(db.PolicyVersion, (tenant, "settings"))
            limit = config.data.get("concurrency", 2) if config else 2
            running = len(
                list(s.scalars(select(db.Run.id).where(db.Run.tenant_id == tenant, db.Run.lease_until > time)))
            )
            if running >= limit:
                return None
            sibling = aliased(db.Run)
            root = aliased(db.Run)
            root_active = (select(func.count()).select_from(sibling).where(
                sibling.tenant_id == tenant, sibling.root_id == db.Run.root_id, sibling.lease_until > time
            ).correlate(db.Run).scalar_subquery())
            r = s.scalar(
                select(db.Run)
                .join(db.Job, and_(db.Job.tenant_id == db.Run.tenant_id, db.Job.run_id == db.Run.id))
                .join(root, and_(root.tenant_id == db.Run.tenant_id, root.id == db.Run.root_id))
                .where(
                    db.Run.tenant_id == tenant,
                    db.Run.status.in_(["QUEUED", "ACTIVE", "CANCELLING", "WAITING"]),
                    db.Run.available_at <= time,
                    or_(db.Run.lease_until.is_(None), db.Run.lease_until <= time),
                    root_active < func.coalesce(root.state["budget"]["max_concurrent_runs"].as_integer(), 2),
                    db.Run.id == self.target_run if self.target_run else or_(
                        db.Run.state["executor_digest"].as_string() == digest(service.implementation_bindings()),
                        db.Run.state["executor_digest"].as_string().is_(None), db.Run.cancel_requested,
                    ),
                )
                .order_by(db.Run.available_at, db.Run.created_at)
                .with_for_update(of=db.Run, skip_locked=True)
                .limit(1)
            )
            if not r:
                return None
            if r.epoch:
                from .reducer import rebuild

                recovered = rebuild(s, tenant, r.id)
                if digest(recovered) != digest(db.projection(r)):
                    r.status, r.wait_reason = "PAUSED", None
                    r.state = {**r.state, "reason": "REPLAY_DIVERGENCE: live state differs from committed event history"}
                    db.emit(s, r, "REPLAY_DIVERGENCE", r.state["reason"])
                    db.get(s, db.Job, tenant, r.id).status = "DONE"
                    return None
                r.state = recovered["state"]
            expired_at = r.lease_until.isoformat() if r.lease_owner and r.lease_until else None
            interrupted = False
            r.epoch += 1
            r.lease_owner, r.lease_until = self.owner, time + timedelta(seconds=settings.lease_seconds)
            job = db.get(s, db.Job, tenant, r.id, True)
            job.status, job.data = (
                "LEASED",
                {**job.data, "owner": self.owner, "epoch": r.epoch, "lease_until": r.lease_until.isoformat()},
            )
            for a in db.rows(s, db.Action, tenant, run_id=r.id):
                if a.status in {"DISPATCHED", "RUNNING", "CANCEL_REQUESTED"}:
                    if not (a.receipt and a.receipt.get("remote_task_id")):
                        interrupted = True
                        a.status = "READY" if a.effect_class in {"read", "workspace_write"} else "UNKNOWN"
            for call in db.rows(s, db.ModelCall, tenant, run_id=r.id):
                if call.status == "DISPATCHED":
                    interrupted = True
                    call.status = "UNKNOWN"
                    service.settle(s, r, call.id, None)
                    r.status = "CANCELLING" if r.cancel_requested else "PAUSED"
                    r.state = {
                        **r.state,
                        "reason": "Interrupted model request has unknown usage; reconcile budget before resuming",
                    }
            if r.status == "QUEUED":
                r.status = "ACTIVE"
            db.emit(s, r, "LEASE_CLAIMED", "Worker acquired exclusive decision lease", recovered=bool(expired_at or interrupted),
                    recovery_version=2, expired_at=expired_at)
            return r.id, r.epoch

    def renew(self, tenant, id, epoch):
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            r.lease_until = db.clock(s) + timedelta(seconds=settings.lease_seconds)

    async def heartbeat(self, tenant, id, epoch):
        while True:
            await asyncio.sleep(max(1, settings.lease_seconds / 3))
            await asyncio.to_thread(self.renew, tenant, id, epoch)

    def pause(self, tenant, id, epoch, reason):
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            if r.status in TERMINAL:
                return
            for call in db.rows(s, db.ModelCall, tenant, run_id=id):
                if call.status == "DISPATCHED":
                    call.status = "UNKNOWN"
                    service.settle(s, r, call.id, None)
            for action in db.rows(s, db.Action, tenant, run_id=id):
                if action.status == "DISPATCHED":
                    action.status = "READY" if action.effect_class in {"read", "workspace_write"} else "UNKNOWN"
            r.status, r.wait_reason = ("CANCELLING", "RECONCILIATION") if r.cancel_requested else ("PAUSED", None)
            if r.cancel_requested:
                db.schedule(s, r, 30)
            r.state = {**r.state, "reason": reason}
            progress.failed(s, r, reason.split(":", 1)[0] if ":" in reason else "EXECUTION_FAILED", "worker")
            db.emit(s, r, "RUN_PAUSED", reason)
            service.checkpoint(s, r)
    async def once(self, tenant):
        lease = await asyncio.to_thread(self.claim, tenant)
        if not lease:
            return False
        id, epoch = lease
        beat = asyncio.create_task(self.heartbeat(tenant, id, epoch))

        async def traced():
            with tracer.start_as_current_span(
                "run.advance", attributes={"forge.run_id": id, "forge.lease_epoch": epoch},
                record_exception=False, set_status_on_exception=False,
            ):
                await self.advance(tenant, id, epoch)

        task = asyncio.create_task(traced())
        try:
            done, _ = await asyncio.wait([beat, task], return_when=asyncio.FIRST_COMPLETED)
            if beat in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                beat.result()
            else:
                task.result()
        except Fault as exc:
            if exc.code != "STALE_LEASE":
                await asyncio.to_thread(self.pause, tenant, id, epoch, f"{exc.code}: {exc.message}")
        except Exception as exc:
            log.error("worker operation failed: %s", type(exc).__name__, extra={"run_id": id})
            try:
                await asyncio.to_thread(self.pause, tenant, id, epoch, f"Execution stopped: {type(exc).__name__}")
            except Exception:
                log.error("Unable to persist failure; lease recovery will reconcile the operation")
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
            await asyncio.to_thread(self.release, tenant, id, epoch)
        return True

    def release(self, tenant, id, epoch):
        with db.transaction(tenant) as s:
            r = db.get(s, db.Run, tenant, id, True)
            if r.epoch == epoch and r.lease_owner == self.owner:
                r.lease_owner = r.lease_until = None
                job = db.get(s, db.Job, tenant, id)
                if r.status in {"ACTIVE", "QUEUED"}:
                    db.schedule(s, r)  # Put a completed decision quantum behind other ready runs.
                else:
                    job.status = "READY" if r.status in {"WAITING", "CANCELLING"} else "DONE"
                if r.status in TERMINAL:
                    service.release_child_allocation(s, r)

    async def advance(self, tenant, id, epoch):
        def pending_remote():
            with db.transaction(tenant) as s:
                run = db.fence(s, tenant, id, self.owner, epoch)
                from .remote import consume_inbox

                consume_inbox(s, run)
                remote_pending = [
                    a.id
                    for a in db.rows(s, db.Action, tenant, run_id=id)
                    if a.status == "RUNNING" and a.receipt and a.receipt.get("remote_task_id")
                ]
            return remote_pending
        remote_pending = await asyncio.to_thread(pending_remote)
        if remote_pending:
            from .remote import poll_remote

            for action_id in remote_pending:
                await poll_remote(tenant, id, action_id, self.owner, epoch)
            return
        def prepare_advance():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                from .knowledge import refresh

                refresh(s, r)
                state = r.state
                if r.status == "PAUSED":
                    return
                if state.get("deadline") and datetime.fromisoformat(state["deadline"]) <= db.clock(s) and not r.cancel_requested:
                    service.control(s, tenant, id, "cancel", r.version, "Child deadline reached")
                    state = r.state
                actions = ordered_actions(db.rows(s, db.Action, tenant, run_id=id))
                unknown = [a for a in actions if a.status == "UNKNOWN"]
                children = db.rows(s, db.Run, tenant, parent_id=id)
                if unknown:
                    r.status, r.wait_reason = "CANCELLING" if r.cancel_requested else "WAITING", "RECONCILIATION"
                    r.state = {**state, "reason": "External effect requires reconciliation"}
                    db.emit(s, r, "RECONCILIATION_REQUIRED", "Unknown effects block further execution")
                    db.schedule(s, r, 30)
                    return
                if r.cancel_requested:
                    try:
                        service.require_settled_usage(s, r)
                    except Fault as exc:
                        r.status, r.wait_reason = "CANCELLING", "RECONCILIATION"
                        r.state = {**state, "reason": exc.message}
                        db.emit(s, r, "CANCELLATION_WAITING", "Cancellation awaits billing reconciliation")
                        db.schedule(s, r, 30)
                        return
                    if any(c.status not in TERMINAL for c in children):
                        r.status, r.wait_reason = "CANCELLING", "CHILD_RUN"
                        db.emit(s, r, "CANCELLATION_WAITING", "Cancellation awaits child settlement")
                        db.schedule(s, r, 2)
                        return
                    for a in actions:
                        if a.status in {"READY", "PREPARED", "WAITING_APPROVAL"}:
                            a.status = "CANCELLED"
                    r.status, r.wait_reason = "CANCELLED", None
                    db.emit(s, r, "RUN_CANCELLED", "All in-flight effects settled; no new actions dispatched")
                    return
                service.authorization(s, r)
                if state["semantic"]["tools_digest"] != digest(service.TOOLS):
                    raise Fault("SEMANTIC_DRIFT", "Pinned tool catalog differs from this worker")
                if state["semantic"].get("implementation") != service.implementation_bindings():
                    raise Fault("SEMANTIC_DRIFT", "Pinned runtime implementation changed; fork with current version")
                if r.pause_requested:
                    r.status, r.wait_reason = "PAUSED", None
                    db.emit(s, r, "RUN_PAUSED", "Paused at a durable boundary")
                    service.checkpoint(s, r)
                    return
                pending = [a for a in actions if a.status == "WAITING_APPROVAL"]
                if pending:
                    for a in pending:
                        approvals = [p for p in db.rows(s, db.Approval, tenant, action_id=a.id) if p.decision == "pending"]
                        if not approvals or approvals[-1].expires_at <= db.clock(s):
                            for p in approvals:
                                p.decision = "expired"
                            a.status = "FAILED"
                            a.receipt = {"exit_code": 1, "error": "Approval expired"}
                            r.status, r.wait_reason = "PAUSED", None
                            db.emit(s, r, "APPROVAL_EXPIRED", "Approval expired before dispatch")
                            return
                    r.status, r.wait_reason = "WAITING", "APPROVAL"
                    db.emit(s, r, "APPROVAL_WAITING", "Pending approval blocks execution")
                    db.schedule(s, r, 5)
                    return
                required_pending, required_failed = service.join_children(s, r, children)
                if required_pending:
                    r.status, r.wait_reason = "WAITING", "CHILD_RUN"
                    db.emit(s, r, "CHILD_WAITING", "Required children have not settled")
                    db.schedule(s, r, 2)
                    return
                if required_failed:
                    raise Fault("CHILD_FAILED", "A required child run did not succeed")
                if (db.clock(s) - r.created_at).total_seconds() > state["budget"]["max_wall_seconds"]:
                    raise Fault("WALL_TIME_LIMIT", "Task wall time limit reached")
                root_run = db.get(s, db.Run, tenant, r.root_id)
                if (db.clock(s) - root_run.created_at).total_seconds() > root_run.state["budget"]["max_wall_seconds"]:
                    raise Fault("ROOT_WALL_LIMIT", "Root task wall-clock quota exhausted")
                retry_at = state.get("model_retry_at")
                if retry_at and datetime.fromisoformat(retry_at) > db.clock(s):
                    delay = (datetime.fromisoformat(retry_at) - db.clock(s)).total_seconds()
                    r.status, r.wait_reason = "WAITING", "RETRY_TIMER"
                    db.schedule(s, r, delay)
                    db.emit(s, r, "PROVIDER_RETRY_WAITING", "Respect persisted provider Retry-After deadline")
                    return
                r.status, r.wait_reason = "ACTIVE", None
                ready = [a.id for a in actions if a.status == "READY"]
                for a in actions:
                    if a.status in {"SUCCEEDED", "FAILED"} and not a.consumed:
                        a.consumed = True
                        db.emit(s, r, "ACTION_CONSUMED", "Persisted result consumed", action_id=a.id)
                state = r.state
                received = next((c.id for c in db.rows(s, db.ModelCall, tenant, run_id=id)
                                 if c.data.get("receipt_state") == "received" and c.status != "UNKNOWN"), None)
            return state, ready, received
        prepared = await asyncio.to_thread(prepare_advance)
        if prepared is None:
            return
        state, ready, received = prepared
        if received:
            await self.consume_response(tenant, id, epoch, received)
            return
        content = json.loads(await asyncio.to_thread(objects.get, tenant, state["workspace_ref"]))
        root = await asyncio.to_thread(sandbox.restore, tenant, id, epoch, content, state.get("repository"))
        if ready:
            for action_id in ready:
                await self.dispatch(tenant, id, epoch, action_id, root)
            return
        if state.get("completion"):
            await self.complete(tenant, id, epoch)
            return
        if state["turn"] >= state["budget"]["max_turns"]:
            raise Fault("TURN_LIMIT", "Maximum model turns reached")
        await self.decide(tenant, id, epoch, state)

    async def decide(self, tenant, id, epoch, snapshot):
        async with self.provider_slots:
            from .concurrency import admission, provider_key

            async with admission(provider_key(), settings.provider_concurrency) as admitted:
                if not admitted:
                    def defer():
                        with db.transaction(tenant) as s:
                            r = db.fence(s, tenant, id, self.owner, epoch)
                            r.status, r.wait_reason = "WAITING", "PROVIDER_BACKPRESSURE"
                            db.schedule(s, r, 1)
                            db.emit(s, r, "PROVIDER_BACKPRESSURE", "Provider capacity exhausted; no request reserved or sent")
                    await asyncio.to_thread(defer)
                    return
                await self.decide_with_slot(tenant, id, epoch, snapshot)

    async def decide_with_slot(self, tenant, id, epoch, snapshot):
        def prepare_code_context():
            # Reuse the already fenced snapshot; object reads and syntax parsing
            # happen outside the transaction. Recheck inputs before consumption.
            st = snapshot
            policy = st["semantic"].get("harness", {}).get("code_retrieval", "off")
            binding = digest({k: st.get(k) for k in ("workspace_digest", "task", "input", "input_revision", "semantic", "relevant_paths")})
            if policy == "off":
                return binding, None
            ref, task, extra, relevant = st["workspace_ref"], st["task"], st.get("input", ""), st.get("relevant_paths", [])
            from .code_index import retrieve

            content = json.loads(objects.get(tenant, ref))
            return binding, retrieve(content, task["goal"] + " " + str(extra), policy, relevant)

        code_binding, code_context = await asyncio.to_thread(prepare_code_context)
        def prepare_request():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                from .knowledge import refresh

                refresh(s, r)
                state = r.state
                if code_binding is not None and code_binding != digest({k: state.get(k) for k in (
                    "workspace_digest", "task", "input", "input_revision", "semantic", "relevant_paths"
                )}):
                    return None
                actions = db.rows(s, db.Action, tenant, run_id=id)
                observations = [
                    observation.project(a) for a in actions if a.receipt and (
                        not state.get("knowledge_barrier") or a.receipt.get("observation", {}).get("input_revision", -1) >= state["knowledge_barrier"])
                ]
                observations += [
                    {
                        "child_run_id": c.id,
                        "status": c.status,
                        "verification": c.state.get("verification"),
                        "summary": c.state.get("completion"),
                    }
                    for c in db.rows(s, db.Run, tenant, parent_id=id)
                ]
                messages, manifest = compile_context(
                    state["task"],
                    {**state, "phase": r.phase},
                    observations,
                    state["skills"],
                    state["memories"],
                    settings.context_window,
                    settings.max_output,
                    code_retrieval=code_context,
                )
                call_id = uid()
                reservation = 0 if state["fixture"] else models.estimate_cost(len(canonical(messages)), settings.max_output)
                # Configuration failures happen before creating an uncertain billable request.
                if (
                    not state["fixture"]
                    and self.model == models.generate
                    and (not settings.model_api_key or not settings.model_id)
                ):
                    raise Fault("MODEL_NOT_CONFIGURED", "Configure model credentials and ID, then resume")
                if not state["fixture"] and self.model == models.generate:
                    if (
                        state["semantic"]["model_provider"] != settings.model_provider
                        or state["semantic"]["model_id"] != settings.model_id
                        or state["semantic"].get("model_profile") != service.model_profile()
                    ):
                        raise Fault("MODEL_BINDING_CHANGED", "Explicitly bind the configured model before resuming")
            return state, actions, messages, call_id, manifest, reservation
        prepared = await asyncio.to_thread(prepare_request)
        if prepared is None:
            return
        state, actions, messages, call_id, manifest, reservation = prepared
        request = None
        input_bound = len(canonical(messages)) + 2048
        if not state["fixture"] and self.model == models.generate:
            from .model_protocol import build_request
            from .remote_contracts import catalog

            request = await asyncio.to_thread(build_request, messages, state, catalog(state))
            request["client_request_id"] = call_id
            if request["provider"] == "openai":
                request["payload"]["prompt_cache_key"] = digest({"tenant": tenant, "run": id,
                    "tools": request["payload"].get("tools"), "system": messages[0]["content"]})[7:]
            exchange = state.get("native_exchange")
            if exchange and exchange.get("input_revision", 0) == state.get("input_revision", 0):
                def read_exchange():
                    raw = json.loads(objects.get(tenant, exchange["response_ref"]))
                    metadata = raw.get("_forge")
                    results = {}
                    with db.transaction(tenant) as s:
                        for binding in exchange.get("actions", []):
                            action = db.get(s, db.Action, tenant, binding["action_id"])
                            if action.run_id != id or action.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                                raise Fault("PROTOCOL_RESULT_PENDING", "Native tool result has not settled in this run")
                            results[binding["provider_call_id"]] = json.loads(objects.get(tenant, action.receipt["ref"])) if action.receipt and action.receipt.get("ref") else action.receipt or {"status": action.status}
                    for native in (metadata or {}).get("calls", []):
                        results.setdefault(native["call_id"], {"control": exchange["kind"], "children": state.get("child_results", {}), "input_revision": state.get("input_revision", 0)})
                    return metadata, results
                metadata, results = await asyncio.to_thread(read_exchange)
                from .model_protocol import attach_continuation

                request = attach_continuation(request, metadata, results)
            input_bound, count_source = await models.count_request(request)
            if input_bound + settings.max_output > settings.context_window:
                raise Fault("CONTEXT_OVERFLOW", "Actual provider request including tools exceeds the context window")
            manifest = {**manifest, "provider_input_tokens": input_bound, "provider_count_source": count_source}
            prefix = request["payload"].get("system") or request["payload"].get("input", request["payload"].get("messages"))[0]
            manifest["stable_prefix_digest"] = digest([prefix, request["payload"].get("tools", [])])
            manifest["digest"] = digest({k: v for k, v in manifest.items() if k != "digest"})
            reservation = billing.cost({"input_tokens": input_bound, "output_tokens": settings.max_output},
                                       state["semantic"]["model_profile"], upper=True)
        messages_ref = await asyncio.to_thread(resources.put, tenant, id, request or messages)
        def register_request():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                if r.cancel_requested or r.pause_requested or r.status == "PAUSED":
                    return False
                if (r.state.get("input_revision", 0) != state.get("input_revision", 0)
                    or r.state["workspace_digest"] != state["workspace_digest"]
                    or digest(r.state["semantic"]) != digest(state["semantic"])):
                    return False
                service.reserve(
                    s,
                    r,
                    call_id,
                    reservation,
                    0 if state["fixture"] else input_bound + settings.max_output,
                )
                call = db.ModelCall(
                    tenant_id=tenant,
                    id=call_id,
                    run_id=id,
                    status="DISPATCHED",
                    data={
                        "request_digest": digest(request or messages),
                        "ordinal": state["turn"] + 1,
                        "input_revision": state.get("input_revision", 0),
                        "context_digest": manifest["digest"],
                        "model_id": state["semantic"]["model_id"],
                        "messages_ref": messages_ref,
                        "input_bound": input_bound,
                        "rate_card": state["semantic"].get("model_profile", {}),
                    },
                )
                s.add(call)
                s.add(db.ContextManifest(tenant_id=tenant, run_id=id, data={"turn": state["turn"] + 1, **manifest}))
                r.phase = "DECIDING"
                db.emit(s, r, "MODEL_REQUESTED", "Context and model request persisted", call_id=call_id)
            return True
        if not await asyncio.to_thread(register_request):
            return
        try:
            if state["fixture"]:
                if not actions:
                    current = json.loads(await asyncio.to_thread(objects.get, tenant, state["workspace_ref"]))
                    response = {
                        "kind": "tool_calls",
                        "summary": "Correct addition fixture",
                        "calls": [
                            {
                                "tool": "repo.write",
                                "args": {
                                    "path": "src/calculator.py",
                                    "content": "def add(left, right):\n    return left + right\n",
                                    "expected_digest": digest(current["src/calculator.py"].encode()),
                                },
                            }
                        ],
                    }
                else:
                    response = {
                        "kind": "propose_completion",
                        "summary": "Fixture change ready for independent AST verification",
                    }
                raw_text, usage, raw, provider_id = (
                    json.dumps(response),
                    {"input_tokens": 0, "output_tokens": 0},
                    response,
                    call_id,
                )
            else:
                raw_text, usage, raw, provider_id = (await self.model(messages, state["semantic"]["model_id"], request=request)
                    if request else await self.model(messages, state["semantic"]["model_id"]))
        except Exception as exc:
            def record_failure(exc=exc):
                with db.transaction(tenant) as s:
                    r = db.fence(s, tenant, id, self.owner, epoch)
                    call = db.get(s, db.ModelCall, tenant, call_id)
                    from .model_retry import classify

                    count = r.state.get("model_errors", 0) + 1
                    retry = classify(exc, db.clock(s), count)
                    code = getattr(exc, "status_code", None)
                    known_unbilled = retry["known_unbilled"]
                    call.status = "FAILED" if known_unbilled else "UNKNOWN"
                    call.data = {**call.data, "error_type": type(exc).__name__, "retry": retry,
                                 "provider_request_id": getattr(exc, "request_id", None)}
                    service.settle(s, r, call_id, 0 if known_unbilled else None, 0 if known_unbilled else None)
                    r.state = {**r.state, "model_errors": count}
                    progress.failed(s, r, "MODEL_RATE_LIMIT" if code == 429 else "UNKNOWN_USAGE", "provider")
                    if retry["retryable"] and count <= 2:
                        root = db.get(s, db.Run, tenant, r.root_id)
                        deadline = root.created_at + timedelta(seconds=root.state["budget"]["max_wall_seconds"])
                        retry_at = db.clock(s) + timedelta(seconds=retry["delay_seconds"])
                        if retry_at >= deadline:
                            r.state = {**r.state, "reason": "Provider Retry-After exceeds remaining root wall-clock budget"}
                            db.emit(s, r, "PROVIDER_DELAY_EXCEEDS_BUDGET", r.state["reason"])
                            return False
                        r.state = {**r.state, "model_retry_at": retry_at.isoformat()}
                        r.status, r.wait_reason = "WAITING", "RETRY_TIMER"
                        db.schedule(s, r, retry["delay_seconds"])
                        db.emit(s, r, "MODEL_RETRY_SCHEDULED", "Rate limit; durable bounded retry")
                        return True
                return False
            if await asyncio.to_thread(record_failure):
                return
            raise Fault("MODEL_CALL_FAILED", "Model request failed; unknown usage stays reserved") from exc
        response_ref = await asyncio.to_thread(resources.put, tenant, id, raw)
        try:
            decision = models.parse_decision(raw_text)
            validation_error = None
        except (ValidationError, ValueError) as exc:
            decision, validation_error = None, str(exc)[:1500]
        def record_response():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                call = db.get(s, db.ModelCall, tenant, call_id, True)
                try:
                    actual = billing.cost(usage, call.data["rate_card"]) if usage is not None else None
                    billing_error = None
                except Fault as exc:
                    actual, billing_error = None, exc.code
                protocol_receipt = raw.get("_forge")
                if not isinstance(protocol_receipt, dict):
                    protocol_receipt = {} if protocol_receipt is None else {"end_status": "invalid_response"}
                call.status = "RECEIVED" if actual is not None else "UNKNOWN"
                receipt: ModelReceipt = {"response_ref": response_ref, "usage": usage,
                             "provider_request_id": provider_id, "receipt_state": "received",
                             "decision": decision.model_dump() if decision else None,
                             "validation_error": validation_error, "received_at": db.clock(s).isoformat(),
                             "end_status": protocol_receipt.get("end_status", "completed"),
                             "protocol_receipt": protocol_receipt, "billing_error": billing_error}
                call.data = {**call.data, **receipt}
                service.settle(s, r, call_id, actual, billing.tokens(usage) if usage and actual is not None else None)
                if actual is None:
                    r.status = "CANCELLING" if r.cancel_requested else "PAUSED"
                    r.state = {**r.state, "reason": "Provider omitted usage; reconcile reserved billing before continuing"}
                r.phase = "RESPONSE_RECEIVED"
                db.emit(s, r, "MODEL_RESPONSE_RECEIVED", "Response and usage committed before decision consumption", call_id=call_id)
        await asyncio.to_thread(record_response)
        await self.consume_response(tenant, id, epoch, call_id)

    async def consume_response(self, tenant, id, epoch, call_id):
        await asyncio.to_thread(self.consume_response_sync, tenant, id, epoch, call_id)

    def consume_response_sync(self, tenant, id, epoch, call_id):
        with db.transaction(tenant) as s:
            from .knowledge import lock

            lock(s, tenant)
            r = db.fence(s, tenant, id, self.owner, epoch)
            call = db.get(s, db.ModelCall, tenant, call_id, True)
            if call.data.get("receipt_state") != "received" or call.status == "UNKNOWN":
                return
            if r.pause_requested and not r.cancel_requested:
                return
            state = r.state
            actions = db.rows(s, db.Action, tenant, run_id=id)
            decision = models.parse_decision(json.dumps(call.data["decision"])) if call.data.get("decision") else None
            validation_error = call.data.get("validation_error")
            if decision and len(decision.calls) > 1:
                if not state["semantic"].get("harness", {}).get("action_fusion", True):
                    validation_error = "Action fusion is disabled; propose one call per decision"
                elif any(proposed.tool not in service.TOOLS or service.TOOLS[proposed.tool]["effect"] not in {"read", "workspace_write"}
                         for proposed in decision.calls):
                    validation_error = "Only scoped local operations may share a decision batch; external effects require separate decisions"
            usage = call.data.get("usage")
            call.status = "RECONCILED" if call.status == "RECONCILED" else "SUCCEEDED"
            call.data = {**call.data, "receipt_state": "applied", "applied_at": db.clock(s).isoformat()}
            r.state = {**r.state, "turn": call.data["ordinal"],
                       "tokens": r.state["tokens"] + (call.data.get("reconciled_tokens", 0) if call.status == "RECONCILED"
                                                      else billing.tokens(usage) if usage else 0)}
            if call.data.get("input_revision", 0) != state.get("input_revision", 0):
                call.data = {**call.data, "receipt_state": "superseded_input"}
                db.emit(s, r, "MODEL_RESPONSE_SUPERSEDED", "New input invalidated the persisted decision", call_id=call_id)
                return
            if call.data.get("end_status", "completed") != "completed":
                call.status = "FAILED"
                r.status = "CANCELLING" if r.cancel_requested else "PAUSED"
                r.state = {**r.state, "reason": "MODEL_" + call.data["end_status"].upper() + ": no decision effects applied"}
                db.emit(s, r, "MODEL_ENDED_WITHOUT_DECISION", r.state["reason"], call_id=call_id)
                return
            if not decision and not validation_error:
                validation_error = "Persisted model response has no parsed decision"
            if validation_error:
                count = cast(RunState, r.state).get("format_errors", 0) + 1
                r.state = {**r.state, "format_errors": count, "input": "Invalid decision JSON. " + validation_error}
                progress.failed(s, r, "MODEL_FORMAT_ERROR", "provider")
                if count > 2:
                    r.status = "CANCELLING" if r.cancel_requested else "PAUSED"
                db.emit(s, r, "MODEL_FORMAT_ERROR", "Model response did not satisfy decision schema")
                return
            assert decision is not None
            s.add(
                db.Turn(
                    tenant_id=tenant,
                    run_id=id,
                    data={
                        "ordinal": r.state["turn"],
                        "call_id": call_id,
                        "decision": decision.model_dump(),
                        "context_digest": call.data["context_digest"],
                    },
                )
            )
            db.emit(s, r, "DECISION_COMMITTED", decision.summary, call_id=call_id)
            if r.cancel_requested or r.pause_requested:
                return
            if not progress.decided(s, r):
                return
            if decision.kind == "tool_calls":
                if len(actions) + len(decision.calls) > state["budget"]["max_tool_calls"]:
                    r.status = "PAUSED"
                    db.emit(s, r, "TOOL_LIMIT", "Tool call budget exhausted")
                    return
                fingerprint = digest(
                    {"calls": [{"tool": c.tool, "args": c.args} for c in decision.calls], "workspace": r.state["workspace_digest"]}
                )
                repeats = cast(RunState, r.state).get("repeats", 0) + 1 if r.state.get("fingerprint") == fingerprint else 1
                r.state = {**r.state, "fingerprint": fingerprint, "repeats": repeats}
                if repeats >= 3:
                    r.status = "PAUSED"
                    db.emit(s, r, "NO_PROGRESS", "Three identical decisions without workspace progress")
                    return
                native_actions = []
                for i, proposed in enumerate(decision.calls):
                    try:
                        with s.begin_nested():
                            action = service.prepare_action(s, r, proposed, f"{r.state['turn']}:{i}")
                            if proposed.provider_call_id:
                                native_actions.append({"provider_call_id": proposed.provider_call_id, "action_id": action.id})
                    except Fault as exc:
                        r.status = "PAUSED"
                        r.state = {**r.state, "reason": f"{exc.code}: {exc.message}"}
                        db.emit(s, r, "RESOURCE_LIMIT", exc.message)
                        return
            elif decision.kind == "request_input":
                r.status, r.wait_reason = "PAUSED", None
                r.state = {**r.state, "reason": decision.summary, "input_required": True}
            elif decision.kind == "delegate":
                assert decision.child is not None
                try:
                    with s.begin_nested():
                        child = service.create_run(
                            s, tenant, r.actor, decision.child, f"child:{id}:{r.state['turn']}", parent=r
                        )
                except Fault as exc:
                    r.status = "PAUSED"
                    r.state = {**r.state, "reason": f"{exc.code}: {exc.message}"}
                    db.emit(s, r, "DELEGATION_DENIED", exc.message)
                    return
                r.status, r.wait_reason = ("WAITING", "CHILD_RUN") if decision.child.child_contract.required else ("ACTIVE", None)
                db.emit(s, r, "CHILD_CREATED", decision.summary, child_run_id=child.id)
                db.schedule(s, r, 2 if decision.child.child_contract.required else 0)
            else:
                r.state = {**r.state, "completion": decision.summary}
                r.phase = "VERIFYING"
            if call.data.get("protocol_receipt"):
                r.state = {**r.state, "native_exchange": {"response_ref": call.data["response_ref"],
                    "input_revision": state.get("input_revision", 0), "kind": decision.kind,
                    "actions": native_actions if decision.kind == "tool_calls" else []}}
            db.emit(s, r, "DECISION_APPLIED", "Decision effects committed", decision_kind=decision.kind)

    @observed("tool.dispatch")
    async def dispatch(self, tenant, id, epoch, action_id, root):
        def prepare_dispatch():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                a = db.get(s, db.Action, tenant, action_id, True)
                if a.status != "READY" or r.cancel_requested or r.pause_requested:
                    return
                spec = service.TOOLS.get(a.tool)
                if spec is None:
                    # Remote dispatch has its own bound approval check and persisted attempt.
                    remote = True
                else:
                    remote = False
                    service.authorization(s, r, spec["capability"])
                if remote:
                    from .remote import authorize_remote

                    authorize_remote(s, r, a)
                a.status, a.epoch = "DISPATCHED", epoch
                a.attempt += 1
                attempt_id = uid()
                s.add(
                    db.Attempt(
                        tenant_id=tenant,
                        id=attempt_id,
                        run_id=id,
                        status="DISPATCHED",
                        data={"action_id": a.id, "number": a.attempt, "epoch": epoch},
                    )
                )
                r.phase = "EXECUTING"
                db.emit(s, r, "ACTION_DISPATCHED", f"Dispatch {a.tool}", action_id=a.id)
                args, tool, state = a.args, a.tool, r.state
            return args, tool, state, remote, attempt_id
        prepared = await asyncio.to_thread(prepare_dispatch)
        if prepared is None:
            return
        args, tool, state, remote, attempt_id = prepared
        changed = None
        try:
            # Every action starts from the committed snapshot. A failed write/upload cannot leak
            # an uncommitted filesystem mutation into another action in the same lease.
            committed = await asyncio.to_thread(objects.get, tenant, state["workspace_ref"])
            root = await asyncio.to_thread(sandbox.restore, tenant, id, epoch, json.loads(committed), state.get("repository"))
            if remote:
                from .remote import dispatch_remote

                receipt = await dispatch_remote(tenant, id, action_id, args)
            elif tool != "tests.run":
                def local_tool():
                    changed = None
                    if tool.startswith("repo."):
                        from .repo_tools import execute

                        return execute(root, tool, args, state)
                    elif tool == "skill.read":
                        from .knowledge import read_skill

                        receipt = read_skill(state, args["skill_id"], args.get("path", "SKILL.md"))
                    elif tool == "observation.read":
                        if not state["semantic"].get("harness", {}).get("observation_recall", True):
                            raise Fault("HARNESS_RECALL_DISABLED", "Observation recall is disabled in this task contract")
                        with db.transaction(tenant) as s:
                            source = db.get(s, db.Action, tenant, args["action_id"])
                            if source.run_id != id or not source.receipt or not source.receipt.get("ref"):
                                raise Fault("OBSERVATION_SCOPE", "Observation is not available in this task", 404)
                            if state.get("knowledge_barrier") and source.receipt.get("observation", {}).get("input_revision", -1) < state["knowledge_barrier"]:
                                raise Fault("OBSERVATION_RETRACTED", "This historical observation precedes a knowledge withdrawal")
                            ref = source.receipt["ref"]
                        raw = json.loads(objects.get(tenant, ref))
                        start, count = args.get("line_start", 0), args.get("max_lines", 100)
                        receipt = observation.read(raw, ref, start, count)
                    elif tool == "child.integrate":
                        with db.transaction(tenant) as s:
                            child = db.get(s, db.Run, tenant, args["child_id"])
                            if child.parent_id != id or child.status != "SUCCEEDED":
                                raise Fault("CHILD_NOT_READY", "Only a succeeded direct child can be integrated")
                            child_base = json.loads(objects.get(tenant, child.state["baseline_ref"]))
                            child_files = json.loads(objects.get(tenant, child.state["workspace_ref"]))
                        current = files(root)
                        differences = [p for p in set(child_base) | set(child_files) if child_base.get(p) != child_files.get(p)]
                        for name in differences:
                            if not any(
                                name == p or name.startswith(p.rstrip("/") + "/") for p in state["task"]["allowed_paths"]
                            ):
                                raise Fault("CHILD_SCOPE", "Child patch exceeds parent write scope")
                            if current.get(name) not in (child_base.get(name), child_files.get(name)):
                                raise Fault("MERGE_CONFLICT", f"Parent and child both changed {name}")
                        merged = dict(current)
                        for name in differences:
                            if name not in child_files:
                                merged.pop(name, None)
                            else:
                                merged[name] = child_files[name]
                        clear_tree(root)
                        write_tree(root, merged)
                        changed = files(root)
                        receipt = {"exit_code": 0, "child_id": child.id, "integrated_paths": differences}
                    return receipt, changed
                receipt, changed = await asyncio.to_thread(local_tool)
            elif tool == "tests.run":
                build = state.get("build", {})
                image = await sandbox.image_digest(build.get("dependency_image") or None)
                if state.get("image_digest") and state["image_digest"] != image:
                    raise Fault("SEMANTIC_DRIFT", "Sandbox image changed")
                if not state.get("image_digest"):
                    def bind_image():
                        with db.transaction(tenant) as s:
                            r = db.fence(s, tenant, id, self.owner, epoch)
                            r.state = {**r.state, "image_digest": image}
                            db.emit(s, r, "SANDBOX_IMAGE_BOUND", "Sandbox image content address pinned", image_digest=image)
                    await asyncio.to_thread(bind_image)
                if build.get("ecosystem", "none") != "none":
                    from .workspace import body

                    current = await asyncio.to_thread(files, root)
                    if build["lockfile"] not in current or digest(body(current[build["lockfile"]])) != build["lock_digest"]:
                        raise Fault("DEPENDENCY_LOCK_MISMATCH", "Restore the registered lockfile before running tests")
                if build.get("argv"):
                    combined = ["/bin/sh", "-c", shlex.join(build["argv"]) + " && " + shlex.join(args["argv"])]
                    receipt = await resources.execute(tenant, id, attempt_id + ":build-test", root, combined, image,
                                                      min(300, build.get("timeout", 120) + 120), build=build)
                else:
                    receipt = await resources.execute(tenant, id, attempt_id + ":test", root, args["argv"], image, build=build)
            else:
                raise Fault("TOOL_UNAVAILABLE", "Tool is unavailable")
            action_status = (
                "RUNNING" if receipt.get("pending") else "SUCCEEDED" if receipt.get("exit_code", 0) == 0 else "FAILED"
            )
        except Exception as exc:
            receipt = {"exit_code": 1, "error": str(exc)[:2000], "error_code": getattr(exc, "code", "TOOL_FAILED")}
            changed = None
            action_status = "UNKNOWN" if remote else "FAILED"
        receipt = observation.envelope(receipt, tool, state)
        receipt_ref = await asyncio.to_thread(resources.put, tenant, id, receipt)
        workspace_ref = await asyncio.to_thread(resources.put, tenant, id, changed) if changed is not None and action_status == "SUCCEEDED" else None
        def commit_dispatch():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                a = db.get(s, db.Action, tenant, action_id, True)
                a.status = action_status
                a.receipt = {
                    "ref": receipt_ref,
                    "preview": canonical(receipt).decode()[:4000],
                    "exit_code": receipt.get("exit_code", 0),
                    **({"digest": receipt["digest"]} if "digest" in receipt else {}),
                    "observation": receipt["_observation"],
                }
                if remote:
                    a.receipt = {**a.receipt, **receipt}
                if receipt.get("remote_task_id") and receipt.get("pending"):
                    r.status, r.wait_reason = (("CANCELLING", "TOOL") if r.cancel_requested else
                                               ("PAUSED", None) if r.pause_requested else ("WAITING", "TOOL"))
                    if r.status != "PAUSED":
                        db.schedule(s, r, receipt.get("poll_seconds", 5))
                attempt = db.get(s, db.Attempt, tenant, attempt_id)
                attempt.status, attempt.data = action_status, {**attempt.data, "result_ref": receipt_ref}
                if workspace_ref:
                    r.state = {
                        **r.state,
                        "workspace_ref": workspace_ref,
                        "workspace_digest": digest(changed),
                        "verification": None,
                        "artifact_version": r.state["artifact_version"] + 1,
                    }
                    for artifact in db.rows(s, db.Artifact, tenant, run_id=id):
                        artifact.verified = False
                    service.checkpoint(s, r)
                if receipt.get("child_id") and action_status == "SUCCEEDED":
                    r.state = {**r.state, "integrated_children": sorted(set(
                        cast(RunState, r.state).get("integrated_children", []) + [receipt["child_id"]]
                    ))}
                progress.observed(s, r, tool, receipt, action_status)
                db.emit(s, r, "ACTION_RESULT", f"{tool}: {action_status}", action_id=a.id, observation=a.receipt)
        await asyncio.to_thread(commit_dispatch)

    @observed("runtime.complete")
    async def complete(self, tenant, id, epoch):
        def prepare_verification():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                optional_live = [c for c in db.rows(s, db.Run, tenant, parent_id=id) if c.status not in TERMINAL
                                 and not c.state.get("child_contract", {}).get("required", True)]
                if optional_live:
                    for child in optional_live:
                        service.control(s, tenant, child.id, "cancel", child.version, "Parent completion closes optional exploration")
                    r.status, r.wait_reason = "WAITING", "CHILD_RUN"
                    db.emit(s, r, "OPTIONAL_CHILDREN_CLOSING", "Settle optional exploration before root completion")
                    db.schedule(s, r, 2)
                    return
                service.require_finalizable(s, r)
                state = r.state
                r.phase = "VERIFYING"
                db.emit(s, r, "VERIFICATION_STARTED", "Verifying a frozen workspace in an independent environment")
            return state, {"run_id": id, "epoch": epoch, "run_version": r.version, "task_id": r.task_id,
                           "task_digest": digest(state["task"]), "input_revision": state.get("input_revision", 0),
                           "artifact_version": state["artifact_version"], "semantic_digest": digest(state["semantic"])}
        prepared = await asyncio.to_thread(prepare_verification)
        if prepared is None:
            return
        state, evidence_metadata = prepared
        baseline = json.loads(await asyncio.to_thread(objects.get, tenant, state["baseline_ref"]))
        current = json.loads(await asyncio.to_thread(objects.get, tenant, state["workspace_ref"]))
        build = state.get("build", {})
        try:
            image = None if state["fixture"] else await sandbox.image_digest(build.get("dependency_image") or None)
        except Fault as exc:
            if exc.code not in {"SANDBOX_UNAVAILABLE", "SANDBOX_MANAGER_REQUIRED"}:
                raise
            image = None
        if state.get("image_digest") and state["image_digest"] != image:
            raise Fault("SEMANTIC_DRIFT", "Verification sandbox differs from the execution image")
        verification_operation = uid()
        async def execute_verification(root, argv, image, timeout, readonly=True, build=None):
            return await resources.execute(tenant, id, verification_operation + ":" + uid(), root, argv, image,
                                           timeout, readonly, build)
        acceptance = await asyncio.to_thread(service.resolve_acceptance, tenant, state["acceptance"])
        acceptance["conditions"] = acceptance.get("conditions", []) + state["task"].get("acceptance_conditions", [])
        report = await verify(
            tenant, id, epoch, baseline, current, acceptance,
            state["task"]["allowed_paths"], image, state.get("repository"), build, execute_verification, evidence_metadata
        )
        patch = await asyncio.to_thread(sandbox.patch, baseline, current, state.get("repository"))
        report["checks"].append(await asyncio.to_thread(sandbox.check_patch, baseline, current, patch, state.get("repository")))
        patch_ref = await asyncio.to_thread(resources.put, tenant, id, patch.encode())
        report["artifact_digest"] = patch_ref["digest"]
        logs = {"acceptance": report.get("result"), "build": report.get("build_result")}
        log_ref = await asyncio.to_thread(resources.put, tenant, id, logs)
        report["logs"] = {"ref": log_ref, "digest": log_ref["digest"], "encoding": "json", "line_start": 0}
        report["deliverables"] = {kind: "present" for kind in state["task"]["deliverables"]}
        report_ref = await asyncio.to_thread(resources.put, tenant, id, report)
        summary_ref = await asyncio.to_thread(resources.put, tenant, id, state["completion"].encode())
        def commit_verification():
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                if (r.state["workspace_digest"] != report["workspace_digest"]
                    or r.state.get("input_revision", 0) != state.get("input_revision", 0)):
                    raise Fault("VERIFICATION_STALE", "Workspace changed during verification")
                if r.cancel_requested:
                    return
                service.require_finalizable(s, r)
                passed = report["verdict"] == "PASS"
                for name, kind, ref in [
                    ("changes.patch", "patch", patch_ref),
                    ("verification.json", "test_report", report_ref),
                    ("summary.md", "summary", summary_ref),
                    ("verification-log.json", "log", log_ref),
                ]:
                    s.add(
                        db.Artifact(
                            tenant_id=tenant,
                            run_id=id,
                            name=name,
                            kind=kind,
                            ref=ref,
                            verified=passed,
                            version=state["artifact_version"],
                        )
                    )
                s.add(db.VerificationResult(tenant_id=tenant, run_id=id, data={**report, "evidence_ref": report_ref}))
                r.state = {
                    **r.state,
                    "verification": report,
                    "image_digest": image,
                    "reason": "Verification " + report["verdict"],
                }
                progress.record(s, r, "verification", verdict=report["verdict"], evidence_digest=report_ref["digest"])
                if not passed:
                    progress.failed(s, r, "VERIFICATION_" + report["verdict"], report.get("failure_class") or "verification")
                repair_count = cast(RunState, r.state).get("repair_attempts", 0)
                can_repair = (report["verdict"] == "FAIL"
                              and report.get("failure_class") == "acceptance"
                              and repair_count < state["budget"].get("max_repair_attempts", 2)
                              and state["turn"] < state["budget"]["max_turns"]
                              and (db.clock(s) - r.created_at).total_seconds() < state["budget"]["max_wall_seconds"])
                r.status = "SUCCEEDED" if passed else "ACTIVE" if can_repair else "FAILED" if report["verdict"] == "FAIL" else "PAUSED"
                if can_repair:
                    r.phase = "REPAIRING"
                    r.state = {**r.state, "completion": None, "repair_attempts": repair_count + 1,
                               "verification_feedback": {"verdict": "FAIL", "checks": report["checks"],
                                                         "evidence_ref": report_ref}}
                    db.schedule(s, r)
                    db.emit(s, r, "VERIFICATION_REPAIR_REQUESTED", "Acceptance failed; bounded repair remains")
                db.emit(s, r, "VERIFICATION_COMPLETED", r.state["reason"], evidence_ref=report_ref)
                service.checkpoint(s, r)
        await asyncio.to_thread(commit_verification)

    def tenants(self):
        if settings.worker_tenants:
            return sorted(set(t.strip() for t in settings.worker_tenants.split(",") if t.strip()))
        if settings.auth_mode == "local":
            return ["local"]
        with db.Session() as s:
            return list(s.scalars(select(db.Tenant.id).order_by(db.Tenant.id)))

    async def archived_quanta(self, tenant, quanta):
        for _ in range(quanta):
            if not await self.once(tenant):
                break

    async def run(self):
        # Blocking effect work and lease renewals need distinct spare execution capacity.
        # The pool remains bounded even at the supported maximum number of run slots.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
            max_workers=settings.worker_slots * 3 + 8, thread_name_prefix="forge-io",
        ))
        active, cursor = {}, 0
        try:
            while not self.stopping:
                tenants = await asyncio.to_thread(self.tenants)
                completed = [task for task in active if task.done()]
                worked = False
                for task in completed:
                    try:
                        worked = task.result() or worked
                    except Exception as exc:
                        log.error("Worker quantum failed: %s", type(exc).__name__)
                for task in completed:
                    del active[task]
                # Reserve each tenant's fair share, even when its fast quanta finish before
                # another tenant's slow model call. Occupancy, not admission order, is bounded.
                share = max(1, settings.worker_slots // max(1, len(tenants)))
                for _ in range(len(tenants)):
                    if len(active) >= settings.worker_slots:
                        break
                    if not tenants:
                        break
                    tenant = tenants[cursor % len(tenants)]
                    cursor += 1
                    if sum(t == tenant for t in active.values()) < share:
                        active[asyncio.create_task(self.once(tenant))] = tenant
                if active:
                    await asyncio.wait(active, timeout=1 if worked else 0.1, return_when=asyncio.FIRST_COMPLETED)
                if not worked:
                    await asyncio.sleep(0.1)
        finally:
            # Stop admitting new work and let existing quanta reach their durable boundary.
            await asyncio.gather(*active, return_exceptions=True)


def main():
    logging.basicConfig(level=logging.INFO)
    from .telemetry import configure

    configure()
    worker = Worker()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(worker, "stopping", True))
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
