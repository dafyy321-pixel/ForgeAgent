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

A later attempt implemented the repair but called `request_input` with a completion
report. Native control-tool descriptions now explain that `request_input` pauses
for a concrete missing requirement/authority question, while `propose_completion`
submits ready work to the independent verifier. The runtime still honors the
selected control; it never silently converts a request for user input to success.

Concurrent test jobs also demonstrated actual `REA_BACKPRESSURE`. This now maps
to a supported environment failure, while `REA_TIMEOUT` maps to an observer-tool
failure. Both may retry only the same scoped, read-only observation within the
existing recovery quotas. An unchanged repeated failure still pauses the task;
supervision, integrity, authority and unknown failures remain fail-closed.

The follow-up full backend run passed **374 tests, with 8 skips**, in 355.14
seconds. Two subsequently added durable REA recovery-policy cases passed
separately in 2.26 seconds. Skips cover explicit opt-in infrastructure tests and
Windows symlink support; the billable scenarios are run separately below.
The final source also passes Ruff, backend type checking and Git whitespace checks.

The live fixture includes public standard-library tests and a separate sealed
Unicode acceptance suite. Its success assertion allows bounded earlier failed
attempts and requires at least one independently verified successful recovery,
no active recovery intents, fully applied provider receipts and zero outstanding
budget reservations. An earlier test assertion incorrectly required the first
attempt to succeed even though the real task ultimately passed; that assertion
was corrected without relaxing final acceptance or recovery quotas.

Final expanded live test: **passed in 118.36 seconds**. Run
`108af919-cd00-4ced-a2dc-75f07f204312` used the exact `gpt-6-luna/high`
Responses profile with 10 fully accounted provider calls. Its local pinned-rate
ledger settled **$0.008974**, with **zero reserved balance**. Protected acceptance
went from **FAIL to PASS**, and the recovery attempt finished **SUCCEEDED**.
REA analysis and public unittest execution succeeded. One intermediate `rea.view`
action failed; the task subsequently completed using further public observations.
This is an eventual recovery result, not a claim that every proposed tool action
succeeded or that one sample establishes a general model success rate.

The initial diagnosis correctly retained `root_class=unknown` with a hypothesis
of implementation/target/specification error. Final behavior was checked by a
separate sealed test suite covering trimmed queries, case-insensitive matches,
Chinese text, full-width Unicode normalization, empty queries, absent matches,
stable ordering and preservation of the input list. Generated code ran only in
the network-disabled, non-root sandbox. Tested image digest:
`sha256:429174cc0b6de4daefb2a87e919e22f89efa551cd82b47077f2fdc022ac5bbcd`.
Local evidence: `.forge/live-model/runtime-facts/live-recovery.json` and
`.forge/live-model/live-recovery-junit.xml`. This uses local Docker/runc and static
REA; production gVisor and the remaining release infrastructure remain separate
gates.

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

The expanded, billable recovery scenario is opt-in separately:

```
FORGE_TEST_LIVE_RECOVERY=1
FORGE_TEST_DOCKER=1
python -m pytest tests/backend/test_live_recovery.py -q
```

Set `GITHUB_SHA` and `FORGE_RELEASE_FACTS_DIR` as above. It uses a real fixed REA
reference snapshot, real provider tools, PostgreSQL records and protected Python
unittest acceptance in a network-disabled Docker sandbox. The initial unchanged
baseline failure is deliberately seeded; no subsequent model decision or verifier
result is simulated. Run live REA cases serially with other REA integration tests
to avoid contending for the single analyzer slot. Evidence identifies the base
commit and content hashes of changed source/test files, rather than representing
uncommitted changes as a clean commit.
