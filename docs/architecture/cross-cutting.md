# Cross-cutting concerns

Part of the [Sprout architecture docs](README.md). See the [overview](overview.md) for the architecture and data model these concerns apply to, and [decisions](decisions.md) / [risks](risks.md) for the accompanying decision log and risk register.

## 4.1 Sourcing and provenance
- `Sourced[T]` rejects a non-null value with no `source_url`. `value=None` means "not disclosed" and must render as such, never zero or blank.
- Per-fact `retrieved_at` (UTC) supports the staleness policy; registry facts can lag up to 7 days via the HTTP cache.
- Multiple sources per field coexist, which lets the verifier record disagreement.
- Not provenance-bearing: `category`, `news_item` (source is the feed), free-text fields.
- **[AMBIGUOUS]** No rule for which value wins when a field has several facts. Proposed: highest confidence, then most recent, and surface "sources disagree". Put it in the service layer.
- **[AMBIGUOUS], resolved later:** Same-source re-extraction overwrites; no history. Phase 6 "funding history" needs a `funding_round` table or list, or should be cut. Built: a `funding_round` table, one row per round, each sourced independently; `save_profile` replaces the company's rounds on every save, so there is still no history across runs, only within the latest one.
- SQLite treats NULLs as distinct in UNIQUE constraints; `save_fact` dedupes in application code. Do not rely on the constraint alone.

## 4.2 Agent orchestration
- Deterministic supervisor selects companies and runs workers with `asyncio` under a concurrency limit.
- Per company: enrich, then classify (bulk model), news, then verify (only for funding or stage claims). Classifier and news can run in parallel.
- Models: bulk `claude-haiku-5-5` for extraction/classification; strong `claude-sonnet-5-5` for verification/synthesis; overridable via `SPROUT_BULK_MODEL` / `SPROUT_STRONG_MODEL`. IDs were taken from the `claude-api` skill on 2026-10-06; live Phase 2 calls succeeded with `claude-haiku-5-5` (the default). `claude-sonnet-5-5` (`--strong`) is priced but was not run live. Any unpriced override fails closed in `costs.py`.
- Loop (built in Phase 2, see the [Phase 2 page](phase-2-enrichment.md)): `tool_choice: auto` plus a prompt instruction naming `submit_profile`, explicit effort, thinking tokens in budget, prompt-cached prefix and conversation, `pause_turn` and `server_tool_use` handled, per-run caps of iterations, USD and searches (`max_uses`). No per-run fetch cap and no batch-level cap (see the [DRIFT] note on the Phase 2 page).
- Resume: the unit of work is a company; one transaction per company; fresh `ok` rows are skipped.
- A company failure sets status `failed` and the run continues. Target at least 85% `ok`.

## 4.3 Error handling

| Boundary | Failure | Behaviour |
|---|---|---|
| Companies House / RSS | 429 or 5xx | Retry with backoff and `Retry-After`, then `FetchError` |
| Companies House | 404 | Cached, treated as no result |
| RSS | One feed broken | Reported in `failed_feeds`, exit 1, others continue |
| Web fetch | robots, private IP, content type | `FetchBlockedError`, returned to the model as a tool error |
| Web fetch | robots.txt unreachable | Refuse |
| Web fetch | URL policy violation: scheme, port, credentials, denied name, non-public address (including mapped IPv6 and NAT64), host outside `allow_hosts` (Phase 4, planned) | `FetchBlockedError`, returned to the model as a tool error |
| Web fetch | Response slower than `max_seconds` (Phase 4, planned) | `FetchError`, not retried, returned to the model as a tool error |
| Web fetch | Page larger than the tool-result cap | Truncate to 8,000 characters (`TOOL_RESULT_MAX_CHARS`) with a "[truncated]" marker; `WebFetcher` already caps at 20,000 characters |
| Companies House tool | 404 or invalid number | `None` becomes a "no such company" error string; not retried |
| Server-side search | `max_uses_exceeded`, `unavailable`, rate limit | Arrives as an error result block (the loop's URL extraction tolerates a non-list result); the model continues without search; not an exception. Seen live once (Tractable, search unavailable): honest nulls |
| LLM | `pause_turn` | Resend the history including `server_tool_use` blocks; counts as an iteration |
| LLM | `refusal`, `max_tokens`, `end_turn` with no submission | Stop codes `refused`, `truncated`, `no_submit` (one reminder first); nothing persisted. `refused` was seen live once on Cleo and was transient |
| LLM | SDK `APIError` after SDK retries (429/5xx) | Stop `error`; usage so far logged; the `run_log` row is `status=error` and the CLI continues with the next target |
| LLM | Invalid structured output | One repair attempt (Pydantic errors as compact field paths and messages, not the whole input echoed), else stop `invalid`; nothing persisted |
| LLM | `source_url` not in the seen-URL ledger, funding fact without a `note` | Treated as invalid output: same single repair attempt, and the error lists up to 25 citable URLs. Seen live: invented homepages were rejected and repaired |
| LLM | Iteration or USD cap | Stop with `max_iter` or `budget`; nothing is persisted (no partial profile); `run_log` row keeps stats and cost. **[DRIFT]** the earlier "keep partial facts, mark `partial`" is not implemented. Phase 5 `enrichment_state` will need to decide whether to retry or accept partial results |
| LLM | Sources disagree | Record both, lower confidence, flag |
| Run classification (Phase 3, planned) | Any non-`submitted` stop | `failure_category` set from the fixed taxonomy (see the [Phase 3 page](phase-3-observability.md)) in the same commit as the stats and steps, before `EnrichmentFailed` is raised; raw error text stays on the run and step rows as drill-down only |
| Exception outside the loop (Phase 3, planned) | `save_profile` or a DB error after a `submitted` stop | `logged_run` marks the row `error` and, if the category is still NULL, sets `internal_error`; the earlier commit keeps stats and steps |
| Step logging (Phase 3, planned) | A response lacks optional fields or has no text blocks | Defaults via `getattr` (same style as `_usage_of`); bookkeeping never raises into or alters the loop. A failed step INSERT is a DB error and fails the run loudly |
| Observability tab (Phase 3, planned) | No runs, no failures, a run with no steps, `stats == {}` | An explanatory message for each state (see the [Phase 3 page](phase-3-observability.md)); never an exception |
| Eval run (Phase 4, planned) | A golden company's enrichment fails | Counted as not submitted (visible in the profiles-submitted row), its cost still charged; the run continues |
| Eval run (Phase 4, planned) | Batch cap reached before every entry ran | Remaining entries reported `skipped_budget`; the run is marked incomplete, not gated, exit 2 |
| Eval judge (Phase 4, planned) | No verdict (cap, invalid output, refusal) | Counted as `unjudged`, never as correct |
| MCP tool | Bad input or DB error | Compact actionable text; never write to stdout |

## 4.4 Testing
- Unit tests for models, candidates, DB helpers. Adapter tests with `httpx.MockTransport`. `streamlit.testing` smoke tests. 169 tests pass (re-run 2026-10-19); the only live test (`-m live`, `tests/test_live_api.py`) is the Phase 0 hello call and is skipped without a key.
- **Fake-client seam (built):** `run_agent` takes any object with `messages.create(**kwargs)`, and reads only `content[*].type/id/name/input/text`, `stop_reason` and `usage.*` (including `server_tool_use.web_search_requests`). `tests/fakes.py` provides a `ScriptedClient` that returns queued responses (or raises a queued exception) built from `SimpleNamespace` objects with helpers (`text_turn`, `tool_use_turn`, `server_search_turn`, `pause_turn`, plus `response`, `usage`, `search_blocks`) and records every request, with a snapshot of the message list, so tests can assert on cache markers, `tool_choice` and message history. It raises if the loop makes more calls than scripted. The loop appends `response.content` to history unchanged, which works for both SDK and fake objects. Tool handlers are tested with the existing `httpx.MockTransport` fixtures through `WebFetcher` and `CompaniesHouseClient`.
- **Phase 2 tests (built):** `test_costs.py` (exact Decimal arithmetic, fail-closed unknown model, usage addition), `test_agent_loop.py` (cache markers, repair path, second invalid gives `invalid`, exact iteration cap with wrap-up notice, tiny USD cap, 80 percent wrap-up, `pause_turn`, `end_turn` reminder, terminal stop reasons, truncated `tool_use` not executed, handler errors and unknown tools, `APIError` keeps usage, unpriced model fails before any call, budget validation), `test_agent_tools.py` (wrapper, ledger, tool order, server-side search entry, fetch and Companies House tools), `test_enrich.py` (schema derivation, provenance checks, happy path, repair, no persistence on failure with cost recorded, caps, re-run overwrite, `resolve_company`, CLI), and `test_db.py` additions (`save_profile`, `add_company_by_number`, additive `init_db`).
- **[DRIFT]** the planned `-m live` single-company enrichment test was deliberately not added, so a plain `pytest` with a key never spends money. Live behaviour (prompt caching, schema rejection, the 10-company run) is evidenced only by the manual runs in the implementation notes; `--strong`, forced `tool_choice` and `pause_turn` have no live evidence.
- Planned for Phase 4 (design on the [Phase 4 page](phase-4-evals-guardrails.md)): `UrlPolicy` and SSRF cases against an injected resolver; injection cases through `ScriptedClient` and `fixture_fetcher` (plain pytest, offline, no network); golden loader, metrics, number matcher, `BatchBudget` and the judge against synthetic data and a scripted client; one live-gated parametrised injection test (`SPROUT_RUN_LIVE_INJECTION=1`), so a plain `pytest` never spends money. Planned later: MCP tools tested directly on a temp DB.
- Planned for Phase 3 (design on the [Phase 3 page](phase-3-observability.md)): the same `ScriptedClient` seam proves the step records (count equals iterations, summed usage equals the run total, multi-tool and `server_tool_use` names, `tool_errors`, truncation, unchanged existing tests); `enrich` tests assert steps, category and stats survive every failing stop; `test_db.py` covers the old-DB migration and backfill; `test_app_queries.py` seeds a temp DB; `streamlit.testing` covers the empty, populated, no-steps and failed-run states. No live test.
- Golden-set evals run on demand, not per push (cost).
- Gap: CI action versions were written from memory; first green run unconfirmed.

## 4.5 Configuration
- Env plus `.env` via `get_settings()`: `ANTHROPIC_API_KEY`, `COMPANIES_HOUSE_API_KEY`, `SPROUT_BULK_MODEL`, `SPROUT_STRONG_MODEL`, `SPROUT_DB_PATH`, `SPROUT_CACHE_DIR`, `SPROUT_ENABLE_ACTIONS`.
- Secrets never committed; `.env`, `*.db`, `.cache/` are gitignored.
- Phase 2 added per-run USD and iteration caps (`--max-usd`, `--max-iterations`, defaults USD 0.13 and 12 in `agents/enrich.py`), `--effort` and `--strong`, and a price table in `costs.py` (code, not env, so a run is reproducible from the commit). No new env vars or dependencies. No search-backend setting: Phase 2 hard-wires server-side search (D13) and the swap point is `build_tools`.
- No migrations framework. Phase 2 added additive-column handling in `init_db` (D15, nullable columns only); delete `data/sprout.db` for non-additive changes, or adopt Alembic by the end of Phase 5 if one is needed.
- Phase 3 (planned) adds no environment variables, dependencies or CLI flags; the schema change is additive (D15).
- Phase 4 (planned) adds no environment variables: the eval DB path and the batch cap are `run_evals` flags (see the [Phase 4 page](phase-4-evals-guardrails.md)). `pyyaml` joins the `dev` group (A24); pytest `pythonpath` and ruff `src` gain `.`.

## 4.6 Security
- Fetched content and Companies House data are wrapped in `<untrusted_content>` delimiters (closing tag neutralised), the system prompt says it is data, and model output is validated and treated as untrusted. Search snippets are covered by the system-prompt rule only.
- Source URLs and `retrieved_at` are controlled by code (seen-URL ledger), so injected text cannot make the model cite an invented page.
- `WebFetcher` is SSRF-aware; residual DNS-rebinding risk is accepted.
- The enrichment agent has no write or refresh tools; DB writes happen in our code after validation.
- Streamlit actions are off unless `SPROUT_ENABLE_ACTIONS=1`; public deployments are read-only with no keys.
- **Phase 4 additions (planned, see that page):** (1) `UrlPolicy` in `sources/web.py` applies to the requested URL, every redirect hop and `robots.txt`: http and https only, ports 80 and 443 only, no credentials, a name denylist (`localhost`, `metadata.google.internal`, `.local`, `.internal` and similar), every resolved address must be public after unwrapping IPv4-mapped IPv6 and NAT64 (`64:ff9b::/96`, which Python 3.12 currently reports as global), and an optional `allow_hosts` used only by fixtures. No host allowlist in production, because the agent must read arbitrary company sites. (2) A total fetch deadline (`max_seconds`) next to the existing 5 MB streamed cap. (3) `ALLOWED_ENRICHMENT_TOOLS` is a runtime check in `enrich_with_result` and a test, so a state-changing tool (`refresh_company`, Phase 7) can never join the enrichment tool set unnoticed. (4) A system-prompt sentence that instructions in tool results or snippets are never followed. (5) The injection suite: offline cases for the code-level defences plus a live canary tier for model behaviour. (6) The judge treats the candidate text as untrusted (wrapped), and the eval DB is separate from the live DB.
- **Phase 3 (planned, see that page):** the truncated model text logged per step (and the error text) is untrusted and may echo scraped pages. It is truncated to 1,000 characters, never put into a prompt unwrapped, and rendered in Streamlit only with `st.code`, `st.text` or dataframe cells, never `st.markdown` or `unsafe_allow_html`, so a poisoned page cannot make a viewer's browser fetch an attacker URL.
- **Residual risk after Phase 4:** a real model that obeys an injected "set funding to US$1bn" and cites the injected page passes code-level validation and the number-match check; only the live canary run, the golden comparison and the Phase 5 verifier catch it. A prompt may still push a `fetch_page` call to a public attacker host with context in the query string (no secrets in context limits the damage). DNS rebinding between check and connection remains accepted.

## 4.7 Cost and observability
- `costs.py` (built) converts API `usage` (input, output including thinking, cache read, cache write, plus per-search charges for server-side `web_search`) to USD. It computes cost only; the caps are enforced in `loop.py` (`Budget`) against the running `Usage` total. Haiku 5.5 is priced at the lower rate card; its prompts over 100K tokens would double, which per-call prompts stay well below because tool results are capped (not exercised live).
- Search is billed separately from tokens, so `web_searches` is its own `stats` field and `max_uses` is a hard per-run cap. Measured: about 70 percent of a run's cost is the single search at an assumed US$0.01. That rate is not yet confirmed against a console invoice, so the USD figures inherit that uncertainty.
- `run_log.stats` is today's observability record. Add structured logging to stderr.
- **Phase 3 (planned, see that page):** observability moves from one stats blob per run to queryable rows. `run_log_step` holds one row per iteration (tool names, per-step tokens including cache, per-step USD, truncated response text); `run_log.failure_category` holds the fixed failure taxonomy; the Observability tab reads both. Per-run cost and iteration counts for the trend still come from `stats`, so historical runs appear in the trends but have no steps. Step logging adds one batch INSERT to an existing commit and no API call or I/O to the loop. Structured stderr logging remains optional and is not part of this phase.
- **Phase 4 (planned):** the itemized eval-spend estimate, the distinction between the US$5 proposed allocation and US$1.50 per-invocation cap, and their assumptions are in [4.8 below](#48-phase-4-eval-budget-planned). `BatchBudget` (D24) charges enrichment, judge, calibration and injection to one object; the cap is soft by at most one response. The judge runs on the strong model, which is the main reason to keep it to free-text fields only.

## 4.8 Phase 4 eval budget (planned)

The following is a planning estimate, not a measured full eval run. Enrichment is based on the Phase 2 average; the other components are estimates. Actual spend depends on model output, retries, and which optional evals are enabled.

| Component | Calculation / basis | Estimated spend per full eval |
|---|---|---:|
| Enrichment | 30 companies × Phase 2 measured average US$0.0138/company | US$0.41 |
| LLM judge | About 60 short calls (two free-text fields per company); US$0.002-0.004/call implies US$0.12-0.24; planning point used below is US$0.15 | US$0.15 (estimate) |
| Calibration | Planning allowance; not measured | US$0.05 (estimate) |
| Live injection tests | About 6 cases × Phase 2 average US$0.0138/company | US$0.08 |
| **Total** | Sum of planning points; varies with actual usage | **About US$0.70** |

| Budget item | Proposed amount | What it means |
|---|---:|---|
| Eval allocation (A3) | US$5 | At US$0.70 average, about 7 full evals; 6 would cost about US$4.20, leaving US$0.80. This is an estimate, not a guaranteed run count. |
| Per-invocation cap (A23 / D24) | US$1.50 | Maximum planned spend for one invocation, not the expected cost. If each invocation reaches the cap, US$5 funds 3 full-cap invocations (US$4.50) and leaves US$0.50 toward another. |
| Reserve (A3) | US$4 | Proposed reserve; intended use is not specified in the current plan. |
| Remainder of the approx. US$26 total | Approx. US$17 | Not itemized in the current plan; requires allocation before the full budget can be audited. |

The per-invocation cap is soft by at most one model response because spend is known only after a response completes. If the batch budget is exhausted before every company runs, remaining entries are skipped and the invocation is incomplete. `--score-only`, `--only`, `--limit` and `--skip-judge` can reduce spend. Using `--strong` as the generator is estimated to cost about five times more and requires a larger cap. The US$5 eval allocation, US$4 reserve, and US$1.50 invocation cap remain proposals pending confirmation (A3, A23 in [risks and ambiguities](risks.md)).
