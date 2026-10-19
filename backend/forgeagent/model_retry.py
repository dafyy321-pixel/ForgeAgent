"""Retry only a known rejected request; preserve reservations after uncertain dispatch."""

from datetime import UTC
from email.utils import parsedate_to_datetime

from .domain import Fault

PREFLIGHT = {"MODEL_NOT_CONFIGURED", "PRICING_REQUIRED", "MODEL_PROVIDER", "MODEL_CAPABILITY", "MODEL_SCHEMA_UNSUPPORTED"}


def request_id(exc):
    """Extract only the provider's opaque request identifier, never its body."""
    value = getattr(exc, "request_id", None)
    if value:
        return str(value)[:200]
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    return str(headers.get("x-request-id") or headers.get("request-id") or "")[:200] or None


def error_type(exc):
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connection" in name or "network" in name:
        return "connection"
    if "ratelimit" in name or getattr(exc, "status_code", None) == 429:
        return "rate_limit"
    if getattr(exc, "status_code", None) in {400, 401, 403, 404, 422}:
        return "provider_rejected"
    return "provider_error"


def classify(exc, time, attempt):
    status = getattr(exc, "status_code", None)
    known = status in {400, 401, 403, 404, 422, 429} or (isinstance(exc, Fault) and exc.code in PREFLIGHT)
    delay = min(300, 2 ** min(attempt, 8))
    header = getattr(getattr(exc, "response", None), "headers", {}).get("retry-after")
    if header:
        try:
            delay = max(delay, float(header))
        except ValueError:
            try:
                when = parsedate_to_datetime(header)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                delay = max(delay, (when - time).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    if not 0 <= delay <= 86400:
        delay = 86400
    return {"known_unbilled": known, "retryable": known and status == 429,
            "delay_seconds": delay, "requires_query": not known,
            "category": "rate_limit" if status == 429 else "rejected" if known else "uncertain_dispatch"}
