# Risks and open questions

Part of the [Sprout architecture docs](README.md). See the [overview](overview.md), [cross-cutting concerns](cross-cutting.md) and [decisions](decisions.md) for the architecture these risks and ambiguities refer back to.

## 6. Risks and open questions

### 6.1 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Hallucinated or misattributed funding | High | Code-enforced sources, verifier, fabricated-funding gate at most 3%, pass the company number for disambiguation |
| Candidate pool noise inflates cost | High | Rank by `candidate_score`, cheap pre-screen, pilot 10-20 companies first |
| Cost overrun (measured USD 0.0138 per company in Phase 2 against a USD 0.07 target; under US$13 for 150 in Phase 5; US$26 total) | Medium (was High) | Hard caps per company, bulk routing, prompt caching. No batch-level cap yet (see the [Phase 2 page](phase-2-enrichment.md) [DRIFT] note); `BatchBudget` (D24) arrives with the Phase 4 evals and the Phase 5 orchestrator must use it before it runs 150 companies. Measurement rests on an unconfirmed search price |
| Prompt injection via pages | High | Delimiters, no write tools, validation, existing size/type limits; fixtures planned in Phase 4 (offline cases plus a live canary run) |
| Runaway agent loops | Medium | Iteration and USD caps, per-run `max_uses` and fetch limits, last-iteration wrap-up message, tested with a tiny cap |
| Server-side search billing eats the per-company target | Low (measured) | `max_uses` is 3, searches are counted in `stats`; search is about 70 percent of cost but the total is a fifth of target. Check the first console invoice for the US$10 per 1,000 assumption |
| `pause_turn` or `server_tool_use` blocks mishandled (dropped blocks give API 400s or lost context) | Medium | Append `response.content` unchanged; scripted-client tests for pause and search turns. `server_tool_use` blocks worked live; `pause_turn` has not been seen live |
| Same-name company confusion | High | Pass company number, incorporation date, SIC codes and locality in the user message; `lookup_companies_house` result is the identity anchor; prompt says to reject pages that do not match |
| Large pages or long loops inflate input cost | Medium | Tool-result character cap, prompt caching of the growing conversation, cached-token counts in `stats` |
| Pilot companies absent from the 876 candidates | Low (mitigated) | CLI accepts a company number and creates the row from Companies House (`add_company_by_number`, built; 9 pilot companies were added this way) |
| `fetch_page` URL chosen by the model can carry context to an attacker | Low | No secrets in context; `UrlPolicy` in Phase 4 (D25), but a public attacker host still passes |
| Non-deterministic model output: an occasional run ends `invalid` (invented homepage, correctly rejected) or `refused` (transient) | Medium | Single repair attempt; Phase 5 needs retries and `enrichment_state`. 8 `error` rows from the iteration phase remain in `run_log` |
| Snippet-only claims cannot be audited (search results arrive encrypted) | Medium | Code caps them at `medium` confidence and requires a note; the Phase 5 verifier is the real check |
| Funding coverage understated: the model sometimes leaves funding amounts null for non-USD figures | Low | Null is the safe failure; the note carries the original figure |
| `data/sprout.db` is tracked by git despite `*.db` in `.gitignore` | Low | Decide to commit or untrack (open item for you) |
| Sparse funding data | Medium | "Not disclosed" valid; report coverage, never plot as zero |
| Scraping ToS and robots | Medium | Honour robots, per-host limits, store snippets and links; recheck before publishing |
| SDK and model drift | Medium | Pin versions; Haiku 5.5 model ID and the `web_search_20260209` type confirmed by live Phase 2 calls (a newer `web_search_20260318` exists in the SDK, not adopted); Sonnet 5.5 not yet run live |
| Schema change without migrations | Medium | Reset during development; Alembic before the weekly job |
| Unattended weekly spend | Medium | Hard cap, console spend limit, failure notification |
| Small golden set (one company is about 3 points) | Medium | Report counts and percentages; record label date and source; dev and holdout split |
| Golden labels wrong, stale or circular (drafted from the pipeline's own output) | High | Draft from independent sources, user verifies each entry, label date and sources recorded, only verified entries scored |
| Eval gate is flaky: runs vary by 1 to 2 companies at n=30, so an 85 percent gate can flip between identical runs | Medium | Set each gate one company below the baseline; `--only` re-run of failing entries before calling a regression; delta marked as noise under 2 companies |
| Number-match fabrication check gives false flags (summed totals, spelled-out figures) or misses | Medium | Review every flag on the first run and tune before gating; unverifiable reported separately |
| Judge prefers fluent answers or disagrees with the user | Medium | Free text only, judge model differs from generator, reference-first rubric, calibration set, rows marked uncalibrated below 80 percent agreement |
| Passing the injection suite is read as safety | Medium | Documented residual risk (see [4.6 Security](cross-cutting.md#46-security)); no write tools; the Phase 5 verifier; live canary tier |
| SSRF policy gaps (encoded IPs, NAT64, rebinding) | Medium | Parametrised policy tests through an injected resolver; rebinding accepted |
| Logged model text carries injected content (Phase 3) | Medium | Text blocks only, 1,000-character cap, never prompted unwrapped, rendered without Markdown or HTML (see [4.6 Security](cross-cutting.md#46-security), D30) |
| Findings come from a biased sample: the existing runs are mostly the 10 pilot companies and were produced while the prompt was being tuned (Phase 3) | Medium | Run a fresh batch of non-pilot companies with logging on before drawing Phase 4 conclusions (Phase 3 page, step 3c, A30) |
| Failure taxonomy too coarse to guide fixes, `invalid` in particular (Phase 3) | Low | Start at stop and error-type level as the plan says; the step text and `run_log.error` carry the detail; split `invalid` only if the first batch shows it dominates (A28) |
| Schema grows again in Phase 5 for per-company status across workers | Medium | Hooks listed at the end of the Phase 3 page; additive columns and tables only; `enrichment_state` references `run_log.id` |
| CI never run green | Low | Confirm on first push |

### 6.2 Plan ambiguities
- **A1** Search/fetch tooling differs across Phases 1, 2 and 7. **Phase 2 part resolved and built (D13):** server-side search only, all page reads through `WebFetcher`. Still open for Phase 7: replacing `fetch_page` with an MCP server (see the [Phase 7 page](phase-7-mcp-server.md)).
- **A2** Candidates: "~200" vs "at least 200" vs 876 loaded vs 150 enriched; no rule for choosing the 150.
- **A3** Cost arithmetic leaves no budget for evals (LLM judge, repeated runs) or prompt iteration. Phase 2 evidence: all live development spend was about USD 0.42 (30 enrich runs) against the roughly USD 4 ceiling, so the proposed split leaves room. The proposed USD 5 eval and USD 4 reserve allocations still need your confirmation; the reserve's intended use and the remaining roughly USD 17 of the USD 26 total are not itemized here (see [4.8 Phase 4 eval budget](cross-cutting.md#48-phase-4-eval-budget-planned)).
- **A4** Phase 5 per-company status has no home in the Phase 1 schema (`enrichment_state`).
- **A5** News has no company link, big-news score or type (`company_news`).
- **A6** Phase 6 "funding history" is not supported by the model.
- **A7** `CompanyProfile.stage` and a round's `stage` in `funding_rounds` can disagree; define precedence.
- **A8** Classifier "sub-category" has no schema field; the taxonomy is flat and frozen.
- **A9** Snapshot delivery: Phase 6 "bundled", Phase 8 "don't commit", `.gitignore` excludes `*.db`.
- **A10** Dashboard is "read-only" but the console can spend API quota; separate or remove before deploy.
- **A11** Evals (Phase 4) are needed to judge the Phase 5 split. Resolved by the renumbering: evals now come first.
- **A12** The optional Ask box duplicates MCP access and needs a read-only guarantee and rate limit.
- **A13** Phase 4 "restricted tools" is consistent with `refresh_company` only if the latter is never in the agent's tool set. Designed: `ALLOWED_ENRICHMENT_TOOLS` is checked at runtime and in a test (D26).
- **A14** `refresh_company` sync/async, cooldown and payer are unspecified.
- **A15** Discovery-agent output has no dedupe key when `company_number` is NULL.
- **A16** `Stage` has Series C, D, E and F as distinct values. Funding is tracked in USD: `total_raised_million_usd`, `valuation_million_usd` and each round's `amount_million_usd` in `funding_rounds`, all whole millions of US dollars.
- **A17** (new, Phase 2) A re-run that returns null for a previously valued field leaves both facts; the winning-value rule (see [4.1 Sourcing and provenance](cross-cutting.md#41-sourcing-and-provenance)) must treat a NULL fact as lower priority than any valued one.
- **A18** (Phase 4) The plan defines the fabricated-funding rate as "value present, no supporting source", which is always 0 percent because `Sourced`, the ledger and the note rule already reject such values. Proposed (D21): fabricated means the cited page text does not contain the figure; snippet-only sources are reported as unverifiable; ledger membership stays as a separate 100 percent row. Proposed rollout: report findings but do not gate the first baseline; review flags and tune the matcher before considering a gate. Needs your approval; D21 is the proposed resolution, not an accepted decision.
- **A19** (Phase 4) "Last-round amount" is not defined when a company has several rounds or undated rounds. Proposed (D22): latest announced round, ties by stage order; golden `stage` is the stage of that round; within 10 percent of USD millions.
- **A20** (Phase 4) Acceptance says all injection fixtures leave output unchanged, each a pytest case. With a scripted model a pytest case can only prove the code-level defences. Proposed (D26): offline pytest case per fixture for those, plus a live canary run and a gated live pytest per fixture for model behaviour (about US$0.08). Needs your acceptance of the two tiers.
- **A21** (Phase 4) Labels you create from the web sources the agent also uses can be wrong or circular, and the 10 pilot companies were used to tune the prompt. Proposed: the implementer drafts all 30 entries from independent sources with `verified: false`, you verify each (about 1.5 hours, on the critical path), 10 entries are `holdout`, the pilots are `dev`. Needs you.
- **A22** (Phase 4) "URL allowlist/denylist" cannot mean a production host allowlist for an agent that reads arbitrary sites. Proposed (D25): deny by rule (scheme, port, name, address) in production; `allow_hosts` only for fixtures.
- **A23** (Phase 4) Eval spend against A3: the itemized estimate in [4.8](cross-cutting.md#48-phase-4-eval-budget-planned) is about US$0.70 per full run, while US$1.50 is the proposed maximum per invocation, not the expected spend. The proposed US$5 eval allocation is about seven average-cost runs, but only three invocations that each spend the full cap (with US$0.50 left toward another); actual costs vary. Confirm the US$5 eval allocation and US$1.50 cap, and clarify the US$4 reserve.
- **A24** (Phase 4) `golden.yaml` needs YAML parsing and `pyyaml` is not in `uv.lock`. Proposed: add `pyyaml` to the `dev` group; alternative: `golden.toml` with stdlib `tomllib`. Needs your choice (new dependency).
- **A25** (Phase 4) "Source-URL validity" can mean membership in the run's ledger (free, true by construction after D16) or that the URL is alive. Proposed: report both; liveness is opt-in (`--check-urls`) and uses `WebFetcher` (GET, cached, polite), not HEAD.
- **A26** (Phase 4) The judge model and calibration size. Proposed: Sonnet 5.5 judging Haiku 5.5 output, about 12 graded triples, 80 percent agreement before judge rows are trusted; 12 is coarse, so treat judge rows as a trend, not a gate, until the set is larger.
- **A27** (Phase 3) The plan logs "which tool was called" but not what it was called with. The fetched URL is the most useful diagnostic and also model-chosen untrusted text. Proposed: do not log tool inputs in Phase 3; the Phase 4 `stats.sources` list already records the URLs the tools returned. Add a truncated `tool_input` column later if the first batch cannot be diagnosed without it.
- **A28** (Phase 3) Taxonomy granularity. Proposed: eight categories at the stop and error-type level; `invalid_output` covers invented URLs, missing funding notes and schema errors alike. Splitting it would need the validator to return a code rather than text.
- **A29** (Phase 3) A "step" is one model response, which can call several tools; tokens and cost cannot be split per tool. Proposed: one row per response with comma-joined `tool_name`. Say so if a per-tool-call table is wanted.
- **A30** (Phase 3) Phase 4 was designed before this phase, and the plan's step 4 says Phase 3 findings inform it. Existing data is mostly the pilot set and has no steps. Proposed: a fresh live batch of about 20 non-pilot companies (about US$0.30) after the backend lands, findings written to `implementation-notes.md`, and changes to the Phase 4 page proposed to you rather than applied. Needs your approval of the spend and the company choice.
- **A31** (Phase 3) `run_log` has no company link except `stats.company_id`. Proposed: leave it (the company name is joined in Python), and add a nullable `run_log.company_id` with `enrichment_state` in Phase 5.

### 6.3 To resolve, by phase
1. **Phase 2 (done; items below were open before it):**
   - *Resolved and built:* search backend (A1, D13); costs and FX constant (D14); `category` reaching the dev DB (D15); provenance enforced in code (D16); fake-client seam and loop contract (see the [Phase 2 page](phase-2-enrichment.md) and [4.4 Testing](cross-cutting.md#44-testing)); injection handling (see the Phase 2 page and [4.6 Security](cross-cutting.md#46-security)); `strict` schema question (not accepted, D18); `tool_choice: auto`, effort shape (`output_config`), top-level `cache_control` and the `web_search_20260209` type string, all confirmed by live calls on Haiku 5.5; model IDs `claude-haiku-5-5` confirmed live.
   - *Settled with a proposal (D9):* no eval harness in Phase 2. The 10-company check was a manual review recorded in `implementation-notes.md`. The eval harness is now Phase 4, preceded by observability (Phase 3) and followed by the multi-agent split (Phase 5).
   - *Still needs you:* review the 10 profiles yourself (the existing review was by the implementing agent); check the first console invoice against US$10 per 1,000 searches; decide whether `data/sprout.db` stays tracked by git; confirm the USD split for A3. The Phase 0 hello and `pytest -m live` criterion is not recorded as run in the notes (live enrichment calls did succeed with the configured key and model).
   - *Not tested live:* `--strong` (Sonnet 5.5), forced `tool_choice` on Haiku 5.5, the over-100K-token price tier, `pause_turn`, and the minimum cacheable prefix (caching did work in practice).
2. **Phase 3 (observability; designed on 2026-10-28, see the [Phase 3 page](phase-3-observability.md)):**
   - *Decided in this design, pending your veto:* step table (D27), `failure_category` column and taxonomy (D28), steps on `LoopResult` persisted by `enrich.py` (D29), truncation and untrusted rendering (D30), backfill without steps (D31); the dashboard page is a new tab in the existing console, not a separate app.
   - *Needs you:* approve the fresh findings batch and its spend (A30); confirm the one-row-per-response step (A29) and not logging tool inputs (A27).
   - *Implementation order:* 3a backend, 3b UI, 3c findings (split and acceptance on the Phase 3 page). 3a must land before the 3c batch so the new runs carry steps.
3. **Before Phase 4 (designed on 2026-10-25; open items need you, see the [Phase 4 page](phase-4-evals-guardrails.md)):**
   - *Needs you:* label and verify the golden set (about 1.5 hours, critical path, A21); approve the fabricated-funding definition (A18), the last-round rule (A19) and the two-tier injection acceptance (A20); choose `pyyaml` or TOML (A24); confirm the eval budget and the default US$1.50 cap (A23, A26).
   - *Decided in this design, pending your veto:* repo-root `evals/` (D19), separate eval DB (D20), number-match fabrication (D21), last-round rule (D22), judge on `run_agent` (D23), `BatchBudget` (D24), `UrlPolicy` (D25), injection design (D26).
   - *Implementation order:* guardrails and injection cases first (no labels needed): `UrlPolicy` and tests, `max_seconds`, `ALLOWED_ENRICHMENT_TOOLS`, prompt sentence, `extra_context`, `fixture_fetcher`, offline injection cases; then `BatchBudget` and `sources` in `run_log.stats`; then golden loader and metrics on synthetic data; then runner and judge once at least 20 entries are verified; then the baseline run, set `GATES` one company below the result, and record it in `evals/results.md`.
4. **Before Phase 5:** schema additions (`enrichment_state`, `company_news`); a retry policy (the batch-level USD cap is `BatchBudget`, to be built in Phase 4, D24); the winning-value rule (A17); candidate pre-screen and selection of 150 (A2); concurrency model for the sync client; migration approach.
5. **Before Phase 6 (dashboard):** define the winning-value rule (A17) and extract the service layer (D10). **Before Phase 7 (MCP):** define `refresh_company` (A14).
6. **Before Phases 6 and 8:** snapshot delivery (A9); data licensing and robots recheck.
