# Phase 6: Multi-agent pipeline

Part of the [Sprout architecture docs](../README.md). Status: see [7. status.md](../7.%20status.md). What was built, measured and decided: [implementation-notes.md](../../implementation-notes.md#phase-6---multi-agent-pipeline-at-scale).

Scope: a website-screen agent, a news agent and a deterministic orchestrator, run over a working set of about 50 companies. The website screen finds or verifies websites for companies already in the candidate set.

```mermaid
flowchart TB
    CLI["python -m sprout.orchestrator"]
    subgraph ORCH["agents/orchestrator.py"]
        SEL["select_working_set, order_for_work"]
        POOL["asyncio loop, to_thread per company, one Worker per slot"]
        PROC["process_company: website screen, enrich, news"]
        GATE["RateLimitGate and stop event"]
    end
    STATUS["status.py: derived status, reaper, profile gaps"]
    COMMON["agents/common.py: Deps, run_recorded, AgentFailed"]
    WEBSITE["website.py: verify company website"]
    ENR["enrich.py"]
    NEWS["news.py: bulk model, 1 search"]
    LLM(["Anthropic API"])
    EXT(["Search, websites"])
    BATCH["BatchBudget with lock (costs.py)"]
    SQL[("SQLite, WAL")]
    CLI --> SEL
    SEL --> STATUS
    STATUS --> SQL
    SEL --> POOL
    POOL --> PROC
    PROC --> GATE
    PROC --> BATCH
    PROC --> WEBSITE
    PROC --> ENR
    PROC --> NEWS
    WEBSITE --> COMMON
    ENR --> COMMON
    NEWS --> COMMON
    COMMON <--> LLM
    WEBSITE --> EXT
    ENR --> EXT
    NEWS --> EXT
    COMMON --> SQL
    NEWS --> SQL
    SQL -. "make publish-snapshot (D40)" .-> SNAP["data/sprout_snapshot.db"]
    SNAP -. push .-> CI["snapshot workflow: integrity check"]
```

Per-company flow (one worker thread, steps run in this order; a step that is fresh is skipped):

```mermaid
flowchart TB
    S["company_status: steps needed"] --> E{"enrich needed?"}
    E -- yes --> WS["website screen"]
    WS -- "website identified" --> RE["enrich"]
    WS -- "no website established" --> SKIP["skip enrichment"]
    WS -- "screen failed" --> F
    E -- no --> N
    RE -- "failed" --> F["status failed or gave_up; nothing else runs"]
    RE -- "ok" --> N["news (1 search, writes company_news)"]
    N --> O["outcome: ok, partial or failed"]
```

## Per-company pipeline

| # | Step | Run `kind` | Depends on | Runs when |
|---|---|---|---|---|
| 1 | enrich | `company_enrich` | website screen | never enriched, enrichment older than `stale_days`, or latest attempt failed |
| 2 | news | `company_news_match` | an ok enrich (uses its website and one-liner to disambiguate) | no ok news run in `news_stale_days`, or latest attempt failed |

- **Nothing runs in parallel inside a company.** Parallelism is across companies (D38). Enrich runs first, so a budget stop drops news before enrichment.
- Step failures are isolated. Only a failed, stopped or budget-skipped enrich stops the company (no profile to work from). A company that has no usable enrichment and whose enrich is not due (running elsewhere, or excluded by `--steps`) ends `failed`.

### Website screen

Before each due enrichment, the orchestrator and single-company CLI search for the company's official website. A website is accepted only after fetching the page and matching a verbatim identity quote against its visible text. During the screen, `fetch_page` only accepts a URL on a site that `web_search` already returned (host compared without `www.`), so the model cannot fetch a domain it made up; a refused fetch is not a failure. The model judges whether the page belongs to the legal entity, so identity is not guaranteed. The screen stores the website as a sourced fact and records its result and cost in `company_website_screen`. Enrichment receives the recorded website as untrusted context.

An unconfirmed website produces `skipped_no_website`, without enrichment or downstream steps. A failed search or fetch cannot establish absence and produces a retryable failure instead. A skipped company remains in the ranked working set, is screened again on the next invocation and is excluded from the success-rate denominator. `--only` and `--force` do not bypass the website screen. `--dry-run` does not screen and its estimate includes the screen for each due enrichment. Screen budgets and model routing are in [cross-cutting](../4.%20cross-cutting.md#9-phase-6-agent-caps-and-run-budget).

The low-level enrichment functions remain available for controlled evals, which intentionally include non-startup controls and do not use the production screen.

## Interfaces (as built)

```python
# agents/common.py  (extracted from agents/enrich.py)
@dataclass(frozen=True)
class Deps: client; fetcher; ch: CompaniesHouseClient | None; settings; model: str; effort: str = "low"
class AgentFailed(RuntimeError):              # per-agent subclasses set `label`
    stop: str; cost_usd: Decimal; error_type: str | None   # LoopResult.error_type, e.g. "RateLimitError"
    # EnrichmentFailed, NewsFailed
def run_recorded(session, company, kind: RunKind, deps, *, system, messages, tools, budget, ledger,
                 max_tokens, persist: Callable[[Any], Mapping | None], failed=AgentFailed) -> LoopResult
    # logged_run + run_agent + stats (incl. error_type, sources) + run-level columns + run_log_step rows,
    # committed BEFORE raising `failed` on any non-submitted stop. On success persist(payload) runs
    # inside the run block; a mapping it returns is merged into run.stats.

# agents/tools.py
ALLOWED_NEWS_TOOLS     = {"web_search", "fetch_page", "submit_news"}
assert_tools_allowed(tools, allowed, label)       # RuntimeError before logged_run; every agent calls it
build_tools(fetcher, ch, ledger, *, max_searches=3)   # 0 omits web_search

# agents/news.py
NEWS_BUDGET = Budget(8, 0.05, max_repairs=1); MAX_NEWS_PER_COMPANY = 5
class NewsEntry(BaseModel): url; title; published: date | None; big: bool; kind: NewsKind | None; summary
collect_news(...) -> list[NewsEntry]; collect_with_result(...) -> (entries, LoopResult)   # NewsFailed

# status.py  (reads db only; the Phase 8 cooldown and Phase 7 reuse it)
STEPS = ("enrich", "news"); STEP_KINDS: step -> RunKind
StatusPolicy(enrich_stale_days=30, news_stale_days=7, max_attempts=3, running_timeout=30 min)
StepStatus(step, state: fresh|stale|never|failed|exhausted|running, last_ok_at, failed_attempts, last_failure_category)
CompanyStatus(company_id, steps, cost_usd).needs(*, force=False) -> list[str]         # STEPS order
company_statuses(session, company_ids, policy, now) -> dict[int, CompanyStatus]       # one query over run_log
reap_abandoned(session, now, policy) -> int
profile_gaps(session, company_id) -> list[str]     # missing: category, stage, funding, business_model, target_user, news

# agents/orchestrator.py  (src/sprout/orchestrator.py is a three-line shim, like sprout/enrich.py)
Worker(session, fetcher, ch); open_worker(settings, engine, stack) -> Worker
RateLimitGate(pause_seconds): trip(), wait(stop), trips
RunContext(client, settings, batch: BatchBudget, gate, stop: threading.Event, policy, now)
StepOutcome(step, state: ok|failed|skipped_fresh|skipped_no_website|skipped_budget|stopped, cost_usd, failure_category, error)
CompanyOutcome(company_id, result: ok|partial|failed|gave_up|skipped_budget|skipped_no_website|stopped, steps, cost_usd)
process_company(worker, company_id, steps, ctx, *, force=False) -> CompanyOutcome   # sync, never raises, never prints
select_working_set(session, *, limit, only, ch) -> list[int]
order_for_work(ids, statuses, steps, *, force) -> list[int]
async run(settings, args, *, client=None, stop=None) -> RunSummary(exit_code, text, error, stats); main(argv) -> int
```

- **`process_company` is the seam.** `steps` is the set of steps allowed; the function reads the company's status itself and runs those that are due, so callers do not compute the needed steps. It never raises (an unexpected exception rolls the session back and is recorded as `internal_error`) and never prints, so Phase 8's `refresh_company` can call it from an MCP stdio server. The eval runner does **not** go through it: `run_evals._enrich_entry` still calls `enrich_with_result` directly (it needs a `Worker`, a `RunContext` and a cost-breakdown rework).
- **Per-agent `Deps`:** `_run_step` builds one `Deps` per step from the worker's fetcher and Companies House client, with the bulk model. News gets no Companies House client. Routing, effort and caps are in [cross-cutting 9](../4.%20cross-cutting.md#9-phase-6-agent-caps-and-run-budget).
- **News input.** The model gets the company's name, number, locality, website and one-liner, runs the one allowed `web_search` (and may `fetch_page`), rejects same-name false matches, and submits at most 5 entries (big first). An entry whose URL no tool returned gets one repair, then is dropped. "Big news" is the rubric in the prompt (leadership only for C-suite changes, own-site posts only for funding, acquisition or a named-partner partnership). The agent is the only news writer.

## What each agent writes

| Agent | Writes | Never writes |
|---|---|---|
| enrich | `fact` rows, `funding_round` rows (replaced wholesale), `company.category` | news |
| news | `company_news` rows via `db.save_company_news` (`news_kind` set only when `big`, one-sentence `summary`); upsert on `(company_id, url)`, never deletes; run `stats`: `stored`, `big` | `fact` |

## Fact fields: writers, rows and how to read them

A `fact` row is unique per `(company_id, field, source_url)`: a re-run citing the same URL overwrites its row, a different URL adds one. So every field below can in principle have several rows; the last column says where that happens in practice. The winner is chosen by `db.winning_facts` ([winning-value rule](phase-3-enrichment.md#the-winning-value-rule-a6)).

| Field | Type | Filled by | Several rows? |
|---|---|---|---|
| `website` | string (URL) | Website screen, then enrichment | Yes in practice: the screen and enrichment cite different URLs |
| `hq` | string | Ingest (registered-office locality, medium confidence), then enrichment | Yes in practice: ingest row plus enrichment row |
| `founded` | int (1800 to 2100) | Ingest (incorporation year, which can differ from the founding year), then enrichment | Yes in practice: ingest row plus enrichment row |
| `stage` | `Stage` enum | Enrichment only | Only after a re-run that cites a different source |
| `business_status` | `BusinessStatus` enum | Enrichment only | Only after a re-run that cites a different source |
| `total_raised_million_usd` | int (0 or more) | Enrichment only | Only after a re-run that cites a different source |
| `valuation_million_usd` | int (0 or more) | Enrichment only | Only after a re-run that cites a different source |
| `business_model` | string | Enrichment only | Only after a re-run that cites a different source |
| `target_user` | string | Enrichment only | Only after a re-run that cites a different source |
| `one_liner` | string | Enrichment only | Only after a re-run that cites a different source |

Outside the table: `company.category` is a classification with no fact rows, and `funding_rounds` is its own table, replaced wholesale on each enrichment, so it has no winner to pick (`db.latest_round` picks the latest round, D22). `enrich --reset` deletes the company's profile first and re-seeds `website` from the screen result.

### Reading a field: disclosed, not disclosed, not researched

Judge a field by its winning row only (`sprout.queries.get_profile` does this and returns each field as a `ProfileFact` with `disclosed`).

| What you find | Meaning |
|---|---|
| Winning row with a value | Disclosed |
| Winning row with a NULL `value_json` | Not disclosed: the agent researched the field and found nothing (shown as `NOT_DISCLOSED`) |
| No row for the field | Not researched (see the cases below) |

- **A NULL row next to a valued row is not "not disclosed".** A valued fact beats a NULL one, so the NULL row is history. Only a NULL winner counts.
- **Enrichment-only fields are all-or-nothing.** `save_profile` writes every `Sourced` field in one go and sets `company.category`, so after a successful enrichment each of them has a row (valued or NULL). With no row, enrichment has not succeeded yet, including when a run failed before saving.
- **`hq`, `founded` and `website` do not show enrichment.** Ingest and the website screen write them before enrichment runs, so a row only says a source was cited. Use `company.category IS NOT NULL` as the "enriched" marker (as `enriched_company_count` does); a NULL `category` means not enriched.
- **`funding_rounds`:** no rows on an enriched company means none were found; on an unenriched company it means not researched. The table cannot tell the two apart by itself.
- **Storage keeps NULL, not a label.** The values are typed and every reader tests `value_json is not None`; the label is a presentation concern and a sentinel string would break the typed fields and the winning-value rule.

## Selector, status and retry

Status is derived from `run_log` (D32, [data model](../2.%20data-model.md#per-company-status-derived-not-stored)). Constants live in `StatusPolicy`. Decisions: D39.

**Working set.** Candidates are all companies in the database, ranked by `candidate_score` descending then `id`. `--limit N` takes the first N: it bounds the working set, not the amount of new work, so rerunning with the same N completes the same set instead of growing it, and a pilot of 12 is a prefix of the full 50. `--only NAME_OR_NUMBER...` bypasses the ranking and the limit (companies not yet in the DB are added through `resolve_company`; an unresolvable target exits 2).

**Per step.** `StepStatus.state` for a step with kind K:

| State | Condition | Selected |
|---|---|---|
| `fresh` | newest ok row (`status='ok'`, `failure_category IS NULL`) is within the TTL (enrich 30 days, news 7 days) | no |
| `never` | no ok row and no attempts | yes |
| `stale` | newest ok row older than the TTL | yes |
| `failed` | newest attempt failed and `failed_attempts < max_attempts` (3) | yes, ahead of everything else |
| `exhausted` | `failed_attempts >= max_attempts` | no (`gave_up`), unless `--force` |
| `running` | newest row `status='running'` and younger than 30 minutes | no (another process owns it) |

`failed_attempts` counts failed rows newer than the newest ok row for that company and kind, **excluding `api_error` rows**: an API or network failure is not the company's fault, so it never exhausts a company (it is still retried once per invocation and visible in the Observability tab).

**Order of work** (`order_for_work`) within the working set: failed retries, then partial companies (enrich fresh, news due), then never-enriched, then stale, ties by rank, companies with nothing due last. Cheap tails finish before new enrichment spend, so a budget stop leaves the fewest half-done companies. The selector does not rank by confidence.

**Retry policy.** At most one attempt per company and step per invocation, retried by the next invocation (a failed latest attempt is selected, D32) up to 3 failed attempts across runs. The only in-run retry is the one 429 re-run below.

**Stale `running` rows.** A killed process leaves a `running` row with no stats. At startup `reap_abandoned` turns every row still `running` after 30 minutes into `status='error'`, `failure_category='internal_error'`, `error_message='abandoned: process ended before the run finished'`: per-company rows count as a failed attempt, batch rows (`companies_ingest`, `pipeline_run`) just get an honest `error` record. Two orchestrator processes at once are unsupported: startup refuses (exit 2) if a `pipeline_run` row is `running` and younger than 30 minutes, unless `--force` (reaping does not shorten this window; it only fixes the stale record).

**Company result** (one per working-set company; the acceptance rate is `ok` over the working-set size):

| Result | Meaning |
|---|---|
| `ok` | every step is fresh or ran ok |
| `partial` | enrich ok but news failed, was stopped or skipped for budget |
| `failed` | enrich failed, or there is no usable profile to work from |
| `gave_up` | a needed step is `exhausted` |
| `skipped_budget`, `stopped` | enrich skipped for the batch cap, or interrupt before the company finished |

## How a run flows

Ingest and the orchestrator do not call each other; they share only the SQLite database. The orchestrator works on companies already in `company` (except `--only`, which can add one through Companies House).

### One orchestrator invocation

```mermaid
flowchart TD
    S([python -m sprout.orchestrator]) --> P[Parse args and build StatusPolicy<br/>stale 30d enrich, 7d news, no-website 90d]
    P --> K{Anthropic key?}
    K -- missing --> X2([exit 2])
    K -- ok --> L{Another pipeline_run<br/>'running' and not timed out?}
    L -- yes, no --force --> X2
    L -- no --> R[reap_abandoned: stale running rows<br/>become internal_error]
    R --> W["select_working_set<br/>sort by candidate_score desc, id<br/>take first --limit<br/>(--only bypasses both and can add via Companies House)"]
    W --> ST[company_statuses: per step state<br/>derived from run_log<br/>never / stale / failed / exhausted / fresh / running]
    ST --> OR[order_for_work: failed retries, partial,<br/>never-enriched, stale, nothing to do last]
    OR --> DR{--dry-run?}
    DR -- yes --> X0([print plan and estimate, exit 0])
    DR -- no --> PR[("INSERT run_log<br/>kind=pipeline_run, status=running")]
    PR --> POOL[Worker pool, up to 8 threads<br/>each with own session and HTTP clients<br/>shared BatchBudget cap and 429 gate]
    POOL --> PC[[process_company, per company]]
    PC --> OUT[Collect outcomes]
    OUT --> FIN[("UPDATE pipeline_run: status=ok,<br/>stats = results, spent_usd, by_kind,<br/>stopped_early, ...")]
    FIN --> EX([exit 0, 1 or 2])
```

### Inside `process_company`

This is where the per-company `run_log` rows are written.

```mermaid
flowchart TD
    C([company_id]) --> N{"enrich needed?<br/>(never, stale, failed;<br/>--force ignores fresh and the 3-attempt cap)"}
    N -- no, and a profile exists --> NW
    N -- no, and no profile --> F0([result: failed])
    N -- yes --> NWS{"--force not set and the last website<br/>screen found nothing within 90d?"}
    NWS -- yes --> SKIP([skipped_no_website<br/>no API call, no cost])
    NWS -- no --> WS["website step<br/>run_log: company_website_screen"]
    WS --> WR{URL found?}
    WR -- no --> SKIP2([skipped_no_website<br/>enrich not run])
    WR -- failed, budget or stop --> END1([failed, skipped_budget, stopped])
    WR -- yes --> EN["enrich step<br/>run_log: company_enrich<br/>(facts, funding, officers)"]
    EN --> ER{ok?}
    ER -- no --> END1
    ER -- yes --> NW
    NW{"news needed?<br/>(never, stale >7d, failed)"} -- yes --> NM["news step<br/>run_log: company_news_match<br/>(company_news rows)"]
    NW -- no, fresh --> OK
    NM --> OK([ok, or partial if news failed])
```

Each agent call first reserves part of the budget with `try_slice`. After a 429 the gate pauses all workers for 30 seconds and the step is retried once.

### Step states in plain words

The states are computed per company and per step (enrich, news) by `_step_status` in `status.py`, from that company's rows of the step's kind. The exact conditions are in the table under [Selector, status and retry](#selector-status-and-retry).

| State | Meaning | Runs the step? |
|---|---|---|
| `never` | No successful run yet, and the newest run is not a failure | Yes |
| `fresh` | Last successful run is within the TTL (enrich 30 days, news 7 days) | No |
| `stale` | Last successful run is older than the TTL | Yes |
| `failed` | Newest run failed and fewer than 3 failed attempts since the last success | Yes (retry) |
| `exhausted` | Newest run failed and 3 or more failed attempts since the last success | No |
| `running` | Newest run is `running` and under 30 minutes old (another process) | No |

"Enrich needed" in the diagram means `never`, `stale` or `failed`. `--force` also treats `fresh` and `exhausted` as needed, but never takes over a `running` step. API errors do not count towards the 3 attempts.

### Who triggers what

The corrected picture is: orchestrator → (asks) `status.py` → (reads) `run_log`; orchestrator → (runs) agents → (write) `company_*` rows.

- Only the orchestrator starts work: `process_company` calls `AGENTS[step]`, and each agent writes its own `company_website_screen`, `company_enrich` or `company_news_match` row.
- `status.py` is a read-only helper with no side effects on agents (the one write is `reap_abandoned`, which marks stale `running` rows as failed). Its only caller is `orchestrator.py`: `reap_abandoned` at startup, `company_statuses` to order the working set and again per company, and `recently_without_website` before the website screen.
- `pipeline_run` does not trigger the other kinds. It is the wrapper row (`company_id` NULL, no foreign key to the per-company rows) whose `stats` summarise the batch. Which companies are due never reads it, except for the single-run lock.
- The Progress tab's "Logged runs" counts every `run_log` row of every kind; the Observability "Runs" counts only `company_enrich`, so the two numbers differ.

## Concurrency (D38)

- `run()` pre-creates `concurrency` workers (default 4, clamp 1 to 8) in an `asyncio.Queue`. Each company task takes a worker, runs `await asyncio.to_thread(process_company, ...)`, and returns it. Each worker owns its own `HttpClient`, `WebFetcher`, Companies House client and `Session` (`HttpClient` is not thread-safe, D3); the Anthropic client is thread-safe and shared. Sessions and ORM objects never cross threads. Politeness and the Companies House window are therefore per worker.
- `BatchBudget` is charged from worker threads under a `threading.Lock`. Each agent call first takes `batch.try_slice(cap)`, which atomically returns `min(cap, remaining)` or `None` (then `skipped_budget`); the call runs with that slice as its `max_usd`. Spend overshoots the cap by at most one response per in-flight call.
- `get_engine` enables WAL and a 30 s busy timeout. No write transaction is open across an API call (`logged_run` commits the row first; results are written after the call), so writers queue only for short commits.
- **429 handling.** The SDK client is built with `max_retries=5`. A step that still ends `stop="error"` with `error_type=="RateLimitError"` trips the shared `RateLimitGate` (all workers wait 30 s before starting any step) and is re-run once; that re-run is not an extra attempt in the policy because `api_error` rows are excluded. After 5 trips the orchestrator stops launching companies (they end `stopped`), finishes in-flight ones and exits 1 with `rate_limited`.
- **Interrupt.** The first SIGINT sets `ctx.stop`: no new step or company starts, in-flight agent calls finish, the summary prints, exit 1. A second SIGINT hard-exits (`os._exit(130)`), leaving the `pipeline_run` row `running`, which blocks a restart for 30 minutes unless `--force`; abandoned `running` rows, per-company or batch, are reaped on a later start. Each agent run commits its row first and its results at the end in one transaction, so an interrupted run leaves either nothing or a complete step.

## CLI

`python -m sprout.orchestrator [--limit 50] [--concurrency 4] [--max-total-usd 5.00] [--stale-days 30] [--news-stale-days 7] [--no-website-days 90] [--steps enrich,news] [--only NAME_OR_NUMBER ...] [--force] [--dry-run]`

- `--dry-run` selects, prints each company's status, needed steps and an estimate (`ESTIMATED_STEP_USD` in `orchestrator.py`, planning means), and spends nothing; it needs no API key and takes no run lock.
- `--steps` restricts which steps may run (for example `news` alone).
- `--no-website-days` is how long a company whose website screen found nothing is left alone (default 90); the next run skips the screen and ends it `skipped_no_website` at no cost. A failed screen is not "no website" and is retried.
- `--force` ignores freshness, the 3-attempt cap and the no-website window for the selected companies, and the single-run lock; it never takes over a step another process is running.
- `--max-total-usd` is the single cap over enrichment and news spend. The default US$5.00 is the acceptance ceiling.
- Output: one stderr line per finished company (result, per-step state and cost), a final summary on stdout, and one batch `run_log` row, kind `pipeline_run`, `company_id` NULL, `stats`: `limit`, `concurrency`, `max_total_usd`, `selected`, `results` (counts per company result), `spent_usd`, `by_kind`, `rate_limit_trips`, `incomplete_profiles` (company id to missing parts, ok companies only), `stopped_early` (`interrupted`, `rate_limited`, `budget` or null). The spend key is `spent_usd`, **not** `cost_usd`, because the Observability queries sum `stats.cost_usd` over every kind and would count the batch twice.
- Exit codes: 0 when not stopped early and `ok / eligible working set >= 0.85` (or no companies are eligible); 1 below that or stopped early (budget, rate limit, interrupt); 2 for configuration (no Anthropic key, another run in progress, unresolvable `--only` target). Phase 9 maps these to job outcomes.

### `sprout.enrich` versus `sprout.orchestrator`

Both run the same enrichment agent (`enrich_with_result`). The orchestrator adds selection, status and concurrency around it.

| | `python -m sprout.enrich` | `python -m sprout.orchestrator` |
|---|---|---|
| Purpose | Enrich the companies you name, one by one: spot checks, debugging, wipe and redo | Run the pipeline over a ranked working set: real batches |
| Choosing companies | Names or Companies House numbers on the command line | The first `--limit N` by `candidate_score` then `id`, or `--only` to name them |
| Steps | Website screen, then enrichment; no news step | Website screen, enrichment and news; `--steps` restricts them |
| Finished work | Always runs, even for a fresh company | Skips fresh steps (30 days for enrichment, 7 for news), retries failed ones, stops after 3 failed attempts; `--force` overrides |
| Concurrency | Sequential | Worker pool, `--concurrency` (default 4) |
| Spend cap | `--max-total-usd`, none unless given | `--max-total-usd`, default US$5.00 |
| Preview | None | `--dry-run` |
| Clean re-enrichment | `--reset` deletes the company's facts, funding rounds and category first | None; `--force` re-runs but keeps old facts |
| Other flags | `--show`, `--strong`, `--effort`, `--max-usd`, `--max-iterations` | `--stale-days`, `--news-stale-days`, `--no-website-days` |
| Run record | One `company_enrich` row per attempt | The same per-step rows plus one `pipeline_run` row; one run at a time (lock) |

A company added by number has `candidate_score` 0 and is never picked by `--limit`; enrich it with `--only` or `sprout.enrich`.

## Evaluating the news agent (D28)

The news agent is to be measured before it stays in the default pipeline. No golden labels exist, so hand-check 15 to 20 items: same company, not a duplicate, `big` and `kind` match the rubric, URL in the ledger (code-enforced). Keep it if at least 90 percent of items are about the right company; otherwise say so. The check is done: see [D28/A34](../5.%20risks.md) for the result. The news agent changes no profile value, so the category, business-status and fabricated-funding gates cannot move because of it.

## Changes to existing code (built)

| File | Change |
|---|---|
| `src/sprout/db.py` | `get_engine`: 30 s busy timeout, WAL and `synchronous=NORMAL` on connect (a read-only file keeps its mode); `RunKind.PIPELINE_RUN`; `save_company_news`; `latest_round` (the D22 rule, shared with `evals/metrics.py` as `last_round`) |
| `src/sprout/costs.py` | `BatchBudget` gains a `threading.Lock` around `charge` and `clamp` and the new `try_slice(cap)`. `enrichment_batch_size`, `COST_PER_COMPANY_USD` and `BUDGET_PER_RUN_USD` remain, unused by the orchestrator (only `tests/test_costs.py` calls them) |
| `src/sprout/agents/loop.py` | `make_client(api_key, *, max_retries=2)`; no `on_step` callback |
| `src/sprout/agents/tools.py` | `ALLOWED_NEWS_TOOLS`, `assert_tools_allowed`, `build_tools(max_searches=)` |
| `src/sprout/agents/enrich.py` | `Deps`, run recording and the failure base class moved to `agents/common.py`; `EnrichmentFailed` subclasses `AgentFailed`; `UNTRUSTED_CONTENT_RULE` and `CATEGORY_GUIDANCE` extracted as shared prompt constants (`SYSTEM_PROMPT` hash unchanged) |
| `src/sprout/sources/http.py` | `DiskCache.put` writes to a unique temp name (pid and thread id) |
| `src/sprout/models.py` | `NewsKind` |
| `.gitignore` | `*.db-wal`, `*.db-shm` |

No table or column changes (D10 is not triggered): the Phase 6 data lands in existing tables ([data model](../2.%20data-model.md)).

## Key files

`agents/orchestrator.py`

| Aspect | Detail |
| --- | --- |
| Role | <ul><li>Supervisor: working-set selection, worker pool, `process_company`, 429 gate, interrupt handling, `pipeline_run` row</li></ul> |
| Gotchas | <ul><li>`process_company` is sync, never raises and never prints, so Phase 8 can call it</li><li>Each worker owns its own session and HTTP clients; only the Anthropic client is shared</li><li>A second SIGINT hard-exits and leaves the `pipeline_run` row `running`</li></ul> |

`status.py`

| Aspect | Detail |
| --- | --- |
| Role | <ul><li>Derives per-step state from `run_log` in one query; `reap_abandoned` closes stale `running` rows</li></ul> |
| Gotchas | <ul><li>`api_error` rows never count towards the 3-attempt cap</li><li>Reads `db` only, so Phases 7 and 8 can reuse it</li></ul> |

`agents/common.py`

| Aspect | Detail |
| --- | --- |
| Role | <ul><li>`run_recorded` is the shared run wrapper for every agent; `AgentFailed` carries `error_type`</li></ul> |
| Gotchas | <ul><li>The run row is committed before any failure is raised</li><li>`persist` runs inside the run block and may return extra stats</li></ul> |

`agents/news.py`

| Aspect | Detail |
| --- | --- |
| Role | <ul><li>One search per company, model-scored entries, writes `company_news`</li></ul> |

## Known weak spots and open items

- **Noisy candidate pool (A1, D34):** validated live and found too loose; details in [implementation-notes](../../implementation-notes.md#candidate-pool-quality-a1). Whether to tighten the pool is undecided.
- **News:** cross-outlet coverage of one event can still duplicate (same-date exact match is collapsed in code; different dates are not), and some items have no published date.
- **`profile_gaps`** treats an explicit "not disclosed" (NULL) fact as a gap, so most ok companies are reported incomplete.
- **Exercised live since:** a single and a double SIGINT mid-run, and the 429 gate (simulated client). See [risks.md](../5.%20risks.md).
- **A34:** resolved; see [risks](../5.%20risks.md).
- **Not built:** `on_step` callback, live per-iteration progress, an `enrichment_state` table, low-confidence-first ordering.

This phase reuses the generic loop, tool pattern and cost accounting from [Phase 3](phase-3-enrichment.md) (D12), `BatchBudget` from [Phase 5](phase-5-evals-guardrails.md) (D24), `enrich_with_result(extra_context=)` and `stats["sources"]`. Step records are written per run by the worker's own session after `run_agent` returns, as described in the [Phase 4 page](phase-4-observability.md#what-phase-6-uses-from-this-phase).
