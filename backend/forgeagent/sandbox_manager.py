"""Independent Docker authority; the API and Worker need no Docker socket or long-lived provider credentials."""

import asyncio
import json
import os
import ssl
import time
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated

import httpx
import jwt
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from .config import settings
from .domain import BuildContract, Fault, Strict, canonical, digest, uid
from .manager_receipts import prepare_receipt
from .sandbox import sandbox

app = FastAPI(title="ForgeAgent Sandbox Manager")
active = {}
local_slots = BoundedSemaphore(settings.sandbox_concurrency)


def manager_client(timeout):
    verify = ssl.create_default_context(cafile=settings.sandbox_manager_ca_file) if settings.sandbox_manager_ca_file else True
    return httpx.AsyncClient(timeout=timeout, trust_env=False, verify=verify)


class Execution(Strict):
    operation: str = Field(pattern=r"^[a-z0-9-]{36}$")
    root: str
    argv: list[str] = Field(min_length=1, max_length=100)
    image: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    timeout: int = Field(ge=1, le=300)
    readonly: bool = True
    build: BuildContract | None = None


def token(action, payload, ttl=60):
    if len(settings.sandbox_manager_secret) < 32:
        raise Fault("MANAGER_AUTH_UNCONFIGURED", "Manager signing secret requires at least 32 characters", 503)
    return jwt.encode({"aud": "forge-sandbox-manager", "exp": int(time.time()) + min(ttl, 300),
                       "iat": int(time.time()), "action": action, "digest": digest(payload)},
                      settings.sandbox_manager_secret, algorithm="HS256")


def authorize(authorization, action, payload):
    if len(settings.sandbox_manager_secret) < 32:
        raise Fault("MANAGER_AUTH_UNCONFIGURED", "Manager signing secret is unavailable", 503)
    try:
        claims = jwt.decode(authorization.removeprefix("Bearer "), settings.sandbox_manager_secret,
                            algorithms=["HS256"], audience="forge-sandbox-manager",
                            options={"require": ["exp", "iat", "aud", "action", "digest"]})
    except jwt.PyJWTError as exc:
        raise Fault("MANAGER_AUTH", "Invalid or expired short-lived manager credential", 401) from exc
    if claims["action"] != action or claims["digest"] != digest(payload) or claims["exp"] - claims["iat"] > 300:
        raise Fault("MANAGER_SCOPE", "Credential does not authorize this exact operation", 403)


@app.exception_handler(Fault)
async def fault_handler(request, exc):
    return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)


async def run_execution(body):
    root = Path(body.root)
    base = settings.data_dir.resolve() / "workspaces"
    if (not root.is_dir() or root.resolve() == base or not root.resolve().is_relative_to(base)
        or any(p.is_symlink() or p.is_junction() for p in [root, *root.parents])):
        raise Fault("PATH_DENIED", "Manager only mounts managed workspace directories", 403)
    if settings.sandbox_runtime != "runsc":
        raise Fault("GVISOR_REQUIRED", "Independent manager requires the runsc runtime", 503)
    if not local_slots.acquire(blocking=False):
        raise Fault("SANDBOX_BACKPRESSURE", "Manager capacity is exhausted", 503, retryable=True)
    try:
        return await sandbox.execute_with_slot(root, body.argv, body.image, body.timeout, body.readonly,
                                              body.build.model_dump() if body.build else None, body.operation)
    finally:
        local_slots.release()


@app.post("/execute")
async def execute(body: Execution, authorization: Annotated[str, Header()]):
    payload = body.model_dump()
    authorize(authorization, "execute", payload)
    receipt, previous = await asyncio.to_thread(prepare_receipt, payload)
    if previous is not None:
        return previous
    task = asyncio.create_task(run_execution(body))
    active[body.operation] = task
    try:
        result = await task
        temporary = receipt.with_suffix(".tmp")
        await asyncio.to_thread(temporary.write_bytes, canonical(result))
        await asyncio.to_thread(temporary.replace, receipt)
        return result
    finally:
        active.pop(body.operation, None)


@app.post("/cancel/{operation}")
async def cancel(operation: str, authorization: Annotated[str, Header()]):
    authorize(authorization, "cancel", {"operation": operation})
    if operation in active:
        active[operation].cancel()
        await asyncio.gather(active[operation], return_exceptions=True)
    return {"cancelled": operation}


class CleanupRequest(Strict):
    apply: bool = False
    scope: str = Field(pattern=r"^[a-f0-9]{16}$")


@app.post("/maintenance/containers")
async def containers(body: CleanupRequest, authorization: Annotated[str, Header()]):
    authorize(authorization, "container_gc", body.model_dump())
    return await sandbox.reap(body.apply, body.scope)


async def clean_containers(tenant, apply=False):
    scope = digest(tenant)[7:23]
    if not settings.sandbox_manager_url:
        if settings.auth_mode != "local":
            raise Fault("SANDBOX_MANAGER_REQUIRED", "Shared cleanup requires the independent manager", 503)
        return await sandbox.reap(apply, scope)
    url = settings.sandbox_manager_url.rstrip("/")
    if not url.startswith("https://") and not (settings.auth_mode == "local" and url.startswith("http://127.0.0.1:")):
        raise Fault("MANAGER_TRANSPORT", "Shared manager transport requires HTTPS")
    payload = {"apply": apply, "scope": scope}
    async with manager_client(60) as client:
        response = await client.post(url + "/maintenance/containers", json=payload,
            headers={"Authorization": "Bearer " + token("container_gc", payload)})
        if response.is_error:
            raise Fault("SANDBOX_CLEANUP", "Manager cleanup failed", 503)
        return response.json()


class CredentialRequest(Strict):
    route: str = Field(min_length=1, max_length=100)


class ImageRequest(Strict):
    image: str = Field(min_length=1, max_length=300)


@app.post("/image")
async def image(body: ImageRequest, authorization: Annotated[str, Header()]):
    authorize(authorization, "image", body.model_dump())
    return {"digest": await sandbox.inspect_image(body.image)}


async def remote_image(image):
    url = settings.sandbox_manager_url.rstrip("/")
    if not url.startswith("https://") and not (settings.auth_mode == "local" and url.startswith("http://127.0.0.1:")):
        raise Fault("MANAGER_TRANSPORT", "Shared manager transport requires HTTPS")
    payload = {"image": image}
    async with manager_client(30) as client:
        response = await client.post(url + "/image", json=payload,
            headers={"Authorization": "Bearer " + token("image", payload)})
        if response.is_error:
            raise Fault("SANDBOX_UNAVAILABLE", "Manager could not inspect the pinned image")
        return response.json()["digest"]


@app.post("/credentials/proxy")
async def proxy(body: CredentialRequest, authorization: Annotated[str, Header()]):
    authorize(authorization, "credential_read", body.model_dump())
    route = json.loads(settings.credential_routes).get(body.route)
    if not route or not route["url"].startswith("https://"):
        raise Fault("CREDENTIAL_ROUTE_DENIED", "Credential route is not an approved HTTPS read endpoint", 403)
    secret = os.environ.get(route["token_env"], "")
    if not secret:
        raise Fault("CREDENTIAL_UNAVAILABLE", "Upstream credential is unavailable", 503)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", route["url"], headers={"Authorization": "Bearer " + secret}) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 1024 * 1024:
                    raise Fault("PROXY_RESPONSE_LIMIT", "Credential proxy response exceeds limit", 413)
            return Response(bytes(data).replace(secret.encode(), b"[REDACTED]"), status_code=response.status_code)


async def remote_execute(root, argv, image, timeout, readonly, build):
    url = settings.sandbox_manager_url.rstrip("/")
    if not url.startswith("https://") and not (settings.auth_mode == "local" and url.startswith("http://127.0.0.1:")):
        raise Fault("MANAGER_TRANSPORT", "Shared manager transport requires HTTPS")
    payload = Execution(operation=uid(), root=str(root.resolve()), argv=argv, image=image,
                        timeout=min(timeout, 300), readonly=readonly, build=build).model_dump()
    async with manager_client(timeout + 30) as client:
        try:
            response = await client.post(url + "/execute", json=payload,
                                         headers={"Authorization": "Bearer " + token("execute", payload)})
            if response.is_error:
                error = response.json()
                raise Fault(error.get("code", "MANAGER_FAILED"), error.get("message", "Sandbox manager rejected execution"))
            return response.json()
        except (asyncio.CancelledError, httpx.TimeoutException):
            cancellation = {"operation": payload["operation"]}
            await asyncio.shield(client.post(url + "/cancel/" + payload["operation"],
                headers={"Authorization": "Bearer " + token("cancel", cancellation)}))
            raise
