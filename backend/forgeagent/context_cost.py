"""Report observed token costs separately from local compression estimates."""

from . import billing
from .domain import Fault


def report(calls, manifests):
    observed, unknown, cached, written, spent, uncached = 0, 0, 0, 0, 0, 0
    for call in calls:
        if call.get("fixture"):
            continue
        usage, rates = call.get("usage"), call.get("rate_card")
        if not usage or not rates:
            unknown += 1
            continue
        try:
            actual = billing.cost(usage, rates)
            ordinary = billing.cost({"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}, rates)
        except Fault:
            unknown += 1
            continue
        observed += 1
        spent += actual
        uncached += ordinary
        cached += usage.get("cached_input_tokens", 0)
        written += usage.get("cache_write_tokens", 0)
    local_saved = sum(max(0, m.get("full_local_tokens", 0) - m.get("estimated_tokens_upper_bound", 0)) for m in manifests)
    return {"observed_samples": observed, "unknown_samples": unknown, "cached_tokens": cached, "cache_write_tokens": written,
            "observed_model_cost_micros": spent, "uncached_equivalent_micros": uncached,
            "observed_cache_savings_micros": uncached - spent,
            "local_compression_token_estimate": local_saved, "summary_auxiliary_cost_micros": 0,
            "limitations": ["Cache savings may be negative when write costs exceed read savings.",
                            "Local token reduction is an estimate, not a measured paid counterfactual.",
                            "No API samples means real provider economics remain unverified."]}
