"""Retry only a known rejected request; preserve reservations after uncertain dispatch."""

from datetime import UTC
from email.utils import parsedate_to_datetime

from .domain import Fault

PREFLIGHT = {"MODEL_NOT_CONFIGURED", "PRICING_REQUIRED", "MODEL_PROVIDER", "MODEL_CAPABILITY", "MODEL_SCHEMA_UNSUPPORTED"}


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
