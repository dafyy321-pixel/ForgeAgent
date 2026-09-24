import asyncio
import json
import logging
import signal
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import and_, or_, select

from . import db, models, service
from .config import settings
from .context import compile_context
from .domain import TERMINAL, Fault, canonical, digest, uid
from .sandbox import files, safe_path, sandbox
from .storage import objects
from .telemetry import tracer
from .verification import verify

log = logging.getLogger("forge.worker")


class Worker:
    def __init__(self, owner=None, model=None):
        self.owner = owner or uid()
        self.model = model or models.generate
        self.stopping = False

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
            r = s.scalar(
                select(db.Run)
                .join(db.Job, and_(db.Job.tenant_id == db.Run.tenant_id, db.Job.run_id == db.Run.id))
                .where(
                    db.Run.tenant_id == tenant,
                    db.Run.status.in_(["QUEUED", "ACTIVE", "CANCELLING", "WAITING"]),
                    db.Run.available_at <= time,
                    or_(db.Run.lease_until.is_(None), db.Run.lease_until <= time),
                )
                .order_by(db.Run.available_at, db.Run.created_at)
                .with_for_update(of=db.Run, skip_locked=True)
                .limit(1)
            )
            if not r:
                return None
            prior_epoch = r.epoch
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
                        a.status = "READY" if a.effect_class in {"read", "workspace_write"} else "UNKNOWN"
            for call in db.rows(s, db.ModelCall, tenant, run_id=r.id):
                if call.status == "DISPATCHED":
                    call.status = "UNKNOWN"
                    service.settle(s, r, call.id, None)
                    r.status = "PAUSED"
                    r.state = {
                        **r.state,
                        "reason": "Interrupted model request has unknown usage; reconcile budget before resuming",
                    }
            if r.status == "QUEUED":
                r.status = "ACTIVE"
            db.emit(s, r, "LEASE_CLAIMED", "Worker acquired exclusive decision lease", recovered=prior_epoch > 0)
            return r.id, r.epoch

    async def heartbeat(self, tenant, id, epoch):
        while True:
            await asyncio.sleep(max(1, settings.lease_seconds / 3))
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                r.lease_until = db.clock(s) + timedelta(seconds=settings.lease_seconds)

    def pause(self, tenant, id, epoch, reason):
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            if r.status in TERMINAL:
                return
            r.status, r.wait_reason = "PAUSED", None
            r.state = {**r.state, "reason": reason}
            db.emit(s, r, "RUN_PAUSED", reason)
            service.checkpoint(s, r)

    async def once(self, tenant):
        lease = self.claim(tenant)
        if not lease:
            return False
        id, epoch = lease
        beat = asyncio.create_task(self.heartbeat(tenant, id, epoch))

        async def traced():
            with tracer.start_as_current_span(
                "run.advance", attributes={"forge.run_id": id, "forge.lease_epoch": epoch}
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
                self.pause(tenant, id, epoch, f"{exc.code}: {exc.message}")
        except Exception as exc:
            log.error("worker operation failed: %s", type(exc).__name__, extra={"run_id": id})
            try:
                self.pause(tenant, id, epoch, f"Execution stopped: {type(exc).__name__}")
            except Exception:
                log.error("Unable to persist failure; lease recovery will reconcile the operation")
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
            with db.transaction(tenant) as s:
                r = db.get(s, db.Run, tenant, id, True)
                if r.epoch == epoch and r.lease_owner == self.owner:
                    r.lease_owner = r.lease_until = None
                    job = db.get(s, db.Job, tenant, id)
                    job.status = "READY" if r.status in {"ACTIVE", "QUEUED", "WAITING", "CANCELLING"} else "DONE"
        return True

    async def advance(self, tenant, id, epoch):
        with db.transaction(tenant) as s:
            db.fence(s, tenant, id, self.owner, epoch)
            remote_pending = [
                a.id
                for a in db.rows(s, db.Action, tenant, run_id=id)
                if a.status == "RUNNING" and a.receipt and a.receipt.get("remote_task_id")
            ]
        if remote_pending:
            from .remote import poll_remote

            for action_id in remote_pending:
                await poll_remote(tenant, id, action_id, self.owner, epoch)
            return
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            state = r.state
            if r.status == "PAUSED":
                return
            service.authorization(s, r)
            if state["semantic"]["tools_digest"] != digest(service.TOOLS):
                raise Fault("SEMANTIC_DRIFT", "Pinned tool catalog differs from this worker")
            if state["semantic"].get("implementation") != service.implementation_bindings():
                raise Fault("SEMANTIC_DRIFT", "Pinned runtime implementation changed; fork with current version")
            actions = db.rows(s, db.Action, tenant, run_id=id)
            unknown = [a for a in actions if a.status == "UNKNOWN"]
            children = db.rows(s, db.Run, tenant, parent_id=id)
            if unknown:
                r.status, r.wait_reason = "WAITING", "RECONCILIATION"
                r.state = {**state, "reason": "External effect requires reconciliation"}
                db.emit(s, r, "RECONCILIATION_REQUIRED", "Unknown effects block further execution")
                db.schedule(s, r, 30)
                return
            if r.cancel_requested:
                if any(c.status not in TERMINAL for c in children):
                    r.status, r.wait_reason = "CANCELLING", "CHILD_RUN"
                    db.schedule(s, r, 2)
                    return
                for a in actions:
                    if a.status in {"READY", "PREPARED", "WAITING_APPROVAL"}:
                        a.status = "CANCELLED"
                r.status, r.wait_reason = "CANCELLED", None
                db.emit(s, r, "RUN_CANCELLED", "All in-flight effects settled; no new actions dispatched")
                return
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
                db.schedule(s, r, 5)
                return
            if any(c.status not in TERMINAL for c in children):
                r.status, r.wait_reason = "WAITING", "CHILD_RUN"
                db.schedule(s, r, 2)
                return
            if children and any(c.status != "SUCCEEDED" for c in children):
                raise Fault("CHILD_FAILED", "A required child run did not succeed")
            if (db.clock(s) - r.created_at).total_seconds() > state["budget"]["max_wall_seconds"]:
                raise Fault("WALL_TIME_LIMIT", "Task wall time limit reached")
            r.status, r.wait_reason = "ACTIVE", None
            ready = [a.id for a in actions if a.status == "READY"]
            for a in actions:
                if a.status in {"SUCCEEDED", "FAILED"} and not a.consumed:
                    a.consumed = True
                    db.emit(s, r, "ACTION_CONSUMED", "Persisted result consumed", action_id=a.id)
            state = r.state
        content = json.loads(objects.get(tenant, state["workspace_ref"]))
        root = sandbox.restore(tenant, id, epoch, content)
        if ready:
            for action_id in ready:
                await self.dispatch(tenant, id, epoch, action_id, root)
            return
        if state.get("completion"):
            await self.complete(tenant, id, epoch)
            return
        if state["turn"] >= state["budget"]["max_turns"]:
            raise Fault("TURN_LIMIT", "Maximum model turns reached")
        await self.decide(tenant, id, epoch)

    async def decide(self, tenant, id, epoch):
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            state = r.state
            actions = db.rows(s, db.Action, tenant, run_id=id)
            observations = [
                {"action_id": a.id, "tool": a.tool, "args": a.args, "result": a.receipt} for a in actions if a.receipt
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
                state,
                observations,
                state["skills"],
                state["memories"],
                settings.context_window,
                settings.max_output,
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
            service.reserve(
                s,
                r,
                call_id,
                reservation,
                0 if state["fixture"] else len(canonical(messages)) + settings.max_output + 2048,
            )
            call = db.ModelCall(
                tenant_id=tenant,
                id=call_id,
                run_id=id,
                status="DISPATCHED",
                data={
                    "request_digest": digest(messages),
                    "context_digest": manifest["digest"],
                    "model_id": state["semantic"]["model_id"],
                    "messages_ref": objects.put(tenant, id, messages),
                },
            )
            s.add(call)
            s.add(db.ContextManifest(tenant_id=tenant, run_id=id, data={"turn": state["turn"] + 1, **manifest}))
            r.phase = "DECIDING"
            db.emit(s, r, "MODEL_REQUESTED", "Context and model request persisted", call_id=call_id)
        try:
            if state["fixture"]:
                if not actions:
                    current = json.loads(objects.get(tenant, state["workspace_ref"]))
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
                raw_text, usage, raw, provider_id = await self.model(messages, state["semantic"]["model_id"])
        except Exception as exc:
            with db.transaction(tenant) as s:
                r = db.fence(s, tenant, id, self.owner, epoch)
                call = db.get(s, db.ModelCall, tenant, call_id)
                code = getattr(exc, "status_code", None)
                known_unbilled = code == 429 or isinstance(exc, Fault)
                call.status = "FAILED" if known_unbilled else "UNKNOWN"
                call.data = {**call.data, "error_type": type(exc).__name__}
                service.settle(s, r, call_id, 0 if known_unbilled else None, 0 if known_unbilled else None)
                count = r.state.get("model_errors", 0) + 1
                r.state = {**r.state, "model_errors": count}
                if code in {429, 500, 502, 503} and count <= 2 and known_unbilled:
                    r.status, r.wait_reason = "WAITING", "RETRY_TIMER"
                    db.schedule(s, r, min(60, 2**count))
                    db.emit(s, r, "MODEL_RETRY_SCHEDULED", "Rate limit; durable bounded retry")
                    return
            raise Fault("MODEL_CALL_FAILED", "Model request failed; unknown usage stays reserved") from exc
        response_ref = objects.put(tenant, id, raw)
        try:
            decision = models.parse_decision(raw_text)
            validation_error = None
        except (ValidationError, ValueError) as exc:
            decision, validation_error = None, str(exc)[:1500]
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            call = db.get(s, db.ModelCall, tenant, call_id)
            call.status = "SUCCEEDED"
            call.data = {**call.data, "response_ref": response_ref, "usage": usage, "provider_request_id": provider_id}
            actual = models.estimate_cost(usage["input_tokens"], usage["output_tokens"]) if usage else None
            service.settle(s, r, call_id, actual, sum(usage.values()) if usage else None)
            r.state = {
                **r.state,
                "turn": r.state["turn"] + 1,
                "tokens": r.state["tokens"] + (sum(usage.values()) if usage else 0),
            }
            billing_unknown = usage is None
            if billing_unknown:
                call.status = "UNKNOWN"
                r.status = "PAUSED"
                r.state = {**r.state, "reason": "Provider omitted usage; reconcile reserved billing before continuing"}
                db.emit(s, r, "MODEL_USAGE_UNKNOWN", r.state["reason"], call_id=call_id)
            if validation_error:
                count = r.state.get("format_errors", 0) + 1
                r.state = {**r.state, "format_errors": count, "input": "Invalid decision JSON. " + validation_error}
                if count > 2:
                    r.status = "PAUSED"
                db.emit(s, r, "MODEL_FORMAT_ERROR", "Model response did not satisfy decision schema")
                return
            s.add(
                db.Turn(
                    tenant_id=tenant,
                    run_id=id,
                    data={
                        "ordinal": r.state["turn"],
                        "call_id": call_id,
                        "decision": decision.model_dump(),
                        "context_digest": manifest["digest"],
                    },
                )
            )
            db.emit(s, r, "DECISION_COMMITTED", decision.summary, call_id=call_id)
            if r.cancel_requested or r.pause_requested:
                return
            if decision.kind == "tool_calls":
                if len(actions) + len(decision.calls) > state["budget"]["max_tool_calls"]:
                    r.status = "PAUSED"
                    db.emit(s, r, "TOOL_LIMIT", "Tool call budget exhausted")
                    return
                fingerprint = digest(
                    {"calls": [c.model_dump() for c in decision.calls], "workspace": r.state["workspace_digest"]}
                )
                repeats = r.state.get("repeats", 0) + 1 if r.state.get("fingerprint") == fingerprint else 1
                r.state = {**r.state, "fingerprint": fingerprint, "repeats": repeats}
                if repeats >= 3:
                    r.status = "PAUSED"
                    db.emit(s, r, "NO_PROGRESS", "Three identical decisions without workspace progress")
                    return
                for i, call in enumerate(decision.calls):
                    try:
                        with s.begin_nested():
                            service.prepare_action(s, r, call, f"{r.state['turn']}:{i}")
                    except Fault as exc:
                        r.status = "PAUSED"
                        r.state = {**r.state, "reason": f"{exc.code}: {exc.message}"}
                        db.emit(s, r, "RESOURCE_LIMIT", exc.message)
                        return
            elif decision.kind == "request_input":
                r.status, r.wait_reason = "PAUSED", None
                r.state = {**r.state, "reason": decision.summary, "input_required": True}
            elif decision.kind == "delegate":
                if billing_unknown:
                    r.state = {**r.state, "input": "Billing must be reconciled before requesting delegation again."}
                    db.emit(s, r, "DELEGATION_DEFERRED", "Unknown model billing prevents child dispatch")
                    return
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
                r.status, r.wait_reason = "WAITING", "CHILD_RUN"
                db.emit(s, r, "CHILD_CREATED", decision.summary, child_run_id=child.id)
                db.schedule(s, r, 2)
            else:
                r.state = {**r.state, "completion": decision.summary}
                r.phase = "VERIFYING"
            if billing_unknown:
                r.status = "PAUSED"
            db.emit(s, r, "DECISION_APPLIED", "Decision effects committed", decision_kind=decision.kind)

    async def dispatch(self, tenant, id, epoch, action_id, root):
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            a = db.get(s, db.Action, tenant, action_id, True)
            if a.status != "READY" or r.cancel_requested or r.pause_requested:
                return
            spec = service.TOOLS.get(a.tool)
            if spec is None:
                from .remote import dispatch_remote

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
        changed = None
        try:
            if remote:
                receipt = await dispatch_remote(tenant, id, action_id, args)
            elif tool == "repo.list":
                receipt = {"files": sorted(files(root)), "exit_code": 0}
            elif tool == "repo.read":
                path = safe_path(root, args["path"])
                body = path.read_bytes()
                if len(body) > 256 * 1024:
                    raise Fault("READ_LIMIT", "Read exceeds 256 KiB", 422)
                receipt = {"content": body.decode("utf-8"), "digest": digest(body), "exit_code": 0}
            elif tool == "repo.write":
                path = safe_path(root, args["path"])
                if not any(
                    args["path"] == p or args["path"].startswith(p.rstrip("/") + "/")
                    for p in state["task"]["allowed_paths"]
                ):
                    raise Fault("SCOPE_DENIED", "Write outside task allowed paths", 403)
                body = args["content"].encode("utf-8")
                if len(body) > 1024 * 1024:
                    raise Fault("WRITE_LIMIT", "File exceeds 1 MiB", 422)
                current = digest(path.read_bytes()) if path.exists() else "absent"
                if current != digest(body) and current != args.get("expected_digest"):
                    raise Fault("PRECONDITION_FAILED", "File digest changed")
                path.parent.mkdir(parents=True, exist_ok=True)
                # This epoch has an exclusive directory. Only the complete, stored snapshot is
                # committed; interruption during this write restores the prior epoch's snapshot.
                path.write_bytes(body)
                changed = files(root)
                receipt = {"path": args["path"], "digest": digest(body), "exit_code": 0}
            elif tool == "observation.read":
                if not state["semantic"].get("harness", {}).get("observation_recall", True):
                    raise Fault("HARNESS_RECALL_DISABLED", "Observation recall is disabled in this task contract")
                with db.transaction(tenant) as s:
                    source = db.get(s, db.Action, tenant, args["action_id"])
                    if source.run_id != id or not source.receipt or not source.receipt.get("ref"):
                        raise Fault("OBSERVATION_SCOPE", "Observation is not available in this task", 404)
                    ref = source.receipt["ref"]
                raw = json.loads(objects.get(tenant, ref))
                text = raw.get("output", raw.get("content", canonical(raw).decode()))
                lines = text.splitlines()
                start, count = args.get("line_start", 0), args.get("max_lines", 100)
                receipt = {
                    "exit_code": 0,
                    "source_digest": ref["digest"],
                    "line_start": start,
                    "total_lines": len(lines),
                    "content": "\n".join(lines[start : start + count])[:65536],
                }
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
                for name in differences:
                    path = safe_path(root, name)
                    if name not in child_files:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(child_files[name], encoding="utf-8", newline="")
                changed = files(root)
                receipt = {"exit_code": 0, "child_id": child.id, "integrated_paths": differences}
            elif tool == "tests.run":
                image = await sandbox.image_digest()
                if state.get("image_digest") and state["image_digest"] != image:
                    raise Fault("SEMANTIC_DRIFT", "Sandbox image changed")
                if not state.get("image_digest"):
                    with db.transaction(tenant) as s:
                        r = db.fence(s, tenant, id, self.owner, epoch)
                        r.state = {**r.state, "image_digest": image}
                        db.emit(s, r, "SANDBOX_IMAGE_BOUND", "Sandbox image content address pinned", image_digest=image)
                receipt = await sandbox.execute(root, args["argv"], image, readonly=True)
            else:
                raise Fault("TOOL_UNAVAILABLE", "Tool is unavailable")
            action_status = (
                "RUNNING" if receipt.get("pending") else "SUCCEEDED" if receipt.get("exit_code", 0) == 0 else "FAILED"
            )
        except Exception as exc:
            receipt = {"exit_code": 1, "error": str(exc)[:2000]}
            action_status = "UNKNOWN" if remote else "FAILED"
        receipt_ref = objects.put(tenant, id, receipt)
        workspace_ref = objects.put(tenant, id, changed) if changed is not None else None
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            a = db.get(s, db.Action, tenant, action_id, True)
            a.status = action_status
            a.receipt = {
                "ref": receipt_ref,
                "preview": canonical(receipt).decode()[:4000],
                "exit_code": receipt.get("exit_code", 0),
                **({"digest": receipt["digest"]} if "digest" in receipt else {}),
            }
            if receipt.get("remote_task_id"):
                a.receipt = {**a.receipt, **receipt}
                r.status, r.wait_reason = "WAITING", "TOOL"
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
            db.emit(s, r, "ACTION_RESULT", f"{tool}: {action_status}", action_id=a.id, observation=a.receipt)

    async def complete(self, tenant, id, epoch):
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            if any(
                a.status not in {"SUCCEEDED", "FAILED", "CANCELLED"} for a in db.rows(s, db.Action, tenant, run_id=id)
            ):
                raise Fault("UNSETTLED_ACTION", "Cannot verify while actions remain unsettled")
            state = r.state
            r.phase = "VERIFYING"
            db.emit(s, r, "VERIFICATION_STARTED", "Verifying a frozen workspace in an independent environment")
        baseline = json.loads(objects.get(tenant, state["baseline_ref"]))
        current = json.loads(objects.get(tenant, state["workspace_ref"]))
        image = None if state["fixture"] else await sandbox.image_digest()
        if state.get("image_digest") and state["image_digest"] != image:
            raise Fault("SEMANTIC_DRIFT", "Verification sandbox differs from the execution image")
        report = await verify(
            tenant, id, epoch, baseline, current, state["acceptance"], state["task"]["allowed_paths"], image
        )
        patch = sandbox.patch(baseline, current)
        patch_ref = objects.put(tenant, id, patch.encode())
        report["artifact_digest"] = patch_ref["digest"]
        report_ref = objects.put(tenant, id, report)
        summary_ref = objects.put(tenant, id, state["completion"].encode())
        with db.transaction(tenant) as s:
            r = db.fence(s, tenant, id, self.owner, epoch)
            if r.state["workspace_digest"] != report["workspace_digest"]:
                raise Fault("VERIFICATION_STALE", "Workspace changed during verification")
            if r.cancel_requested:
                return
            passed = report["verdict"] == "PASS"
            for name, kind, ref in [
                ("changes.patch", "patch", patch_ref),
                ("verification.json", "test_report", report_ref),
                ("summary.md", "summary", summary_ref),
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
            r.status = "SUCCEEDED" if passed else "FAILED" if report["verdict"] == "FAIL" else "PAUSED"
            db.emit(s, r, "VERIFICATION_COMPLETED", r.state["reason"], evidence_ref=report_ref)
            service.checkpoint(s, r)

    async def run(self):
        while not self.stopping:
            try:
                with db.Session() as s:
                    tenants = (
                        settings.worker_tenants.split(",")
                        if settings.worker_tenants
                        else ["local"]
                        if settings.auth_mode == "local"
                        else list(s.scalars(select(db.Tenant.id)))
                    )
                worked = False
                for tenant in tenants:
                    worked = await self.once(tenant) or worked
                    if self.stopping:
                        break
            except Exception as exc:
                log.error("Worker suspended new dispatch after infrastructure error: %s", type(exc).__name__)
                worked = False
            if not worked:
                await asyncio.sleep(1)


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
