from decimal import ROUND_CEILING, Decimal

from .config import settings
from .domain import Decision, Fault


def estimate_cost(input_tokens, output_tokens):
    # USD / million tokens equals micro-USD / token.
    return int(
        (
            Decimal(str(settings.input_price)) * input_tokens + Decimal(str(settings.output_price)) * output_tokens
        ).to_integral_value(rounding=ROUND_CEILING)
    )


async def generate(messages, model_id):
    if settings.model_provider == "unconfigured" or not settings.model_api_key or not model_id:
        raise Fault("MODEL_NOT_CONFIGURED", "Configure model provider, exact model ID and API key in .env", 503)
    if settings.input_price <= 0 or settings.output_price <= 0:
        raise Fault("PRICING_REQUIRED", "Configure provider prices before making billable calls", 503)
    if settings.model_provider == "openai":
        from openai import AsyncOpenAI

        async with AsyncOpenAI(
            api_key=settings.model_api_key, base_url=settings.model_base_url or None, timeout=90, max_retries=0
        ) as client:
            response = await client.chat.completions.create(
                model=model_id,
                messages=messages,
                max_completion_tokens=settings.max_output,
                response_format={"type": "json_object"},
            )
            raw = response.model_dump(mode="json")
            content = response.choices[0].message.content or ""
            usage = (
                {"input_tokens": response.usage.prompt_tokens, "output_tokens": response.usage.completion_tokens}
                if response.usage
                else None
            )
            request_id = getattr(response, "_request_id", None)
    elif settings.model_provider == "anthropic":
        from anthropic import AsyncAnthropic

        async with AsyncAnthropic(
            api_key=settings.model_api_key, base_url=settings.model_base_url or None, timeout=90, max_retries=0
        ) as client:
            response = await client.messages.create(
                model=model_id, max_tokens=settings.max_output, system=messages[0]["content"], messages=messages[1:]
            )
            raw = response.model_dump(mode="json")
            content = "".join(b.text for b in response.content if b.type == "text")
            usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
            request_id = response.id
    else:
        raise Fault("MODEL_PROVIDER", "Supported providers are openai and anthropic", 422)
    return content, usage, raw, request_id


def parse_decision(content):
    return Decision.model_validate_json(content)
