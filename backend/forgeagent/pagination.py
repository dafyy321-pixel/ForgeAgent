"""Bounded keyset pages with signed cursors bound to identity and filters."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from functools import lru_cache

from sqlalchemy import and_, false, or_, select

from . import db
from .config import settings
from .domain import Fault, canonical, digest


@lru_cache
def secret():
    if len(settings.cursor_secret) >= 32:
        return settings.cursor_secret.encode()
    if settings.auth_mode != "local":
        raise Fault("CURSOR_CONFIG", "Shared deployments require FORGE_CURSOR_SECRET of at least 32 characters", 503)
    path = settings.data_dir.resolve() / "cursor-secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    return path.read_bytes()


def encode(row, scope):
    body = canonical({"v": 1, "scope": digest(scope), "at": row.created_at.isoformat(), "id": row.id})
    signature = hmac.new(secret(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")


def decode(cursor, scope):
    try:
        if len(cursor) > 2000:
            raise ValueError()
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        body, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(hmac.new(secret(), body, hashlib.sha256).digest(), signature):
            raise ValueError()
        value = json.loads(body)
        date = datetime.fromisoformat(value["at"])
        if value["v"] != 1 or value["scope"] != digest(scope) or not date.tzinfo or not isinstance(value["id"], str):
            raise ValueError()
        return date, value["id"]
    except (ValueError, KeyError, TypeError):
        raise Fault("INVALID_CURSOR", "Cursor is invalid for this identity, query or filter", 422) from None


def visible(s, actor, operation="read"):
    if actor.admin:
        return db.Run.tenant_id == actor.tenant
    auth = s.get(db.Authorization, (actor.tenant, actor.actor))
    grants = auth.data.get("project_permissions", {}) if auth else {}
    if operation in grants.get("*", []):
        return db.Run.tenant_id == actor.tenant
    projects = [key for key, values in grants.items() if operation in values]
    return and_(db.Run.tenant_id == actor.tenant,
                or_(db.Run.project_id.in_(projects), db.Run.actor == actor.actor if operation == "read" else false()))


def attention():
    return or_(db.Run.status.in_(["PAUSED", "FAILED"]),
               and_(db.Run.status == "WAITING", db.Run.wait_reason.in_(["APPROVAL", "RECONCILIATION"])))


def runs(s, actor, project=None, status=None, q="", cursor=None, limit=50):
    from .domain import TERMINAL

    if not 1 <= limit <= 100 or len(q) > 500 or (status and status not in TERMINAL | {"QUEUED", "ACTIVE", "WAITING", "PAUSED", "CANCELLING", "active", "attention", "approval", "recovery", "done"}):
        raise Fault("INVALID_PAGE", "Invalid page size or run status", 422)
    scope = [actor.tenant, actor.actor, actor.admin, "runs", project, status, q]
    query = select(db.Run).where(visible(s, actor))
    if project:
        query = query.where(db.Run.project_id == project)
    if status:
        if status == "active":
            query = query.where(db.Run.status.in_(["QUEUED", "ACTIVE", "WAITING", "CANCELLING"]))
        elif status in {"attention", "approval", "recovery"}:
            query = query.where(attention())
            if status != "attention":
                query = query.where(db.Run.wait_reason == "APPROVAL" if status == "approval"
                                    else or_(db.Run.wait_reason.is_(None), db.Run.wait_reason != "APPROVAL"))
        else:
            query = query.where(db.Run.status == ("SUCCEEDED" if status == "done" else status))
    if q:
        query = query.where(or_(db.Run.state["title"].as_string().icontains(q, autoescape=True), db.Run.id.icontains(q, autoescape=True)))
    if cursor:
        date, identity = decode(cursor, scope)
        query = query.where(or_(db.Run.created_at < date, and_(db.Run.created_at == date, db.Run.id < identity)))
    values = list(s.scalars(query.order_by(db.Run.created_at.desc(), db.Run.id.desc()).limit(limit + 1)))
    return values[:limit], encode(values[limit - 1], scope) if len(values) > limit else None


def records(s, actor, cls, query, filters, cursor=None, limit=50):
    if not 1 <= limit <= 100:
        raise Fault("INVALID_PAGE", "Page size must be between 1 and 100", 422)
    scope = [actor.tenant, actor.actor, actor.admin, cls.__tablename__, filters]
    if cursor:
        date, identity = decode(cursor, scope)
        query = query.where(or_(cls.created_at < date, and_(cls.created_at == date, cls.id < identity)))
    values = list(s.scalars(query.order_by(cls.created_at.desc(), cls.id.desc()).limit(limit + 1)))
    return values[:limit], encode(values[limit - 1], scope) if len(values) > limit else None
