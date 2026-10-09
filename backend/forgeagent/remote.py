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
from .remote_contracts import credentials, current, headers, validate_arguments, validate_protocol_arguments


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


def safe_client(headers=None, timeout=httpx.Timeout(5.0), auth=None, **kwargs) -> httpx.AsyncClient:
    kwargs.pop("follow_redirects", None)
    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, **kwargs, follow_redirects=False, trust_env=False, transport=SafeTransport())


async def rpc(connection, method, params, request_id=None):
    await validate_url(connection["url"])
    request_id = request_id or uid()
    wire_headers = {**headers(connection), "MCP-Protocol-Version": connection["protocol"], "Mcp-Method": method,
                    "Accept": "application/json"}
    params = {**params}
    if connection["kind"] == "a2a":
        wire_headers = {**headers(connection), "A2A-Version": connection["protocol"], "Accept": "application/json"}
        if connection["protocol"] == "1.0":
            card = connection.get("negotiated", {}).get("evidence", {}).get("server", {})
            route = next((i.get("tenant") for i in card.get("supportedInterfaces", []) if i.get("url") == connection["url"]
                          and i.get("protocolVersion") == connection["protocol"] and i.get("protocolBinding") == "JSONRPC"), None)
            if route:
                if params.get("tenant", route) != route:
                    raise Fault("REMOTE_TENANT", "Message routing differs from the selected Agent Card interface")
                params["tenant"] = route
    elif connection["protocol"] == "2026-07-28":
        params["_meta"] = {**params.get("_meta", {}), "io.modelcontextprotocol/protocolVersion": connection["protocol"],
                           "io.modelcontextprotocol/clientInfo": {"name": "ForgeAgent", "version": "0.2.0"},
                           "io.modelcontextprotocol/clientCapabilities": {
                               "extensions": {"io.modelcontextprotocol/tasks": {}}} if connection.get("tasks_extension") else {}}
    if connection["kind"] == "mcp" and ("taskId" in params or "name" in params):
        wire_headers["Mcp-Name"] = params.get("taskId", params.get("name"))
    async with safe_client(timeout=30) as client:
        async with client.stream(
            "POST",
            connection["url"],
            headers=wire_headers,
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        ) as response:
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 1024 * 1024:
                    raise Fault("REMOTE_LIMIT", "Remote response exceeds 1 MiB")
    try:
        result = json.loads(chunks)
    except ValueError:
        raise Fault("REMOTE_PROTOCOL", "Remote response is not a JSON-RPC object") from None
    if (not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != request_id
        or ("result" in result) == ("error" in result)):
        raise Fault("REMOTE_PROTOCOL", "Remote response does not match this JSON-RPC request")
    if "error" in result:
        raise Fault("REMOTE_ERROR", "Remote JSON-RPC request returned an error")
    return result["result"]


async def get_document(connection, url):
    await validate_url(url)
    # Discovery and artifacts never forward credentials to a different origin.
    def origin(value):
        parsed = urlsplit(value)
        return parsed.scheme, parsed.hostname, parsed.port or 443
    auth = headers(connection) if origin(url) == origin(connection["url"]) else {}
    async with safe_client(timeout=30) as client:
        async with client.stream("GET", url, headers={**auth, "Accept": "application/json"}) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > min(settings.max_object_bytes, 8 * 1024 * 1024):
                    raise Fault("REMOTE_LIMIT", "Remote document exceeds bounded download limit")
    return bytes(body)


async def discover(connection):
    """Read-only negotiation; registration alone never puts a tool in a model catalog."""
    protocol = connection["protocol"]
    if connection["kind"] == "mcp" and protocol == "2025-11-25":
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.types import PaginatedRequestParams

        async with streamablehttp_client(connection["url"], headers=headers(connection), timeout=30,
                                        httpx_client_factory=safe_client) as (read, write, _):
            async with ClientSession(read, write) as client:
                info = await client.initialize()
                if info.protocolVersion != protocol:
                    raise Fault("PROTOCOL_MISMATCH", "Legacy MCP negotiated an unpinned version")
                tools, cursor = [], None
                for _ in range(10):
                    page = await client.list_tools(params=PaginatedRequestParams(cursor=cursor))
                    tools += [t.model_dump(mode="json") for t in page.tools]
                    cursor = page.nextCursor
                    if not cursor:
                        return {"protocol": protocol, "server": info.model_dump(mode="json"), "tools": tools}
                raise Fault("REMOTE_LIMIT", "Tool discovery exceeds ten pages")
    if connection["kind"] == "mcp":
        info = await rpc(connection, "server/discover", {})
        if protocol not in info.get("supportedVersions", []) or "tools" not in info.get("capabilities", {}):
            raise Fault("PROTOCOL_MISMATCH", "Stateless MCP did not advertise the reviewed protocol/tools")
        if connection.get("tasks_extension") and "io.modelcontextprotocol/tasks" not in info.get("capabilities", {}).get("extensions", {}):
            raise Fault("EXTENSION_MISMATCH", "Server did not advertise the pinned Tasks extension")
        tools, params = [], {}
        for _ in range(10):
            page = await rpc(connection, "tools/list", params)
            tools += page.get("tools", [])
            if not page.get("nextCursor"):
                return {"protocol": protocol, "server": info, "tools": tools}
            params = {"cursor": page["nextCursor"]}
        raise Fault("REMOTE_LIMIT", "Tool discovery exceeds ten pages")
    parsed = urlsplit(connection["url"])
    url = connection.get("discovery_url") or f"https://{parsed.netloc}/.well-known/agent-card.json"
    card = json.loads(await get_document(connection, url))
    if protocol == "1.0":
        if not any(i.get("url") == connection["url"] and i.get("protocolBinding") == "JSONRPC"
                   and i.get("protocolVersion") == protocol for i in card.get("supportedInterfaces", [])):
            raise Fault("PROTOCOL_MISMATCH", "Agent Card has no matching JSONRPC/version/endpoint interface")
        if any(e.get("required") for e in card.get("capabilities", {}).get("extensions", [])):
            raise Fault("EXTENSION_MISMATCH", "Required A2A extensions need an explicit adapter")
    elif (card.get("protocolVersion") != protocol or card.get("url") != connection["url"]
          or card.get("preferredTransport", "JSONRPC") != "JSONRPC"):
        raise Fault("PROTOCOL_MISMATCH", "Legacy Agent Card differs from the pinned connection")
    requirements = card.get("securityRequirements", []) if protocol == "1.0" else card.get("security", [])
    if requirements:
        configured = credentials(connection)
        authorized = configured.get("authorization", "").startswith("Bearer ")
        schemes = card.get("securitySchemes", {})
        def supported(key):
            scheme = schemes.get(key, {})
            http = scheme.get("httpAuthSecurityScheme", {})
            return (http.get("scheme", "").lower() == "bearer" or "oauth2SecurityScheme" in scheme
                    or "openIdConnectSecurityScheme" in scheme or scheme.get("type") in {"oauth2", "openIdConnect"}
                    or (scheme.get("type") == "http" and scheme.get("scheme", "").lower() == "bearer"))
        acceptable = False
        for option in requirements:
            required = option.get("schemes", {}) if protocol == "1.0" else option
            if not required or (authorized and all(supported(key) and set(scopes if isinstance(scopes, list) else scopes.get("list", []))
                                                   <= set(configured.get("scopes", [])) for key, scopes in required.items())):
                acceptable = True
        if not acceptable:
            raise Fault("REMOTE_AUTH_UNSUPPORTED", "Agent Card authentication/scopes are not supported by the configured bearer reference")
    return {"protocol": protocol, "server": card, "tools": []}


def remote_result(connection, value):
    if not isinstance(value, dict):
        raise Fault("REMOTE_PROTOCOL", "Remote tool/task response must be an object")
    if connection["kind"] == "mcp":
        task_id = value.get("taskId")
        state = value.get("status")
    else:
        value = value.get("task", value)
        task_id = value.get("id") if "status" in value else None
        state = value.get("status", {}).get("state", "").removeprefix("TASK_STATE_").lower().replace("-", "_")
    if task_id and state not in {"submitted", "working", "input_required", "auth_required", "completed", "failed", "cancelled", "canceled", "rejected"}:
        raise Fault("REMOTE_STATUS", "Remote task returned an unsupported state")
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
    if not action.args.get("contract"):
        raise Fault("CONNECTION_UNVERIFIED", "Legacy remote effects require explicit rebinding and a reviewed contract")
    current(s, run, action.args["connection_id"])
    auth = service.authorization(s, run, "external.read" if action.effect_class == "read" else "external.write")
    if action.effect_class == "read":
        return
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


def prepare_remote(s, run, connection_id, operation, arguments, logical_key=None):
    connection = current(s, run, connection_id)
    contract = next((t for t in connection["tools"] if t["operation"] == operation), None)
    if not contract:
        raise Fault("REMOTE_TOOL", "Operation was not selected and reviewed", 403)
    service.authorization(s, run, "external.read" if contract["effect"] == "read" else "external.write")
    logical_key = logical_key or "remote:" + uid()
    key = digest([run.tenant_id, run.id, connection_id, operation, logical_key])[7:]
    arguments = {**arguments}
    if contract.get("idempotency_field"):
        if contract["idempotency_field"] in arguments:
            raise Fault("REMOTE_KEY", "Business idempotency keys are owned by the runtime", 422)
        arguments[contract["idempotency_field"]] = key
    validate_arguments(contract["input_schema"], arguments)
    validate_protocol_arguments(connection, arguments)
    service.consume_tool_slot(s, run)
    args = {
        "connection_id": connection_id,
        "connection": connection,
        "operation": operation,
        "arguments": arguments,
        "contract": contract,
        "business_key": key,
    }
    a = db.Action(
        tenant_id=run.tenant_id,
        run_id=run.id,
        logical_key=logical_key,
        tool="remote.call",
        args=args,
        effect_class=contract["effect"],
        status="READY" if contract["effect"] == "read" else "WAITING_APPROVAL",
        effect_digest=digest({"args": args, "actor": run.actor, "resource": run.state["workspace_digest"]}),
    )
    s.add(a)
    s.flush()
    if contract["effect"] == "read":
        db.emit(s, run, "ACTION_PREPARED", "Reviewed remote read prepared", action_id=a.id, effect_digest=a.effect_digest)
        return a
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
    validate_protocol_arguments(connection, args["arguments"])
    await validate_url(connection["url"])
    protocol = connection["protocol"]
    if connection["kind"] == "mcp" and protocol == "2025-11-25":
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(connection["url"], headers=headers(connection), timeout=30, httpx_client_factory=safe_client) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                if initialized.protocolVersion != protocol:
                    raise Fault("PROTOCOL_MISMATCH", "MCP server negotiated an unpinned version")
                if args.get("contract"):
                    page = await client.list_tools()
                    matched = next((t for t in page.tools if t.name == args["operation"]), None)
                    if not matched or digest(matched.inputSchema) != digest(args["contract"]["input_schema"]):
                        raise Fault("REMOTE_SCHEMA_DRIFT", "Live tool schema differs from the reviewed effect")
                result = await client.call_tool(args["operation"], args["arguments"])
                return {"exit_code": 1 if result.isError else 0, "response": result.model_dump(mode="json")}
    if connection["kind"] == "mcp" and protocol == "2026-07-28":
        # Separate stateless adapter: never send legacy initialize/session fields.
        params = {"name": args["operation"], "arguments": args["arguments"]}
        if args.get("contract"):
            info = await discover(connection)
            matched = next((t for t in info["tools"] if t["name"] == args["operation"]), None)
            if not matched or digest(matched["inputSchema"]) != digest(args["contract"]["input_schema"]):
                raise Fault("REMOTE_SCHEMA_DRIFT", "Live tool schema differs from the reviewed effect")
        if connection.get("tasks_extension"):
            discovered = await rpc(connection, "server/discover", {})
            if "io.modelcontextprotocol/tasks" not in discovered.get("capabilities", {}).get("extensions", {}):
                raise Fault("EXTENSION_MISMATCH", "Remote server did not advertise the pinned Tasks extension")
            params["_meta"] = {
                "io.modelcontextprotocol/clientCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}
            }
        result = await rpc(connection, "tools/call", params, action_id)
        if (result.get("resultType") == "task" or result.get("taskId")) and not connection.get("tasks_extension"):
            raise Fault("UNNEGOTIATED_TASK", "Remote task was returned without extension negotiation")
        if result.get("resultType") == "input_required" and not result.get("taskId"):
            raise Fault("REMOTE_INPUT_UNSUPPORTED", "Synchronous remote input requires explicit reconciliation; no request will be repeated")
        return remote_result(connection, result)
    if connection["kind"] == "a2a" and protocol == "1.0":
        if args["operation"] != "SendMessage":
            raise Fault("A2A_OPERATION", "A2A creation must use SendMessage")
        return remote_result(connection, await rpc(connection, "SendMessage", args["arguments"], action_id))
    if connection["kind"] == "a2a" and protocol == "0.3.0":
        from a2a.client import A2AClient
        from a2a.types import MessageSendParams, SendMessageRequest

        async with safe_client(timeout=30, headers=headers(connection)) as http:
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
    def prepare_poll():
        with db.transaction(tenant) as s:
            run = db.fence(s, tenant, run_id, owner, epoch)
            action = db.get(s, db.Action, tenant, action_id)
            connection, receipt = action.args["connection"], action.receipt
            task_id = receipt["remote_task_id"]
            # Cancellation is a separate durable control effect, acknowledged before polling.
            cancel_key = "remote-cancel-" + action_id
            cancel_job = s.get(db.Outbox, (tenant, cancel_key), with_for_update=True)
            if run.cancel_requested and not cancel_job and not receipt.get("cancel_sent"):
                cancel_job = db.Outbox(tenant_id=tenant, id=cancel_key, status="pending",
                                      data={"action_id": action_id, "kind": "cancel", "task_id": task_id})
                s.add(cancel_job)
            if cancel_job and cancel_job.status == "dispatching":
                cancel_job.status = "unknown"
                db.emit(s, run, "REMOTE_CANCEL_UNKNOWN", "Interrupted cancellation is not repeated; query task status", outbox_id=cancel_key)
            cancel = bool(cancel_job and cancel_job.status == "pending" and run.cancel_requested)
            uncertain_inputs = [
                o
                for o in db.rows(s, db.Outbox, tenant)
                if o.status in {"dispatching", "unknown"} and o.data.get("action_id") == action_id and o.data.get("kind") != "cancel"
            ]
            if uncertain_inputs:
                for job in uncertain_inputs:
                    job.status = "unknown"
                action.status = "UNKNOWN"
                db.emit(s, run, "REMOTE_INPUT_UNKNOWN", "Interrupted remote input delivery requires reconciliation")
                return None
            input_jobs = [
                o for o in db.rows(s, db.Outbox, tenant) if o.status == "pending" and o.data.get("action_id") == action_id and o.data.get("kind") != "cancel"
            ]
            approved_inputs = []
            for job in input_jobs:
                auth = service.authorization(s, run, "external.write")
                stale = job.data.get("input_digest") and job.data["input_digest"] != digest(receipt.get("response", {}).get("inputRequests", {}))
                if run.cancel_requested or job.data["policy_epoch"] != auth.data["epoch"] or stale:
                    job.status = "cancelled"
                elif run.pause_requested:
                    continue
                else:
                    job.status = "dispatching"
                    approved_inputs.append((job.id, job.data["params"]))
            deadline = run.created_at + timedelta(seconds=run.state["budget"]["max_wall_seconds"])
            if db.clock(s) > deadline:
                action.status = "UNKNOWN"
                db.emit(s, run, "REMOTE_DEADLINE", "Remote task exceeded deadline; explicit reconciliation required")
                return None
            if cancel:
                cancel_job.status = "dispatching"
                db.emit(s, run, "REMOTE_CANCEL_DISPATCHING", "Cancellation intent persisted before delivery", outbox_id=cancel_key)
        return approved_inputs, cancel, connection, receipt, task_id, run.state, action.tool, cancel_key
    prepared = await asyncio.to_thread(prepare_poll)
    if prepared is None:
        return
    approved_inputs, cancel, connection, receipt, task_id, state, tool, cancel_key = prepared
    is_mcp = connection["kind"] == "mcp"
    is_v1 = connection["protocol"] == "1.0"
    params = {"taskId" if is_mcp else "id": task_id}
    cancel_sent = receipt.get("cancel_sent", False)
    try:
        for job_id, input_params in approved_inputs:
            await rpc(connection, "tasks/update", input_params, job_id)
            def acknowledge_input():
                with db.transaction(tenant) as s:
                    run = db.fence(s, tenant, run_id, owner, epoch)
                    job = db.get(s, db.Outbox, tenant, job_id, True)
                    job.status = "sent"
                    db.emit(s, run, "REMOTE_INPUT_DELIVERED", "Reviewed remote input acknowledged", outbox_id=job_id)
            await asyncio.to_thread(acknowledge_input)
        if cancel:
            await rpc(connection, "tasks/cancel" if is_mcp or not is_v1 else "CancelTask", params, cancel_key)
            cancel_sent = True
            def acknowledge_cancel():
                with db.transaction(tenant) as s:
                    run = db.fence(s, tenant, run_id, owner, epoch)
                    job = db.get(s, db.Outbox, tenant, cancel_key, True)
                    job.status = "sent"
                    action = db.get(s, db.Action, tenant, action_id, True)
                    action.receipt = {**action.receipt, "cancel_sent": True}
                    db.emit(s, run, "REMOTE_CANCEL_ACKNOWLEDGED", "Cancellation accepted; terminal task confirmation still required", outbox_id=cancel_key)
            await asyncio.to_thread(acknowledge_cancel)
        result = await rpc(connection, "tasks/get" if is_mcp or not is_v1 else "GetTask", params)
        result = remote_result(connection, result)
        if result.get("remote_task_id") != task_id:
            raise Fault("REMOTE_TASK_MISMATCH", "Status query returned a different task")
        result["cancel_sent"] = cancel_sent
        from . import observation, resources

        result = observation.envelope(result, tool, state)
        result_ref = await asyncio.to_thread(resources.put, tenant, run_id, result)
    except Exception:
        def record_poll_failure():
            with db.transaction(tenant) as s:
                run = db.fence(s, tenant, run_id, owner, epoch)
                if cancel:
                    job = db.get(s, db.Outbox, tenant, cancel_key, True)
                    if job.status == "dispatching":
                        job.status = "unknown"
                        db.emit(s, run, "REMOTE_CANCEL_UNKNOWN", "Cancellation may have been delivered; no blind retry", outbox_id=cancel_key)
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
                    return None
                db.schedule(s, run, 10)
                db.emit(s, run, "REMOTE_POLL_DELAYED", "Remote status query failed; no effect was repeated")
        await asyncio.to_thread(record_poll_failure)
        return
    def commit_poll():
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
            if not result.get("pending"):
                control = s.get(db.Outbox, (tenant, cancel_key), with_for_update=True)
                if control and control.status in {"unknown", "dispatching", "pending"}:
                    control.status = "settled"
                    control.data = {**control.data, "terminal_status": result.get("remote_status")}
            run.status, run.wait_reason = (("CANCELLING", "TOOL" if result.get("pending") else None) if run.cancel_requested
                                           else ("PAUSED", None) if run.pause_requested
                                           else ("WAITING", "TOOL") if result.get("pending") else ("ACTIVE", None))
            if result.get("remote_status") in {"input_required", "auth_required"} and not run.cancel_requested and not run.pause_requested:
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
    await asyncio.to_thread(commit_poll)


def callback_signature(secret, tenant, provider, message_id, timestamp, body):
    from .domain import canonical

    signed = canonical({"version": 1, "tenant": tenant, "connection": provider, "message_id": message_id,
                        "timestamp": timestamp, "body_sha256": digest(body)})
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def accept_callback(s, tenant, provider, message_id, timestamp, body, signature):
    connection = db.get(s, db.ToolVersion, tenant, provider)
    if connection.status != "active" or not connection.data.get("callback_key_ref"):
        raise Fault("CALLBACK_DISABLED", "Connection callback key is not configured", 503)
    secret = credentials(connection.data, connection.data["callback_key_ref"]).get("callback_secret")
    if not secret:
        raise Fault("CALLBACK_DISABLED", "Connection callback secret is unavailable", 503)
    if not message_id or len(message_id) > 200 or len(body) > 65536:
        raise Fault("CALLBACK_LIMIT", "Callback ID or payload exceeds its bound", 413)
    if abs(db.clock(s).timestamp() - timestamp) > 300:
        raise Fault("CALLBACK_EXPIRED", "Callback timestamp outside replay window", 401)
    expected = callback_signature(secret, tenant, provider, message_id, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise Fault("CALLBACK_SIGNATURE", "Invalid callback signature", 401)
    key = digest({"provider": provider, "message_id": message_id})[7:]
    s.execute(db.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": tenant + ":" + key})
    previous = s.get(db.Inbox, (tenant, key))
    if previous:
        if previous.data.get("body_digest") != digest(body):
            raise Fault("CALLBACK_CONFLICT", "Message ID was reused with different content")
        return {"duplicate": True}
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("action_id"), str):
            raise ValueError()
    except ValueError:
        raise Fault("CALLBACK_PAYLOAD", "Callback requires a JSON object with action_id", 422) from None
    action = db.get(s, db.Action, tenant, payload["action_id"])
    if action.args.get("connection_id") != provider:
        raise Fault("CALLBACK_PROVIDER", "Callback is not associated with this provider", 403)
    # Inbox is a durable observation. It cannot promote arbitrary remote output into verified success.
    s.add(db.Inbox(tenant_id=tenant, id=key, status="received", data={"provider": provider, "message_id": message_id,
                                                                 "payload": payload, "body_digest": digest(body)}))
    run = db.get(s, db.Run, tenant, action.run_id, True)
    db.emit(s, run, "CALLBACK_RECEIVED", "Authenticated remote observation received", action_id=action.id, inbox_id=key)
    if run.status in {"WAITING", "CANCELLING"}:
        db.schedule(s, run)
    return {"duplicate": False}


def consume_inbox(s, run):
    actions = {a.id: a for a in db.rows(s, db.Action, run.tenant_id, run_id=run.id)}
    for entry in db.rows(s, db.Inbox, run.tenant_id):
        if entry.status != "received" or entry.data.get("payload", {}).get("action_id") not in actions:
            continue
        entry.status = "consumed"
        entry.data = {**entry.data, "consumed_at": db.clock(s).isoformat()}
        # Callbacks are wake-up hints. Task state comes from the bound provider query,
        # and repository success still requires the independent local verifier.
        db.emit(s, run, "CALLBACK_CONSUMED", "Remote hint consumed once; querying authoritative task state", inbox_id=entry.id)


def artifact_links(receipt):
    links = []
    def visit(value):
        if isinstance(value, dict):
            url = value.get("url") or value.get("uri")
            if isinstance(url, str) and url.startswith("https://") and url not in links:
                links.append(url)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    response = receipt.get("response", {})
    visit(response.get("artifacts", []) if "artifacts" in response else response.get("content", []))
    return links
