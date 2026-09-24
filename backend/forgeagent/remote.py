import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from datetime import timedelta
from urllib.parse import urlsplit

import httpx

from . import db, service
from .config import settings
from .domain import Fault, digest, uid
from .storage import objects


class SafeTransport(httpx.AsyncBaseTransport):
    """Resolve once, validate every address, and connect to that exact IP with original TLS SNI."""

    def __init__(self):
        # IP pinning changes the pool origin; avoid reusing TLS connections across hostnames sharing an IP.
        self.transport = httpx.AsyncHTTPTransport(retries=0, limits=httpx.Limits(max_keepalive_connections=0))

    async def handle_async_request(self, request):
        address = await validate_url(str(request.url))
        hostname = request.url.host
        request.headers["Host"] = request.url.netloc.decode()
        request.extensions["sni_hostname"] = hostname
        request.url = request.url.copy_with(host=address)
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        await self.transport.aclose()


def safe_client(**kwargs):
    kwargs.pop("follow_redirects", None)
    return httpx.AsyncClient(**kwargs, follow_redirects=False, trust_env=False, transport=SafeTransport())


async def rpc(connection, method, params, request_id=None):
    await validate_url(connection["url"])
    headers = {"MCP-Protocol-Version": connection["protocol"], "Mcp-Method": method}
    if connection["kind"] == "a2a":
        headers = {"A2A-Version": connection["protocol"]}
    elif "taskId" in params or "name" in params:
        headers["Mcp-Name"] = params.get("taskId", params.get("name"))
    async with safe_client(timeout=30) as client:
        async with client.stream(
            "POST",
            connection["url"],
            headers=headers,
            json={"jsonrpc": "2.0", "id": request_id or uid(), "method": method, "params": params},
        ) as response:
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 1024 * 1024:
                    raise Fault("REMOTE_LIMIT", "Remote response exceeds 1 MiB")
    result = json.loads(chunks)
    if "error" in result:
        raise Fault("REMOTE_ERROR", "Remote JSON-RPC request returned an error")
    return result["result"]


def remote_result(connection, value):
    if connection["kind"] == "mcp":
        task_id = value.get("taskId")
        state = value.get("status")
    else:
        value = value.get("task", value)
        task_id = value.get("id") if "status" in value else None
        state = value.get("status", {}).get("state", "").removeprefix("TASK_STATE_").lower().replace("-", "_")
    pending = task_id and state not in {"completed", "failed", "cancelled", "canceled", "rejected"}
    failed = state in {"failed", "rejected"} or value.get("isError") or value.get("result", {}).get("isError")
    receipt = {"response": value, "exit_code": 1 if failed else 0, "trust": "untrusted_remote_output"}
    if task_id:
        receipt.update(
            remote_task_id=task_id,
            remote_status=state,
            poll_seconds=max(1, min(60, value.get("pollIntervalMs", 5000) / 1000)),
            pending=bool(pending),
        )
    return receipt


async def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise Fault("REMOTE_URL", "Remote endpoints must be credential-free HTTPS URLs", 422)
    if parsed.hostname not in {h.strip() for h in settings.remote_hosts.split(",") if h.strip()}:
        raise Fault("REMOTE_HOST", "Endpoint host is not on the administrator allowlist", 403)
    answers = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(item[4][0]).is_global for item in answers):
        raise Fault("REMOTE_ADDRESS", "Private, loopback and metadata addresses are forbidden", 403)
    return answers[0][4][0]


def authorize_remote(s, run, action):
    auth = service.authorization(s, run, "external.write")
    approvals = db.rows(s, db.Approval, run.tenant_id, action_id=action.id)
    if not any(
        p.decision == "approved"
        and p.expires_at > db.clock(s)
        and p.policy_epoch == auth.data["epoch"]
        and p.effect_digest == action.effect_digest
        and p.resource_version == run.state["workspace_digest"]
        for p in approvals
    ):
        raise Fault("APPROVAL_REQUIRED", "Current effect, resource and permission require a valid approval", 403)


def prepare_remote(s, run, connection_id, operation, arguments):
    service.authorization(s, run, "external.write")
    service.consume_tool_slot(s, run)
    connection = db.get(s, db.ToolVersion, run.tenant_id, connection_id)
    if connection.status != "active" or connection.data.get("kind") not in {"mcp", "a2a"}:
        raise Fault("CONNECTION_DISABLED", "Remote connection is disabled")
    args = {
        "connection_id": connection_id,
        "connection": connection.data,
        "operation": operation,
        "arguments": arguments,
    }
    a = db.Action(
        tenant_id=run.tenant_id,
        run_id=run.id,
        logical_key="remote:" + uid(),
        tool="remote.call",
        args=args,
        effect_class="external_write",
        status="WAITING_APPROVAL",
        effect_digest=digest({"args": args, "actor": run.actor, "resource": run.state["workspace_digest"]}),
    )
    s.add(a)
    s.flush()
    auth = service.authorization(s, run)
    s.add(
        db.Approval(
            tenant_id=run.tenant_id,
            action_id=a.id,
            effect_digest=a.effect_digest,
            resource_version=run.state["workspace_digest"],
            policy_epoch=auth.data["epoch"],
            expires_at=db.clock(s) + timedelta(minutes=15),
        )
    )
    run.status, run.wait_reason = "WAITING", "APPROVAL"
    db.emit(s, run, "APPROVAL_REQUESTED", "Remote effect prepared; awaiting specific authorization", action_id=a.id)
    db.schedule(s, run, 5)
    return a


async def dispatch_remote(tenant, run_id, action_id, args):
    connection = args["connection"]
    await validate_url(connection["url"])
    protocol = connection["protocol"]
    if connection["kind"] == "mcp" and protocol == "2025-11-25":
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(connection["url"], timeout=30, httpx_client_factory=safe_client) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                if initialized.protocolVersion != protocol:
                    raise Fault("PROTOCOL_MISMATCH", "MCP server negotiated an unpinned version")
                result = await client.call_tool(args["operation"], args["arguments"])
                return {"exit_code": 1 if result.isError else 0, "response": result.model_dump(mode="json")}
    if connection["kind"] == "mcp" and protocol == "2026-07-28":
        # Separate stateless adapter: never send legacy initialize/session fields.
        params = {"name": args["operation"], "arguments": args["arguments"]}
        if connection.get("tasks_extension"):
            discovered = await rpc(connection, "server/discover", {})
            if "io.modelcontextprotocol/tasks" not in discovered.get("capabilities", {}).get("extensions", {}):
                raise Fault("EXTENSION_MISMATCH", "Remote server did not advertise the pinned Tasks extension")
            params["_meta"] = {
                "io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}
            }
        result = await rpc(connection, "tools/call", params, action_id)
        if result.get("resultType") == "task" and not connection.get("tasks_extension"):
            raise Fault("UNNEGOTIATED_TASK", "Remote task was returned without extension negotiation")
        return remote_result(connection, result)
    if connection["kind"] == "a2a" and protocol == "1.0":
        if args["operation"] != "SendMessage":
            raise Fault("A2A_OPERATION", "A2A creation must use SendMessage")
        return remote_result(connection, await rpc(connection, "SendMessage", args["arguments"], action_id))
    if connection["kind"] == "a2a" and protocol == "0.3.0":
        from a2a.client import A2AClient
        from a2a.types import MessageSendParams, SendMessageRequest

        async with safe_client(timeout=30) as http:
            client = A2AClient(http, url=connection["url"])
            result = await client.send_message(
                SendMessageRequest(id=action_id, params=MessageSendParams.model_validate(args["arguments"]))
            )
            value = result.model_dump(mode="json")
            if "error" in value:
                return {"exit_code": 1, "response": value}
            return remote_result(connection, value.get("result", value))
    raise Fault("PROTOCOL_UNSUPPORTED", "This adapter version is not implemented; no request was sent")


async def poll_remote(tenant, run_id, action_id, owner, epoch):
    with db.transaction(tenant) as s:
        run = db.fence(s, tenant, run_id, owner, epoch)
        action = db.get(s, db.Action, tenant, action_id)
        connection, receipt = action.args["connection"], action.receipt
        task_id = receipt["remote_task_id"]
        cancel = run.cancel_requested and not receipt.get("cancel_sent")
        uncertain_inputs = [
            o
            for o in db.rows(s, db.Outbox, tenant)
            if o.status in {"dispatching", "unknown"} and o.data.get("action_id") == action_id
        ]
        if uncertain_inputs:
            for job in uncertain_inputs:
                job.status = "unknown"
            action.status = "UNKNOWN"
            db.emit(s, run, "REMOTE_INPUT_UNKNOWN", "Interrupted remote input delivery requires reconciliation")
            return
        input_jobs = [
            o for o in db.rows(s, db.Outbox, tenant) if o.status == "pending" and o.data.get("action_id") == action_id
        ]
        approved_inputs = []
        for job in input_jobs:
            auth = service.authorization(s, run, "external.write")
            if cancel or job.data["policy_epoch"] != auth.data["epoch"]:
                job.status = "cancelled"
            else:
                job.status = "dispatching"
                approved_inputs.append((job.id, job.data["params"]))
        deadline = run.created_at + timedelta(seconds=run.state["budget"]["max_wall_seconds"])
        if db.clock(s) > deadline:
            action.status = "UNKNOWN"
            db.emit(s, run, "REMOTE_DEADLINE", "Remote task exceeded deadline; explicit reconciliation required")
            return
    is_mcp = connection["kind"] == "mcp"
    is_v1 = connection["protocol"] == "1.0"
    params = {"taskId" if is_mcp else "id": task_id}
    cancel_sent = receipt.get("cancel_sent", False)
    try:
        for job_id, input_params in approved_inputs:
            await rpc(connection, "tasks/update", input_params, job_id)
            with db.transaction(tenant) as s:
                run = db.fence(s, tenant, run_id, owner, epoch)
                job = db.get(s, db.Outbox, tenant, job_id, True)
                job.status = "sent"
                db.emit(s, run, "REMOTE_INPUT_DELIVERED", "Reviewed remote input acknowledged", outbox_id=job_id)
        if cancel:
            await rpc(connection, "tasks/cancel" if is_mcp or not is_v1 else "CancelTask", params)
            cancel_sent = True
        result = await rpc(connection, "tasks/get" if is_mcp or not is_v1 else "GetTask", params)
        result = remote_result(connection, result)
        result["cancel_sent"] = cancel_sent
        result_ref = objects.put(tenant, run_id, result)
    except Exception:
        with db.transaction(tenant) as s:
            run = db.fence(s, tenant, run_id, owner, epoch)
            uncertain = False
            for job_id, _ in approved_inputs:
                job = db.get(s, db.Outbox, tenant, job_id, True)
                if job.status == "dispatching":
                    job.status = "unknown"
                    uncertain = True
            if uncertain:
                action = db.get(s, db.Action, tenant, action_id, True)
                action.status = "UNKNOWN"
                db.emit(s, run, "REMOTE_INPUT_UNKNOWN", "Remote input may have been delivered; reconciliation required")
                return
            db.schedule(s, run, 10)
            db.emit(s, run, "REMOTE_POLL_DELAYED", "Remote status query failed; no effect was repeated")
        return
    with db.transaction(tenant) as s:
        run = db.fence(s, tenant, run_id, owner, epoch)
        action = db.get(s, db.Action, tenant, action_id, True)
        action.receipt = {**result, "ref": result_ref}
        action.status = (
            "RUNNING"
            if result.get("pending")
            else "CANCELLED"
            if result.get("remote_status") in {"cancelled", "canceled"}
            else "SUCCEEDED"
            if result["exit_code"] == 0
            else "FAILED"
        )
        run.status, run.wait_reason = (
            ("WAITING", "TOOL")
            if result.get("pending")
            else ("CANCELLING", None)
            if run.cancel_requested
            else ("ACTIVE", None)
        )
        if result.get("remote_status") in {"input_required", "auth_required"}:
            run.status = "PAUSED"
            run.state = {
                **run.state,
                "input_required": True,
                "reason": "Remote task requires reviewed input; see action receipt",
            }
        db.emit(
            s,
            run,
            "REMOTE_TASK_OBSERVED",
            "Remote task state persisted",
            action_id=action.id,
            remote_status=result.get("remote_status"),
        )
        db.schedule(s, run, result.get("poll_seconds", 5) if result.get("pending") else 0)


def accept_callback(s, tenant, provider, message_id, timestamp, body, signature):
    if not settings.callback_secret:
        raise Fault("CALLBACK_DISABLED", "Callback signing secret is not configured", 503)
    if abs(db.clock(s).timestamp() - timestamp) > 300:
        raise Fault("CALLBACK_EXPIRED", "Callback timestamp outside replay window", 401)
    expected = hmac.new(
        settings.callback_secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise Fault("CALLBACK_SIGNATURE", "Invalid callback signature", 401)
    key = digest({"provider": provider, "message_id": message_id})[7:]
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": tenant + ":" + key})
    if s.get(db.Inbox, (tenant, key)):
        return {"duplicate": True}
    payload = json.loads(body)
    action = db.get(s, db.Action, tenant, payload["action_id"])
    if action.args.get("connection_id") != provider:
        raise Fault("CALLBACK_PROVIDER", "Callback is not associated with this provider", 403)
    # Inbox is a durable observation. It cannot promote arbitrary remote output into verified success.
    s.add(db.Inbox(tenant_id=tenant, id=key, data={"provider": provider, "message_id": message_id, "payload": payload}))
    run = db.get(s, db.Run, tenant, action.run_id, True)
    db.emit(s, run, "CALLBACK_RECEIVED", "Authenticated remote observation received", action_id=action.id, inbox_id=key)
    return {"duplicate": False}
