# RongKai gateway / GPT-6 Luna live verification

## Configuration and price evidence

Test endpoint: `https://sub.rongkaitech.com/v1`; exact model: `gpt-6-luna`;
Responses API, native tools, `reasoning.effort=high`, explicit SSE streaming.
Credentials live only in the ignored local `.env`.

The gateway returns SSE even with `stream:false`. Use
`FORGE_MODEL_STREAM=true`. Its model list omits `gpt-6-luna`, but actual requests
accept the exact model and the terminal response echoes `high`.
Provider token counting is unavailable for this test profile, so
`FORGE_MODEL_TOKEN_COUNT=false` uses the existing conservative UTF-8 bound.
For the REA recovery scenario, `FORGE_CONTEXT_WINDOW=65536` admits the full
request including native tool schemas. The default 32768 conservatively rejects
this larger catalog before dispatch. The [model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
lists a 1,050,000-token window; the local test limit remains well below that and
below the 272K long-context pricing threshold.

[OpenAI's public price table](https://developers.openai.com/api/docs/pricing)
lists these Standard short-context USD rates per million tokens:

| Category | Rate used locally | Evidence |
| --- | ---: | --- |
| Input | $0.10 | Official price and gateway aggregate corroboration |
| Output, including reasoning | $0.50 | Official price and gateway aggregate corroboration |
| Cache read | $0.01 | Official price and gateway aggregate corroboration |
| Cache write | Unconfigured | Gateway sample has zero writes |

Authenticated, read-only `/v1/usage` reports USD. The `gpt-6-luna` aggregate
observed on 2026-10-14 satisfies, exactly:

```
(9,382,858 * 0.10 + 71,991 * 0.50 + 14,949,120 * 0.01) / 1,000,000
= $1.12377250
= gateway actual_cost
```

Here the gateway aggregate's ordinary input count excludes cached reads.
This historical match corroborates the configured rates; it is not a published
contractual gateway tariff or a guarantee of future account/group multipliers.
The gateway's model plaza API requires website authentication and rejects API
keys. No administrative endpoints were used to obtain pricing. Local task costs
use the pinned rates and round each call upward to microdollars; they are not an
invoice. The gateway's official-price cache-write rate ($0.125) remains unset
locally because no gateway cache-write sample confirmed it.

## Adapter checks

The model profile pins reasoning effort and transport. Unsupported streaming
protocols fail before dispatch. The existing SDK parses SSE; terminal responses
retain the original native call IDs, status and usage. Missing or repeated
terminal responses remain uncertain dispatches and cannot trigger an automatic
billable retry. Incomplete and failed responses retain accounting but cannot
authorize tool effects.

The SDK adds `cache_write_tokens:null` when the provider omits that optional
counter. With no separate cache-write tariff this follows the existing zero-write
interpretation; when a write tariff is configured an explicit count is required.
Tests use the real SDK parser over an HTTP mock transport, without external calls.

Initial checks: 36 model protocol/stream tests passed; Ruff and backend type
checking passed. The real configured-model release test passed in 58.06 seconds:
six provider calls, all receipts applied, all budget entries settled, zero
reserved balance, and $0.002668 charged to the local pinned-rate task ledger.
The runtime-lab verifier checks the repair structurally with AST; this result
does not by itself establish container isolation or the complete production
release gates.

The subsequent backend regression passed **373 tests, with 7 opt-in/platform
skips**, in 444.66 seconds, including the real Docker isolation, timeout and
protected-acceptance tests and the installed REA CLI integration. Browser tests
passed **20/20** in Edge after starting the API and a worker limited to the
`local` test tenant. The first browser attempt ran before those services were
ready and failed; no test timeouts or assertions were relaxed. ESLint, Prettier,
OpenAPI type consistency, the frontend build, Ruff and backend type checks passed.

A first expanded live REA recovery run demonstrated a safe stop rather than
successful repair: the model repeated analysis of an unchanged reference, and
Windows reported a temporary-directory sharing violation during cleanup. The
failed run and its unknown-root-cause diagnosis remain in the local audit.
Windows analyzer jobs now explicitly terminate their process family and confirm
that its active-process count reaches zero before returning to directory cleanup.
A regression check covers normal parent exit with surviving descendants and
immediate directory removal, alongside the existing timeout/cancellation checks.
After this change the REA suite passed 7 tests with one platform skip.
The expanded live task also explicitly tells the model to consume successful
analysis/read receipts and proceed to writing, rather than repeat unchanged work.

Local sanitized evidence is in `.forge/live-model/`. Account-wide usage and
downloaded frontend assets remain ignored and are not release artifacts.

## Reproduction

Set the local `.env` profile above and the three corroborated rates, then run the
existing opt-in test with `FORGE_TEST_RELEASE=1`, `GITHUB_SHA` set to the tested
commit, and `FORGE_RELEASE_FACTS_DIR` set to a new evidence directory:

```
python -m pytest tests/backend/test_release_integration.py::test_live_model_completes_with_settled_provider_usage -q
```

For ordinary regression tests, use an unconfigured model profile with default
reasoning/stream settings so developer credentials cannot affect fixtures. Real
Docker tests additionally require `FORGE_TEST_DOCKER=1` and a built sandbox image.
The full release suite also needs independent gVisor-manager, protocol, S3 and
restore infrastructure; running only the model test does not certify those gates.
