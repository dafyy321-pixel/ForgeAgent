"""Real local tokenizer estimates; provider counts remain the request authority."""

from functools import lru_cache

import tiktoken

from .config import settings
from .domain import Fault, canonical
from .telemetry import observed


@lru_cache(maxsize=8)
def encoder(name):
    try:
        return tiktoken.get_encoding(name)
    except (ValueError, OSError) as exc:
        raise Fault("TOKENIZER_UNAVAILABLE", "Configured tokenizer cannot be loaded; configure or prewarm its cache") from exc


@observed("context.tokenization")
def count(value):
    text = value if isinstance(value, str) else canonical(value).decode("utf-8")
    return len(encoder(settings.model_tokenizer).encode(text, disallowed_special=()))


def request_estimate(request):
    # Provider protocol framing is not public BPE text. Record this estimate as such;
    # the provider's count endpoint validates the actual request before dispatch.
    return count(request) + 256
