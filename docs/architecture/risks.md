# Risks and open questions

Part of the [Sprout architecture docs](README.md). See the [overview](overview.md), [cross-cutting concerns](cross-cutting.md) and [decisions](decisions.md) for the architecture these risks and ambiguities refer back to.

## 6. Risks and open questions

### 6.1 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Hallucinated or misattributed funding | High | Code-enforced sources, verifier, fabricated-funding gate at most 3%, pass the company number for disambiguation |
| Candidate pool noise inflates cost | High | Rank by `candidate_score`, cheap pre-screen, pilot a small batch first |
| Cost overrun | Medium | Hard caps per company, bulk routing, prompt caching. No batch-level cap yet; `BatchBudget` (D24) must land with the Phase 4 evals and the Phase 5 orchestrator must use it before running the full candidate set |
| Prompt injection via pages | High | Delimiters, no write tools, validation, existing size/type limits; offline and live-canary injection tests planned for Phase 4 |
| Runaway agent loops | Medium | Iteration and USD caps, per-run `max_uses` and fetch limits, last-iteration wrap-up message |
| Server-side search billing eats the per-company target | Low | `max_uses` is 3, searches are counted in `stats`; search dominates per-run cost but the total stays well under target |
| `pause_turn` or `server_tool_use` blocks mishandled | Medium | Append `response.content` unchanged; scripted-client tests cover pause and search turns |
| Same-name company confusion | High | Pass company number, incorporation date, SIC codes and locality in the user message; `lookup_companies_house` result is the identity anchor |
| Large pages or long loops inflate input cost | Medium | Tool-result character cap, prompt caching of the growing conversation |
| Pilot companies absent from the candidate pool | Low | CLI accepts a company number and creates the row from Companies House directly (`add_company_by_number`) |
| `fetch_page` URL chosen by the model can carry context to an attacker | Low | No secrets in context; `UrlPolicy` planned for Phase 4, but a public attacker host still passes |
| Non-deterministic model output: an occasional run ends `invalid` or `refused` | Medium | Single repair attempt; Phase 5 needs retries and `enrichment_state` |
| Snippet-only claims cannot be audited | Medium | Code caps them at `medium` confidence and requires a note; the Phase 5 verifier is the real check |
| Funding coverage understated for non-USD figures | Low | Null is the safe failure; the note carries the original figure |
| `data/sprout.db` is tracked by git despite `*.db` in `.gitignore` | Low | Decide to commit or untrack |
| Sparse funding data | Medium | "Not disclosed" valid; report coverage, never plot as zero |
| Scraping ToS and robots | Medium | Honour robots, per-host limits, store snippets and links; recheck before publishing |
| SDK and model drift | Medium | Pin model IDs explicitly; re-verify before swapping |
| Schema change without migrations | Medium | Reset during development; Alembic before the weekly job |
| Unattended weekly spend | Medium | Hard cap, console spend limit, failure notification |
| Small golden set | Medium | Report counts and percentages; record label date and source; dev and holdout split |
| Golden labels wrong, stale or circular | High | Draft from independent sources, user verifies each entry, only verified entries scored |
| Eval gate is flaky at n=30 | Medium | Set each gate one company below the baseline; re-run failing entries before calling a regression |
| Number-match fabrication check gives false flags or misses | Medium | Review every flag and tune before gating; unverifiable reported separately |
| Judge prefers fluent answers or disagrees with the user | Medium | Free text only, judge model differs from generator, reference-first rubric, calibration set |
| Passing the injection suite is read as safety | Medium | Documented residual risk (see [4.6 Security](cross-cutting.md#46-security)); no write tools; the Phase 5 verifier; live canary tier |
| SSRF policy gaps (encoded IPs, NAT64, rebinding) | Medium | Parametrised policy tests through an injected resolver; rebinding accepted |
| Logged model text carries injected content | Medium | Text blocks only, 1,000-character cap, never prompted unwrapped, rendered without Markdown or HTML |
| Phase 3 findings come from a biased, pilot-heavy sample | Medium | Run a fresh batch of non-pilot companies with logging on before drawing Phase 4 conclusions |
| Failure taxonomy too coarse to guide fixes | Low | Start at stop/error-type level; the step text and `run_log.error` carry the detail; split `invalid` only if it dominates |
| Schema grows again in Phase 5 for per-company status | Medium | Additive columns and tables only; `enrichment_state` references `run_log.id` |
| CI never run green | Low | Confirm on first push |

### 6.2 Open ambiguities

| # | Phase | Open question | Current position |
|---|---|---|---|
| A1 | 2 / 7 | Search/fetch tooling differs across phases | Phase 2 resolved: server-side search only, all page reads through `WebFetcher` (D13). Still open for Phase 7: whether to replace `fetch_page` with an MCP server (see the [Phase 7 page](phase-7-mcp-server.md)) |
| A2 | 1 / 5 | Candidate pool size and the rule for selecting which companies get enriched | Not pinned down |
| A3 | 4 | Cost allocation across evals, repeated runs and prompt iteration | Not itemized against the total budget (see [4.8 Phase 4 eval budget](cross-cutting.md#48-phase-4-eval-budget-planned)) |
| A4 | 5 | Per-company status has no home in the current schema | Proposed: `enrichment_state` table |
| A5 | 5 | News has no company link, big-news score or type | Proposed: `company_news` table |
| A6 | 6 | "Funding history" is not supported by the current model | Open |
| A7 | 2 | `CompanyProfile.stage` and a round's `stage` in `funding_rounds` can disagree | Precedence undefined |
| A8 | 5 | Classifier "sub-category" has no schema field | Taxonomy stays flat and frozen |
| A9 | 6 / 8 | Snapshot delivery mechanism for read-only deployments | Undecided |
| A10 | 6 | Dashboard is meant to be read-only but the console can spend API quota | Needs separating or removing before deploy |
| A11 | 4 / 5 | Ordering of evals vs. the multi-agent split | Resolved: evals (Phase 4) now precede the Phase 5 split |
| A12 | 6 | The optional Ask box duplicates MCP access | Needs a read-only guarantee and rate limit |
| A13 | 2 / 7 | Keeping `refresh_company` out of the enrichment agent's tool set | Resolved: `ALLOWED_ENRICHMENT_TOOLS` checked at runtime and in a test (D26) |
| A14 | 7 | `refresh_company` sync/async, cooldown and payer | Unspecified |
| A15 | 5 | Discovery-agent output has no dedupe key when `company_number` is NULL | Open |
| A16 | 2 | `Stage` values and funding-field units | Resolved: Series C/D/E/F are distinct values; funding fields are whole millions of USD |
| A17 | 2 | A re-run that nulls a previously valued field leaves both facts | Proposed: the winning-value rule (see [4.1 Sourcing and provenance](cross-cutting.md#41-sourcing-and-provenance)) treats a NULL fact as lower priority than any valued one |
| A18 | 4 | Fabricated-funding definition | Proposed (D21), not yet accepted |
| A19 | 4 | Last-round amount rule | Proposed (D22), not yet accepted |
| A20 | 4 | Injection-testing acceptance | Proposed: two tiers, offline + live canary (D26); needs sign-off |
| A21 | 4 | Golden-set labelling | Needs the user to verify entries from independent sources before tuning can start |
| A22 | 4 | URL policy: allowlist vs. deny-by-rule | Resolved: deny-by-rule in production since the agent must read arbitrary sites (D25); `allow_hosts` is for fixtures only |
| A23 | 4 | Eval spend estimate vs. proposed caps | US$0.70/run estimate vs. a US$1.50 per-invocation cap and US$5 allocation; needs confirming |
| A24 | 4 | Golden-set file format | Open: add `pyyaml` for YAML, or use stdlib `tomllib` with TOML |
| A25 | 4 | Source-URL "validity" | Resolved: reports both ledger membership (free) and liveness (opt-in request) |
| A26 | 4 | Judge model and calibration-set size | Sonnet judging Haiku, ~12 graded triples; a coarse signal until the set grows |
| A27 | 3 | Logging tool inputs (e.g. the fetched URL) per step | Resolved: not logged; `stats.sources` already records them |
| A28 | 3 | Failure taxonomy granularity | Resolved: stays at eight categories; `invalid_output` is not split by sub-reason |
| A29 | 3 | Granularity of a "step" | Resolved: one model response, which can call several tools; tokens and cost are not split per tool |
| A30 | 3 | Phase 3 findings need a fresh batch of non-pilot companies | Needed before informing Phase 4 gate values |
| A31 | 3 / 5 | `run_log` has no company link except inside `stats.company_id` | Deferred: a nullable `run_log.company_id` lands in Phase 5 with `enrichment_state` |

### 6.3 Open items by phase
- **Phase 2:** profile review by the user, a console-invoice check of the search price, deciding whether `data/sprout.db` stays tracked by git, and the cost-split confirmation (A3).
- **Phase 3:** approve the findings-batch spend (A30); confirm one-step-per-response (A29) and not logging tool inputs (A27).
- **Phase 4:** label and verify the golden set (A21, critical path); approve the fabricated-funding definition (A18), last-round rule (A19) and two-tier injection acceptance (A20); choose `pyyaml` or TOML (A24); confirm the eval budget and cap (A23, A26).
- **Before Phase 5:** schema additions (`enrichment_state`, `company_news`); a retry policy; the winning-value rule (A17); candidate pre-screen and selection; concurrency model for the sync client.
- **Before Phase 6:** winning-value rule (A17) and the shared service layer (D10). **Before Phase 7:** define `refresh_company` (A14).
- **Before Phases 6 and 8:** snapshot delivery (A9); data licensing and robots recheck.
