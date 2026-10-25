# Sprout: Learnings

Skills and lessons picked up phase by phase. Planned learnings come from `docs/project-plan.md`; where a phase is actually done (see `docs/implementation-notes.md`), its planned learnings are followed by what was actually learned while building it.

## Phase 0 - Setup & definitions

**Planned:** modern Python packaging (`uv`, `pyproject.toml`), secrets handling, Pydantic data modelling, designing a schema before building agents.

**Actual (done):**
- `uv` needs Python 3.12 set explicitly (`requires-python`, `.python-version`); macOS ships 3.9, so everything must run through `uv run` or the wrong interpreter gets picked up.
- Enforcing "provenance over completeness" in the schema itself, not just the prompt, works: `Sourced[T]` rejects a non-null value without a `source_url`, so a model can't silently skip citing a fact.
- PEP 695 generics (`Sourced[T]`) must be instantiated parametrised (`Sourced[int](...)`); bare `Sourced(...)` leaves `T` unchecked.
- `anthropic` 1.x is built on `httpx2`, not `httpx`; a plain `httpx.Client` can't be passed to the SDK.
- On the 5.x models, forced `tool_choice` (`any`/`tool`) 400s; thinking tokens also count against `max_tokens`, so budgets must account for them.
- Locking the taxonomy and schema before any agent code avoids churn: changing either later invalidates classifications and the golden set.
- **Pydantic data modelling:**

| Topic | Key learning |
| --- | --- |
| `BaseModel` vs plain class/`dataclass` | `BaseModel` validates and coerces types at the point data enters the system and raises `ValidationError` on mismatch. A plain class does none of this. `dataclass` gives `__init__`/`__eq__`/`__repr__` for free but still ignores type hints at runtime, bad data passes through silently until it breaks something downstream. |
| `Field()` | Attaches per-field constraints and metadata on top of a type annotation: `min_length`/`max_length`, `ge`/`le`/`gt`/`lt`, `pattern`, `alias`, `description`. Complements, doesn't replace, the type-level contract the annotation already gives. |
| `_Strict(BaseModel)` + `ConfigDict(extra="forbid")` | A shared internal base class that rejects any field not explicitly declared on the model, instead of silently ignoring (`"ignore"`, the default) or accepting (`"allow"`) it. Defined once, inherited everywhere, so malformed/hallucinated LLM output fails loudly rather than passing through unnoticed. |
| `default_factory` | Required, not just style, for mutable defaults (`list`, `dict`, `set`): Pydantic v2 rejects a bare mutable default outright because Python evaluates default values once at definition time, so every instance would otherwise share and mutate the same object. Also fits any default that must be computed per instance rather than once at class definition (e.g. `uuid.uuid4()`, `datetime.now()`). Mutually exclusive with `default`, passing both raises an error. |
| Typed fields (`AnyHttpUrl`, `AwareDatetime`) | Push format and timezone validation into the schema itself: `AnyHttpUrl` rejects malformed URLs, `AwareDatetime` rejects naive (timezone-less) datetimes. Moves a class of bugs from "caught later, maybe" to "caught at the model boundary, always." |
| Class inheritance refresher | `class _Strict(BaseModel):` makes `_Strict` a subclass of `BaseModel`; it inherits all of `BaseModel`'s behaviour and overrides only `model_config`. Other models then inherit from `_Strict` instead of `BaseModel` directly, picking up the strictness rule without repeating `ConfigDict(extra="forbid")` on every model (DRY). |
| Why `value: T \| None` on `Sourced[T]` | `None` means a genuinely absent fact, not a design gap: a company with no funding round yet, or a field the agent researched and found no sourced claim for. Every `CompanyProfile` field is a `Sourced[T]`, so if `value` were required, each field would need wrapping in `Optional[Sourced[T]]` instead, doubling the optionality for the same concept. The validator only forbids the one invalid combination, a value with no `source_url`, leaving "no claim at all" (`value=None`) as a legitimate state. |
| `Sourced[T]` is a Pydantic model despite not importing from `pydantic` | `Sourced[T](_Strict)` and `_Strict(BaseModel)`, so it's a `pydantic.BaseModel` subclass two levels down, not a class Pydantic ships directly. Inheriting from `BaseModel` (however many levels removed) is what makes a class "a Pydantic model": validation, `.model_dump()`, `model_validator`, etc. all come along automatically, no re-import needed. |
| "Generic" vs "custom" aren't opposites here | The `[T]` in `Sourced[T]` is PEP 695 (Python 3.12) generic class syntax, plain Python, not a Pydantic feature; it makes `T` a type parameter filled in at use-site (`Sourced[str]`, `Sourced[Stage]`). Pydantic separately knows how to validate generic models and resolves `T` to the concrete type. So `Sourced[T]` is accurately described as a custom-written, generic subclass of `pydantic.BaseModel`, all three words apply at once. |
| What a PEP is | Python Enhancement Proposal: the numbered design-document process for language/stdlib changes. PEP 695 (new generic syntax, Python 3.12) is why `class Sourced[T](_Strict):` works and why `requires-python` is pinned to 3.12; PEP 8 is the style guide; PEP 484 introduced type hints. |

## Phase 1 - Data foundations

**Planned:** REST API consumption, HTTP resilience (retries, backoff, rate limits, caching), idempotent ingestion, ORM/SQL modelling, web-scraping etiquette and its legal/ethical limits.

**Actual (done):**

| Topic | Key learning |
| --- | --- |
| Companies House environments | Sandbox and live are separate; a key issued for a "test" application only works against the sandbox and returns no real companies, even though the error (401) looks like a bad key. |
| SIC codes as an AI signal | They say little on their own: most AI startups register under generic software codes. A name-based keyword filter (tiered strong/weak keywords, weak ones needing a tech SIC code too) is needed to keep false positives down, and the result is still a noisy candidate pool, not a verified list. |
| `/advanced-search` vs `/search` | `/advanced-search/companies` returns SIC codes in results where plain `/search/companies` does not, which matters when SIC is part of the filter. |
| Idempotent ingestion | One HTTP entry point with per-call cache TTLs, a sliding-window rate limiter, and retry-with-backoff makes ingestion idempotent for free: a second run makes zero HTTP calls and adds zero rows. |
| SQLModel JSON columns | In-place JSON mutation on SQLModel columns isn't tracked by SQLAlchemy; a whole new dict/list must be assigned to trigger a write. |
| Timezone-naive datetimes | Rejected outright by this SQLModel version, so "UTC everywhere" has to be enforced from the first write. |
| Web-scraping etiquette as security | Resolving every host, and every redirect hop, to a public IP blocks SSRF via DNS rebinding to loopback/private/metadata addresses, on top of honouring `robots.txt`. |
| Paginating without a total count | `/advanced-search/companies` never reports a total result count, so paginating it has to treat "a page came back shorter than requested" as the only end-of-results signal, rather than computing how many pages are left. |
| Taxonomy locked, left unwired | The `Category` taxonomy is deliberately locked in Phase 0 but left unwired through all of Phase 1: `ingest.py`/`candidates.py`/`db.py` never reference it, since the heuristic filter only produces uncategorised candidates by design. Assigning a category is Phase 3's job (a dedicated classifier agent), not something to bolt onto discovery early. |

**Key files:**

`ingest.py`

| Aspect | Detail |
| --- | --- |
| Entry point | One command, `python -m sprout.ingest`. Runs companies and news ingestion as two independent pipelines, each wrapped in `logged_run` for stats; one failing doesn't block the other. |
| `ingest_companies()` | Loops over `DISCOVERY_TERMS`, pages Companies House per term via `iter_advanced_search`, dedupes company numbers already seen **within the run** (overlapping terms can return the same company), filters each hit through `assess()`, and upserts accepted candidates plus registry facts (HQ locality, founding year), each sourced to the public register page. |
| `ingest_news()` | Fetches each RSS feed in turn; a single feed's `FetchError` is recorded in `failed_feeds` and skipped rather than aborting the run. Dedup on URL happens at the DB layer (`add_news_items`). |
| `max_per_term` | Default 200, caps Companies House results read per search term; overridable via `--max-per-term`. Exists to bound API calls (12 terms x N results), not accuracy. |

`candidates.py`

| Aspect | Detail |
| --- | --- |
| Purpose | Heuristic-only filter: Companies House describes legal entities, not products, so this produces *candidates*, not verified AI companies. |
| Hard rejects | Non-`active` status, or incorporation before 2012-01-01 (`MIN_INCORPORATION_DATE`). |
| Strong keywords | "machine learning", "neural", "llm", "computer vision", etc. Qualify a company on their own (2 points each). |
| Weak keywords | "ai", "analytics", "autonomous", "cognitive", etc. Only qualify a company if it *also* has a tech SIC code (1 point each); this is what keeps "Ai Restaurant Ltd" or "Autonomous Vehicle Analytics" (if it's a non-tech SIC business) out. |
| Scoring | `2 x strong + weak + (1 if tech SIC)`, but the score itself doesn't gate acceptance, only the strong-or-(weak-and-SIC) condition does; score is just a tiebreaker/signal kept on the `Assessment`. |

`sources/companies_house.py`

| Aspect | Detail |
| --- | --- |
| Auth | HTTP Basic, API key as username, empty password, that's how Companies House auth works. Rate-capped at 550 calls/5min, just under the real ~600 limit. |
| `CompanyHit` | Frozen dataclass holding only what Sprout needs: number, name, status, incorporated date, SIC codes, locality, plus a derived `.url` to the public register page (used later as `source_url`). |
| Methods actually used | Only `iter_advanced_search()` (which calls `advanced_search()` internally) is called from `ingest.py`. `search()`, `get_company()`, `filing_history()` exist and are tested; as of Phase 2 `get_company()` and `filing_history()` are used by the `lookup_companies_house` agent tool `get_company()` by the enrichment CLI to add a company by number, and `search()` for name suggestions. |
| Pagination | `iter_advanced_search` requests pages of up to 100 at increasing `start_index`, yielding hits as they arrive, and stops the moment a page comes back shorter than requested, since the API never reports a total result count up front. |
| 404 handling | `_get_json` treats a 404 as "nothing found" (`None`), not an error, since `get_company`/`filing_history` legitimately expect that for an unknown number. |

`sources/http.py`

| Aspect | Detail |
| --- | --- |
| Role | The one GET implementation behind every network source (Companies House, RSS, web fetcher), so caching/politeness/retries are written once and shared. |
| `DiskCache` | One JSON file per request, keyed by a SHA-256 hash of the URL plus sorted params (so param order can't create duplicate entries). Caches both 2xx and 404 (a stable "doesn't exist" answer). Writes atomically (temp file + `os.replace`) so a crash mid-write can't corrupt the cache. |
| Rate limiting | `RateLimiter` is a sliding window (deque of call timestamps, evicts anything older than the period, sleeps if full). `PerHostRateLimiter` keeps one of these per hostname so a slow host never delays requests to a different one. |
| Retries | Retries on transport errors or specific statuses (429, 500, 502, 503, 504) with exponential backoff (`base * 2^attempt`), but respects a server's `Retry-After` header when present, capped at 60s. Gives up after `max_retries` (default 4). |
| Size cap | `_send` streams the body in chunks and aborts with `FetchError` past `max_bytes` (default 5MB), guards against a huge page or feed blowing up memory/cost. |
| Credential isolation | Credentials live only in the underlying `httpx.Client`; they never become part of the cache key or get written into a cache file. |

`sources/rss.py`

| Aspect | Detail |
| --- | --- |
| Feeds | 6 fixed sources: Sifted, UKTN, Tech.eu, The Guardian (AI), TechCrunch (Startups), BBC (Technology). |
| What's kept | Only title, link, and a short summary (truncated to 500 chars via `_summary`, using `html_to_text` from `web.py` to strip HTML out of the summary field), never full article bodies. |
| `normalise_url()` | Strips the fragment and known tracking params (`utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `ref`, `cmp`) so the same article reached through different tracking links still dedupes to one URL. |
| `parse_feed()` | Raises `FetchError` if `feedparser` can't recognise the response as RSS/Atom at all (e.g. the server returned an HTML error page). Individual entries that fail `NewsItem` validation (bad/missing URL) are silently skipped rather than failing the whole feed. |
| Caching | `fetch_feed()` caches responses for 6 hours (`FEED_CACHE_TTL`) and raises on a 404. |

`sources/web.py`

| Aspect | Detail |
| --- | --- |
| Role | General-purpose "polite" fetcher for arbitrary URLs, not RSS or Companies House, built defensively because the URLs it fetches may originate from untrusted scraped content. |
| SSRF protection | `_check_public_host` resolves the hostname and requires every resolved IP to be public/global, blocking localhost, private ranges, and link-local addresses (including cloud metadata endpoints like `169.254.169.254`). |
| Redirect handling | Auto-redirects are disabled on the underlying `httpx.Client` specifically so `_get_following_redirects` can re-run the public-host and robots checks on **every** redirect hop, not just the first URL; otherwise a safe-looking URL could redirect to an internal address and slip past the check. Capped at `MAX_REDIRECTS` (5). |
| `robots.txt` | Fetched and parsed per origin (in-memory for the session, 1-day disk cache), checked via `can_fetch()` before any real GET. If robots.txt itself is unreachable (4xx other than 429), everything is treated as allowed, per RFC 9309. |
| Rate limiting | 1 request per host per 2 seconds by default (`DEFAULT_MIN_INTERVAL_SECONDS`), politer than the Companies House limiter, appropriate for scraping third-party sites that didn't opt into being hit hard. |
| `html_to_text()` | A hand-rolled `HTMLParser` subclass: skips `<script>`/`<style>`/`<noscript>`/`<template>`/`<svg>`/`<head>` entirely, captures `<title>` separately, inserts newlines around block-level tags, and collapses whitespace per line. |
| Size cap | `fetch()` truncates extracted text to `max_chars` (20,000 default), bounds downstream token cost for anything that reads the page content. |

## Streamlit progress & test console (ahead of Phase 6)

Not a plan phase, but built early to check progress. **Actual learnings:**
- Streamlit Community Cloud has no persistent disk and the database is gitignored, so a deployed copy always starts empty; a bundled snapshot DB is unavoidable for a populated public demo.
- Gating state-changing UI (an "ingest now" button) behind an environment variable keeps a public deployment read-only by default without separate code paths.
- Driving a "progress" view off the shape of the docs themselves (status/checkbox conventions in this file) is cheap but brittle: the convention has to be kept consistent or the view misreports.

## Phase 2 - First agent: single-company enrichment

**Planned:** how tool use actually works (the loop, stop reasons, tool results), structured output via tool schemas, prompt design for grounded extraction, prompt caching, token/cost accounting, error-feedback repair loops.

**Actual (done, with caveats: the tiny-cap criterion is verified with mocks only, and the 10-profile review so far is the implementing agent's, not the user's):**

| Topic | Key learning |
| --- | --- |
| Forced `tool_choice` | Not used. The loop sends `tool_choice: {"type": "auto"}` and the system prompt names `submit_profile` as the required final step. Forced choice was reported to 400 on the 5.x models and was never re-tested live on Haiku 5.5. If the model ends its turn without submitting, the loop sends one reminder, then stops with `no_submit`. |
| `strict: true` on a derived schema | The API rejected the schema derived from `CompanyProfile` ("29 parameters with type arrays or anyOf, limit 16"), because every nullable `Sourced` field adds an `anyOf`. `submit_profile` is therefore not strict, and Pydantic validation plus one repair attempt is the schema guarantee. Any future strict tool must stay under the 16 union-parameter limit. |
| Deriving the tool schema | `submit_profile_schema()` takes `CompanyProfile.model_json_schema()`, inlines `$ref`s, drops keywords the tool schema does not accept (`title`, `default`, `minimum`, `format` and so on), and marks all fields required. Deriving it means the schema cannot drift from the model, but `_simplify` drops any dict key with those names, so a future `CompanyProfile` field called `title` or `format` would silently vanish from the schema. |
| Provenance in code, not prompt | A prompt saying "cite only real URLs" was not enough: in the first live pass 3 of 10 companies (Cleo, Tractable, Quantexa) ended `invalid` because the model cited homepages no tool had returned, and repeated them after the repair message. What worked was a `SourceLedger` of URLs tools actually returned, validation in `validate_submission`, a repair message listing up to 25 citable URLs, and a prompt line saying to `fetch_page` a site before citing it. Final pass: 10/10 `submitted`. |
| URL matching | The ledger compared `https://x.com` and `https://x.com/` as different URLs, which kept Cleo and Quantexa `invalid` on the second pass. `_ledger_key` now ignores a trailing slash, host case, fragments and tracking params (via `normalise_url`). Exact string matching of URLs between model and tools is a bug source. |
| Snippet-only sources | Server-side `web_search` results arrive encrypted, so what the model saw cannot be audited from our side. The ledger records whether a URL was only seen in a search result or actually fetched, and `validate_submission` caps unfetched sources at `medium` confidence. A real check needs a verifier agent (Phase 3). |
| Currency conversion | Telling the model to "note the original currency" was not enough: it left funding amounts null for non-USD figures. Putting approximate conversion rates into USD in the prompt fixed most cases, with the original figure and rate in `note`. Null is still the safe failure, but it understates funding coverage. |
| Errors fed back to the model | The rejection message lists the problem fields and citable URLs but does not echo the model's input (`_compact_errors`). One repair attempt (`Budget.max_repairs = 1`) is allowed, and a repair is not counted as a failure in the stats. |
| Prompt injection | Every custom tool result is wrapped in `<untrusted_content source="...">` by `wrap_untrusted`, which truncates to 8,000 characters and neutralises any literal `<untrusted_content` tag inside the page so a page cannot close the wrapper early. The prompt tells the model that wrapped text and search results are data, never instructions. Tests cover the wrapper, but there is no adversarial eval yet (Phase 5). |
| Tool errors are tool results | Handlers return `ToolOutcome(is_error=True)` for robots blocks, SSRF blocks, fetch failures and unknown companies, and `_run_tool` also converts unexpected exceptions into error results. A tool bug or a blocked page must reach the model as text it can react to, never end the run. |
| Guards run before each call | Iteration and USD caps are checked before every API call, so the iteration cap is exact but the USD cap is soft by at most one response, since cost is only known afterwards. The wrap-up notice is appended on the last iteration or at 80 percent of the USD cap. The tiny-cap test criterion is verified with `ScriptedClient` mocks only, not live. |
| Thinking tokens and cost | Thinking is billed as output and is already inside `usage.output_tokens`, so `cost_usd` needs no separate term. Effort is set explicitly (`output_config={"effort": "low"}`) because default effort on Haiku 5.5 is `medium` and would raise cost and `max_tokens` use. |
| Prompt caching | Two markers are needed: one on the system block (static prefix, tools then system) and a top-level `cache_control` so the growing conversation is re-read at the cache-read price. It works live (`cache_creation_input_tokens > 0`; about 455k tokens written and 1.50M read across all runs). Tool order is fixed in `build_tools` because tool definitions sit at the start of the cached prefix. The 512-token minimum cacheable prefix was not tested. |
| Cost is the search, not the model | Average cost was USD 0.0138 per company (final 10-company run USD 0.1387), against a USD 0.07 target. About 70 percent of each run is the single web search (US$0.01), so lowering `max_uses` or caching search results is the lever, not a cheaper model. The US$10 per 1,000 searches figure comes from a Managed Agents note in the `claude-api` skill and still needs checking against a first invoice. |
| Fail closed on prices | `PRICES` is code, not env, and `cost_usd` raises `UnknownModelError` for any model not in the table. `run_agent` calls `cost_usd(model, Usage())` before the first request, so a typo in `SPROUT_BULK_MODEL` cannot bypass the spend caps. |
| Server-side vs custom tools | Server-side `web_search_20260209` only returns URLs and snippets, so any page the agent reads goes through our `fetch_page` and keeps the robots, SSRF, size and cache protections of `WebFetcher`. Server-side `web_fetch` was rejected because it would bypass them. `pause_turn` is handled but has only mock evidence. |
| Failure persistence | `logged_run` rolls back on exceptions, so `enrich_with_result` assigns `run.stats` and commits before raising `EnrichmentFailed`. Without that, a failed run (which still cost money) would leave no `run_log` trace. Facts and category are written only after a validated `submit_profile`, so failed runs persist nothing partial. |
| NULL-value facts | `save_profile` stores "researched, nothing found" as a NULL-value fact, so a dashboard can tell it from "never tried". Consequence found in review: a field valued in run 1 and null in run 2 ends with both facts, so a winning-value rule must exist before any front end reads facts. |
| Additive migration | SQLModel `create_all` does not alter existing tables. `init_db` now adds missing nullable columns with `ALTER TABLE` (needed for `company.category` on the existing `data/sprout.db`), and raises with "delete the database" advice for anything that is not a plain nullable column. |
| Testing an LLM loop | `tests/fakes.py` provides a `ScriptedClient` that returns queued responses and records every request, which makes stop reasons, caps and repair paths deterministic. It snapshots the `messages` list per call because the loop mutates the same list. No `-m live` enrichment test was added, so a plain `pytest` with an API key never spends money. |
| Non-determinism | Even with the ledger, an occasional run ends `invalid` or `refused` (one Cleo run was a transient refusal that succeeded on retry), and one Tractable run had web search unavailable and correctly returned nulls. Mocked tests cannot catch this, so the 10-company live run (30 `enrich` runs while iterating, about USD 0.42 in total) was the real acceptance test. Phase 3 needs retries and `enrichment_state`. |
| Wrong-company risk | A claimed website can be wrong (Airborne Robotics' site returned another company's content). The prompt tells the model to disambiguate by Companies House number, and the result is stored at medium confidence with a caveat in the note, but only a verifier would catch it reliably. |

**Key files:**

`agents/loop.py`

| Aspect | Detail |
| --- | --- |
| Role | Hand-written tool-use loop (`run_agent`), generic over tools. The only pipeline module that imports `anthropic`, so SDK drift and the test seam (`MessagesClient`) live in one file. |
| Stop reasons | `Stop` is one of `submitted`, `invalid`, `max_iter`, `budget`, `no_submit`, `truncated`, `refused`, `error`. `pause_turn` just continues, `max_tokens` is `truncated`, `refusal` is `refused`, and `anthropic.APIError` is `error`. The SDK itself retries 408/409/429/5xx twice before the loop sees a failure. |
| Terminal tool | A tool marked `terminal` ends the run when its outcome carries a `payload`. An error outcome from it triggers one repair (the error goes back as a `tool_result`), then `invalid`. |
| Usage | `_usage_of` reads `server_tool_use.web_search_requests` with `getattr` defaults, so responses without it still count. Cost is recomputed from cumulative usage after each call. |

`agents/tools.py`

| Aspect | Detail |
| --- | --- |
| Tools | `web_search` (server-side spec, `max_uses` 3, no handler), `fetch_page` (wraps `WebFetcher`) and `lookup_companies_house` (only added when the company has a number). `build_tools` is the single place to swap the search backend. |
| `SourceLedger` | Maps normalised URL to first-seen time and a `fetched` flag. `fetch_page` adds both the requested URL and the final `page.url`, since a redirect changes it. First-seen time becomes the fact's `retrieved_at`. |
| Companies House tool | Uses `get_company` and `filing_history` (the previously unused client methods) and summarises at most 15 filings. Its source URL is the public register page, which it adds to the ledger as fetched. |
| Latent limits | There is no per-run fetch cap, only the iteration cap. |

`agents/enrich.py` and `enrich.py`

| Aspect | Detail |
| --- | --- |
| Role | Everything profile-specific: system prompt, `submit_profile` schema, `validate_submission`, `enrich_company` / `enrich_with_result`, `resolve_company` and the CLI. `sprout/enrich.py` is an 8-line shim so `python -m sprout.enrich` works. |
| Defaults | 12 iterations, USD 0.13, effort `low`, `MAX_OUTPUT_TOKENS` 6,000, Haiku 5.5 (Sonnet 5.5 via `--strong`, not run live). |
| Identity | The model never supplies `name` or `news`, and `retrieved_at` is stamped from the ledger by code. `name` comes from the DB row. |
| Size | About 460 lines. Split the CLI and `resolve_company` out when Phase 3 adds an orchestrator. |

`costs.py`

| Aspect | Detail |
| --- | --- |
| Role | Pure price table, `Usage` (summable per run), `cost_usd`. No imports from the rest of `sprout`. Uses `Decimal` throughout. |
| Assumptions to refresh | The web search price and the Haiku 5.5 over-100K-token tier (prices double above 100K prompt tokens; per-call prompts stay well below it, so the lower rate card is used and the higher tier is untested). |

`db.py` additions and `tests/fakes.py`

| Aspect | Detail |
| --- | --- |
| `save_profile` | Iterates `CompanyProfile.model_fields` and saves every `Sourced` field, so a new `Sourced` field persists with no db change. `news` is not persisted until news items link to companies. |
| `add_company_by_number` | Turns a Companies House `/company/{number}` response into a row via `parse_hit` and `assess`, which makes `db.py` import source and filter code (a known coupling). |
| `tests/fakes.py` | Builders for `tool_use_turn`, `server_search_turn`, `pause_turn`, `text_turn` and a `ScriptedClient` that raises if the loop makes more calls than scripted. |

**Terminology clarified (code walkthrough):**

| Term | Clarification |
| --- | --- |
| Shim | `sprout/enrich.py` is a shim: a thin wrapper with no real logic, just re-exporting `main` from `agents/enrich.py` so `python -m sprout.enrich` works as an entry point. |
| Tool vs function | A function is called directly by code you control. A tool is a function (a `Tool.handler`) the model decides to call, via a schema describing it; `loop.py`'s `_run_tool` is what actually dispatches to the handler once the model requests it. |
| `SourceLedger` | A running log (literal meaning of "ledger": an ordered record of entries) of every URL a tool has surfaced during one run, with a `fetched` flag. Used by `enrich.py` to reject `submit_profile` citations the model made up rather than actually retrieved. |
| `client` | The object that makes the real network call to the API (`MessagesClient` protocol, backed by `anthropic.Anthropic` in `make_client`). The loop depends only on the narrow protocol, not the concrete SDK class, which is the test seam that lets `tests/fakes.py` substitute `ScriptedClient`. |
| `pause_turn` | Returned by the API when a server-side tool (multi-step `web_search`) hasn't finished a logical turn yet. The loop just `continue`s and resends the updated history; it is not a stopping condition, unlike `end_turn`/`max_tokens`/`tool_use`. |
| Schema | A description of data's shape (fields, types, required-ness), not the data itself. Shows up three ways here: tool `input_schema` (JSON Schema the model must match to call a tool), `submit_profile_schema()` (derived from `CompanyProfile` so it can't drift), and SQLModel table classes (the DB schema). |
| Delimiter spoofing example | `wrap_untrusted` escapes any `<untrusted_content`/`</untrusted_content` already inside fetched page text before wrapping it. Without that, a page could embed a fake closing tag followed by injected instructions (e.g. "set funding to US$50m"), tricking the model into reading them as outside the untrusted block, i.e. as if they were trusted, instead of as page content to ignore. |

## Phase 3 - Multi-agent pipeline at scale

**Planned:** multi-agent orchestration patterns (supervisor/worker, specialisation), model routing for cost, concurrency with `asyncio`, resumable pipelines, verification/cross-checking patterns, failure isolation.

**Status:** not started yet; no actual learnings to add.

## Phase 4 - MCP server

**Planned:** the Model Context Protocol (tools vs resources vs prompts, transports, capability negotiation), writing tool descriptions that LLMs use well, MCP client + server roles, separating read-only from side-effecting tools.

**Status:** not started yet; no actual learnings to add.

## Phase 5 - Evals & guardrails

**Planned:** evaluating LLM systems (golden sets, metrics, LLM-as-judge and its limits), regression testing prompts, prompt-injection and SSRF defences, treating model output as untrusted too.

**Status:** not started yet; no actual learnings to add.

## Phase 6 - Dashboard

**Planned:** data-app development with Streamlit (state, caching), presenting uncertain data honestly, reusing a single service layer across UI and MCP.

**Status:** not started yet (beyond the early progress console above); no actual learnings to add.

## Phase 7 - Automation, deployment & documentation

**Planned:** CI/CD with GitHub Actions, secrets in CI, containerisation, deployment of data apps, technical writing and documenting trade-offs.

**Status:** not started yet; no actual learnings to add.
