from . import billing
from .config import settings
from .domain import Decision, Fault
from .model_protocol import build_request, normalize_response


def estimate_cost(input_tokens, output_tokens):
    return billing.cost({"input_tokens": input_tokens, "output_tokens": output_tokens}, upper=True)


def openai_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=settings.model_api_key, base_url=settings.model_base_url or None, timeout=90, max_retries=0)


def anthropic_client():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=settings.model_api_key, base_url=settings.model_base_url or None, timeout=90, max_retries=0)


async def count_request(request):
    if not settings.model_token_count:
        from .domain import canonical

        return len(canonical(request["payload"])) + 2048, "utf8_upper_bound"
    try:
        if request["provider"] == "anthropic":
            async with anthropic_client() as client:
                payload = {k: v for k, v in request["payload"].items() if k in {"model", "system", "messages", "tools"}}
                response = await client.messages.count_tokens(**payload)
        elif request["protocol"] == "responses":
            async with openai_client() as client:
                payload = {k: v for k, v in request["payload"].items()
                           if k not in {"max_output_tokens", "store", "service_tier", "include", "prompt_cache_key"}}
                response = await client.responses.input_tokens.count(**payload)
        else:
            from .domain import canonical

            return len(canonical(request["payload"])) + 2048, "chat_utf8_upper_bound"
        count = response.input_tokens
        if type(count) is not int or count < 0:
            raise ValueError("invalid token count")
        return count, "provider_count"
    except Exception as exc:
        raise Fault("TOKEN_COUNT_UNAVAILABLE", "Provider token count failed before billable dispatch", retryable=True) from exc


async def generate(messages, model_id, request=None):
    if settings.model_provider == "unconfigured" or not settings.model_api_key or not model_id:
        raise Fault("MODEL_NOT_CONFIGURED", "Configure model provider, exact model ID and API key in .env", 503)
    if settings.input_price <= 0 or settings.output_price <= 0:
        raise Fault("PRICING_REQUIRED", "Configure provider prices before making billable calls", 503)
    if request is None:
        from .service import TOOLS

        request = build_request(messages, {"capabilities": ["repo.read"], "semantic": {"model_id": model_id}}, TOOLS)
    if settings.model_provider == "openai":
        async with openai_client() as client:
            headers = {"X-Client-Request-Id": request["client_request_id"]} if request.get("client_request_id") else {}
            response = (await client.responses.create(**request["payload"], extra_headers=headers) if request["protocol"] == "responses" else
                        await client.chat.completions.create(**request["payload"], extra_headers=headers))
    elif settings.model_provider == "anthropic":
        async with anthropic_client() as client:
            response = await client.messages.create(**request["payload"])
    else:
        raise Fault("MODEL_PROVIDER", "Supported providers are openai and anthropic", 422)
    raw = response.model_dump(mode="json")
    try:
        content, metadata = normalize_response(raw, request)
    except (TypeError, ValueError, KeyError, IndexError):
        content, metadata = "", {"end_status": "invalid_response", "response_id": raw.get("id")}
    raw["_forge"] = metadata
    usage = billing.normalize(raw.get("usage"), settings.model_provider, request["protocol"], request.get("rate_card"))
    unsupported = request["protocol"] == "responses" and any(i.get("type") not in {"message", "function_call", "reasoning"} for i in raw.get("output", []))
    if unsupported or raw.get("model") != model_id or raw.get("service_tier", "default") != "default":
        usage = None
        metadata["billing_error"] = "Returned model or processing tier differs from the pinned tariff"
    return content, usage, raw, getattr(response, "_request_id", None) or raw.get("id")


def parse_decision(content):
    return Decision.model_validate_json(content)


async def query_billing(request, response_id):
    if request.get("provider") != "openai" or request.get("protocol") != "responses" or not response_id.startswith("resp_"):
        raise Fault("PROVIDER_QUERY_UNAVAILABLE", "This protocol has no queryable original response ID; review provider billing evidence")
    if not settings.model_api_key:
        raise Fault("MODEL_NOT_CONFIGURED", "Configure credentials for the original provider account")
    if (request.get("rate_card") or {}).get("endpoint", settings.model_base_url) != settings.model_base_url:
        raise Fault("MODEL_BINDING_CHANGED", "Use credentials and endpoint for the original provider binding")
    try:
        async with openai_client() as client:
            response = await client.responses.retrieve(response_id)
    except Exception as exc:
        raise Fault("PROVIDER_QUERY_UNAVAILABLE", "Provider could not retrieve this original response; no request was redispatched") from exc
    raw = response.model_dump(mode="json")
    rates = request.get("rate_card")
    usage = billing.normalize(raw.get("usage"), "openai", "responses", rates)
    if (raw.get("id") != response_id or raw.get("model") != request["payload"]["model"]
        or raw.get("service_tier", "default") != "default"
        or any(i.get("type") not in {"message", "function_call", "reasoning"} for i in raw.get("output", []))):
        usage = None
    try:
        actual = billing.cost(usage, rates) if usage else None
    except Fault:
        actual = None
    from .domain import digest, now

    return {"response_id": response_id, "queried_at": now().isoformat(), "status": raw.get("status"),
            "usage": usage, "actual_micros": actual, "evidence_digest": digest(raw),
            "requires_review": True, "limitations": "A read query does not redispatch or settle the original operation."}
