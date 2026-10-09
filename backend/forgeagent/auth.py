from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Request

from .config import settings
from .domain import Fault


@dataclass(frozen=True)
class Identity:
    tenant: str
    actor: str
    admin: bool = False
    expires_at: int | None = None


@lru_cache
def keys(url):
    return jwt.PyJWKClient(url)


def identity(request: Request):
    if settings.auth_mode == "local":
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "testclient"}:
            raise Fault("LOCAL_ONLY", "Local authentication requires a loopback connection", 403)
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if host == "testclient":
            allowed_hosts.add("testserver")
        if request.url.hostname not in allowed_hosts:
            raise Fault("HOST_DENIED", "Local authentication requires a loopback Host header", 403)
        # Cross-origin browsers cannot use the loopback service as a confused deputy.
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise Fault("ORIGIN_DENIED", "Cross-site browser access is not permitted", 403)
        if origin and origin not in {o.strip() for o in settings.trusted_origins.split(",")}:
            raise Fault("ORIGIN_DENIED", "Origin is not permitted", 403)
        return Identity("local", "local-user", True)
    if settings.auth_mode != "oidc" or not settings.oidc_jwks_url:
        raise Fault("AUTH_CONFIG", "OIDC is not configured", 503)
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    try:
        signing_key = keys(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "sub", "iss", "aud", "tenant_id"]},
        )
        if not isinstance(claims["tenant_id"], str) or not isinstance(claims["sub"], str):
            raise jwt.InvalidTokenError("Invalid identity claims")
        roles = claims.get("roles", [])
        return Identity(claims["tenant_id"], claims["sub"], isinstance(roles, list) and "forge-admin" in roles, claims["exp"])
    except jwt.PyJWTError:
        raise Fault("UNAUTHORIZED", "Invalid or expired access token", 401)


def require_admin(actor):
    if not actor.admin:
        raise Fault("FORBIDDEN", "Administrator role is required", 403)


PROJECT_OPERATIONS = {"read", "create", "control", "edit", "fork", "approve", "reconcile", "evaluate", "acceptance.read"}


def project_access(s, actor, project_id, operation):
    from . import db

    if actor.admin:
        return True
    auth = s.get(db.Authorization, (actor.tenant, actor.actor))
    grants = auth.data.get("project_permissions", {}) if auth else {}
    return operation in grants.get(project_id, []) or operation in grants.get("*", [])


def run_access(s, actor, run, operation="read"):
    if actor.tenant != run.tenant_id:
        return False
    if project_access(s, actor, run.project_id, operation):
        return True
    return run.actor == actor.actor and operation in {"read", "control", "edit", "fork"}


def require_run_access(s, actor, run, operation="read"):
    if not run_access(s, actor, run, operation):
        raise Fault("FORBIDDEN", "This operation requires ownership or an explicit project permission", 403)


def authorized_identity(request: Request, actor: Identity = Depends(identity)):
    """Check resource access before any endpoint can read or mutate a run-owned record."""
    from . import db

    parts = request.url.path.strip("/").split("/")
    with db.transaction(actor.tenant) as s:
        authorization = s.get(db.Authorization, (actor.tenant, actor.actor))
        if (authorization is not None and authorization.status != "active") or (settings.auth_mode == "oidc" and authorization is None):
            raise Fault("FORBIDDEN", "Workspace identity is not active", 403)
        if len(parts) < 3 or parts[0] != "v1":
            return actor
        resource, resource_id = parts[1:3]
        operation = "read" if request.method == "GET" else "edit"
        run = None
        if resource == "runs":
            run = db.get(s, db.Run, actor.tenant, resource_id)
            action = parts[3] if len(parts) > 3 else ""
            if action in {"pause", "resume", "cancel", "verify", "recheck"}:
                operation = "control"
            elif action == "fork":
                operation = "fork"
            elif action == "budget" and request.method != "GET":
                operation = "reconcile"
        elif resource in {"actions", "artifacts", "approvals", "outbox"}:
            cls = {"actions": db.Action, "artifacts": db.Artifact, "approvals": db.Approval, "outbox": db.Outbox}[resource]
            item = db.get(s, cls, actor.tenant, resource_id)
            if resource == "approvals":
                item = db.get(s, db.Action, actor.tenant, item.action_id)
                operation = "approve"
            elif resource == "outbox":
                item = db.get(s, db.Action, actor.tenant, item.data["action_id"])
                operation = "reconcile"
            elif parts[-1] == "reconcile":
                operation = "reconcile"
            run = db.get(s, db.Run, actor.tenant, item.run_id)
        if run:
            require_run_access(s, actor, run, operation)
    return actor
