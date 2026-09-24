from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Request

from .config import settings
from .domain import Fault


@dataclass(frozen=True)
class Identity:
    tenant: str
    actor: str
    admin: bool = False


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
        return Identity(claims["tenant_id"], claims["sub"], "forge-admin" in claims.get("roles", []))
    except jwt.PyJWTError:
        raise Fault("UNAUTHORIZED", "Invalid or expired access token", 401)


def require_admin(actor):
    if not actor.admin:
        raise Fault("FORBIDDEN", "Administrator role is required", 403)
