"""Normalized, non-overlapping token categories and conservative request bounds."""

from decimal import ROUND_CEILING, Decimal

from .config import settings
from .domain import Fault


def rate_card():
    return {"input_price": settings.input_price, "output_price": settings.output_price,
            "cached_input_price": settings.cached_input_price, "cache_write_price": settings.cache_write_price}


def tokens(usage):
    if not usage:
        return 0
    return usage["input_tokens"] + usage["output_tokens"]


def cost(usage, rates=None, upper=False):
    rates = rates or rate_card()
    try:
        for key in ("input_price", "output_price", "cached_input_price", "cache_write_price"):
            value = rates.get(key)
            if value is None and key in {"cached_input_price", "cache_write_price"}:
                continue
            price = Decimal(str(value))
            if not price.is_finite() or price < 0:
                raise ValueError("Invalid tariff")
    except Exception as exc:
        raise Fault("UNKNOWN_PRICING", "Tariffs must be finite nonnegative amounts") from exc
    incoming, outgoing = usage["input_tokens"], usage["output_tokens"]
    cached, written = usage.get("cached_input_tokens", 0), usage.get("cache_write_tokens", 0)
    reasoning = usage.get("reasoning_tokens", 0)
    if any(type(n) is not int or n < 0 for n in [incoming, outgoing, cached, written, reasoning]):
        raise Fault("UNKNOWN_USAGE", "Provider token counts must be nonnegative integers")
    if cached + written > incoming or reasoning > outgoing:
        raise Fault("UNKNOWN_USAGE", "Provider token categories exceed their totals")
    if upper:
        highest = max(Decimal(str(v)) for k, v in rates.items() if k.endswith("_price") and v is not None)
        value = highest * incoming + Decimal(str(rates["output_price"])) * outgoing
    else:
        if (cached and rates.get("cached_input_price") is None) or (written and rates.get("cache_write_price") is None):
            raise Fault("UNKNOWN_PRICING", "Positive cache usage requires a configured price for that category")
        value = (Decimal(str(rates["input_price"])) * (incoming - cached - written)
                 + Decimal(str(rates.get("cached_input_price") or 0)) * cached
                 + Decimal(str(rates.get("cache_write_price") or 0)) * written
                 + Decimal(str(rates["output_price"])) * outgoing)
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def normalize(raw, provider, protocol="responses", rates=None):
    """Missing billing counters stay unknown; reasoning is a subset of output."""
    if not isinstance(raw, dict):
        return None
    rates = rates or rate_card()
    try:
        if provider == "anthropic":
            ordinary, outgoing = raw["input_tokens"], raw["output_tokens"]
            cached, written = raw.get("cache_read_input_tokens", 0), raw.get("cache_creation_input_tokens", 0)
            if any(type(n) is not int or n < 0 for n in [ordinary, outgoing, cached, written]):
                return None
            incoming = ordinary + cached + written
            reasoning = 0
        else:
            incoming = raw["prompt_tokens" if protocol == "chat" else "input_tokens"]
            outgoing = raw["completion_tokens" if protocol == "chat" else "output_tokens"]
            details = raw["prompt_tokens_details" if protocol == "chat" else "input_tokens_details"]
            cached = details["cached_tokens"]
            # A configured separate write tariff requires explicit write accounting.
            written = details["cache_write_tokens"] if rates.get("cache_write_price") is not None else details.get("cache_write_tokens", 0)
            reasoning = (raw.get("completion_tokens_details" if protocol == "chat" else "output_tokens_details") or {}).get("reasoning_tokens", 0)
            if raw["total_tokens"] != incoming + outgoing:
                return None
        usage = {"input_tokens": incoming, "output_tokens": outgoing, "cached_input_tokens": cached,
                 "cache_write_tokens": written, "reasoning_tokens": reasoning}
        # Validate categories without requiring tariff configuration yet.
        cost(usage, {"input_price": 1, "output_price": 1, "cached_input_price": 1, "cache_write_price": 1})
        return usage
    except (KeyError, TypeError, Fault):
        return None
