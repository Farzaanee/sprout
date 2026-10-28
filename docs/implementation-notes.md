# Sprout: Implementation Notes

Running log of what was built, why, and what later phases need to know. Newest phase last.

## Phase 0 - Setup & definitions

**Status: done except two items that need your credentials** (see "Open items").

### What was built
| Item | Where |
|---|---|
| uv project, Python 3.12 (`requires-python >=3.12`, `.python-version` = 3.12) | [pyproject.toml](pyproject.toml) |
| Runtime deps: `anthropic`, `pydantic`, `sqlmodel`, `httpx`, `feedparser`, `mcp`, `streamlit`, `python-dotenv` · dev: `pytest`, `ruff` | [pyproject.toml](pyproject.toml), `uv.lock` |
| Settings loader (keys, model IDs) | [src/sprout/config.py](src/sprout/config.py) |
| Taxonomy, stage enum, `Sourced[T]`, `CompanyProfile` | [src/sprout/models.py](src/sprout/models.py) |
| "hello" smoke call to Claude | [src/sprout/hello.py](src/sprout/hello.py) (`uv run python -m sprout.hello`) |
| Tests (valid + invalid examples) | [tests/](tests/) |
| Lint/test config, CI | `[tool.ruff]`/`[tool.pytest]` in pyproject, [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Secrets hygiene | `.env` and `*.db` in [.gitignore](.gitignore); template in [.env.example](.env.example) |

### Acceptance criteria
- [x] `uv run pytest` and `uv run ruff check` pass: 24 passed, 1 skipped. Also verified on a copy of the project without `.venv`/`.git`/`.env` (`uv sync --locked` → ruff → pytest). A true `git clone` check will happen once CI runs.
- [x] Claude "hello" call, **not verified**: no `ANTHROPIC_API_KEY` in this environment, and the `ant` CLI isn't installed. `python -m sprout.hello` fails with a clear `MissingConfigError` until `.env` exists. The live test in `tests/test_live_api.py` is skipped without a key and will run automatically once there is one.
- [x] Taxonomy and schemas in code with unit tests ([tests/test_models.py](tests/test_models.py)).

### Open items (need you)
1. **Anthropic API key**: create one, set a monthly spend limit in the console *before* the first paid call, `cp .env.example .env`, fill in `ANTHROPIC_API_KEY`, then run `uv run python -m sprout.hello` and `uv run pytest -m live`.
2. **Companies House API key**: register at developer.company-information.service.gov.uk and put it in `COMPANIES_HOUSE_API_KEY` (needed from Phase 1).
3. **Git**: `uv init` ran `git init` on branch `main`; there are no commits yet (nothing was committed). `.gitignore` is in place, so `git add .` will not pick up `.env`.

### Decisions locked in
- **Taxonomy (16, frozen):** AI models and applications, Fintech and payments, Enterprise software and cloud, Cybersecurity, Biotech and pharma, Healthtech and digital health, Electric vehicles and batteries, Climate, energy and cleantech, Semiconductors and AI hardware, Robotics and automation, Defense, aerospace and space, E-commerce and retail tech, Consumer social, media and gaming, Logistics and supply chain, Deep tech and quantum, Other. Changing it later invalidates classifications and the golden set. Tests assert the exact 16 labels and contain `Other`.
- **Stage enum:** Pre-seed, Seed, Series A, Series B, Series C, Series D, Series E, Series F, Growth, Unknown.
- **`Sourced[T]`** = `value`, `source_url`, `retrieved_at`, `confidence` (low/medium/high), plus an optional `note` (added for Phase 2's "funding in USD with original currency noted", e.g. `"US$12m converted"`).
  - `value=None` means "not disclosed" and is always valid.
  - **A non-null value without `source_url` fails validation.** This enforces "provenance over completeness" in code, not only in the prompt.
  - `source_url` must be `http(s)`; `retrieved_at` must be timezone-aware; unknown fields are rejected (`extra="forbid"` on every model).
- **Every `Sourced` field defaults to "not disclosed"** (via `default_factory`, so profiles never share an instance). An empty `CompanyProfile(name=...)` is valid and states nothing it cannot back up.
- **`category` is a plain `Category`, not `Sourced`.** It is a classification against our own taxonomy, not a fact found at a URL. Defaults to `Other`. If you'd rather have a per-profile rationale/confidence, add it in Phase 5 with the classifier.
- **Money is whole USD as `int` (`>= 0`)**, not float, to avoid rounding artefacts; original-currency detail goes in `note`.
- **`funding_rounds`** is `list[Sourced[FundingRound]]` (each round has stage, amount_million_usd, announced date, investors, and its own source, confidence and note), stored in the `funding_round` table and replaced wholesale on each `save_profile`. `founded` is bounded to 1800–2100.
- **Models:** `claude-haiku-5-5` (bulk) and `claude-sonnet-5-5` (strong), overridable via `SPROUT_BULK_MODEL` / `SPROUT_STRONG_MODEL`. IDs are taken from the `claude-api` skill's model table (dated 2026-10-06); re-check before Phase 2.
- **Deviation from the plan's layout:** `costs.py` and `db.py` are not created yet (they belong to Phases 1–2). No console-script entry point (`uv init` adds one; removed as unused).

### Things later phases must know
- **`anthropic` is now 1.x, built on `httpx2`, not `httpx`.** Don't pass a plain `httpx.Client` to the SDK; use `DefaultHttpxClient`. Our own code (Companies House, fetcher) can keep using `httpx`; they are separate libraries.
- **Phase 2 (`submit_profile`):** the plan's "final tool whose input schema is the Pydantic model" still works, but on Claude Sonnet 5.5 / Opus 5.5 / Fable 5.1 *forced* `tool_choice` (`any`/`tool`) returns a 400. Use `tool_choice: auto` + a prompt instruction naming the tool, with `strict: true` on the tool, or `output_config.format` structured outputs. Haiku 5.5 should be checked against `shared/model-migration.md` in the skill.
- **Thinking tokens count against `max_tokens`** on the 5.x models (adaptive thinking is on by default; effort defaults to `medium` on Haiku 5.5). `hello.py` uses `effort: low` and `max_tokens=1024` for that reason. Set effort explicitly per agent; budget caps in Phase 2 must count thinking tokens.
- **Server-side web tools** are now `web_search_20260209` / `web_fetch_20260209` on Sonnet 5.5; older type strings in the plan's era are stale. Don't declare `code_execution` alongside them.
- **Pydantic generics:** `Sourced[T]` uses PEP 695 syntax; instantiate as `Sourced[int](...)`. Bare `Sourced(...)` has an unparameterised `T` and won't validate the value type. Database storage (Phase 1) will need to flatten `Sourced` into `fact` rows (value serialised as JSON).
- **Ruff rules:** `E, F, I, UP, B`, line length 100. CI runs `ruff check` and `pytest` only; `ruff format` is applied locally but not enforced.
- **CI action versions** (`actions/checkout@v4`, `astral-sh/setup-uv@v6`) were written from memory and haven't been run yet; confirm the first run is green.

## Phase 1 - Data foundations

**Status: done.** Verified end to end against the live Companies House API and the RSS feeds.

### What was built
| Item | Where |
|---|---|
| Shared HTTP layer: disk cache, sliding-window rate limiter (global and per host), retries with exponential backoff + `Retry-After`, response size cap | [src/sprout/sources/http.py](src/sprout/sources/http.py) |
| Companies House client: `search`, `advanced_search` (+ paginating iterator), `get_company`, `filing_history` | [src/sprout/sources/companies_house.py](src/sprout/sources/companies_house.py) |
| RSS ingestion (6 feeds) → `NewsItem`, URL normalisation | [src/sprout/sources/rss.py](src/sprout/sources/rss.py) |
| Polite web fetcher: robots.txt, per-domain rate limit, public-hosts-only, cache, HTML→text | [src/sprout/sources/web.py](src/sprout/sources/web.py) |
| Candidate filter (SIC + name keywords) and discovery terms | [src/sprout/candidates.py](src/sprout/candidates.py) |
| SQLModel schema `company`, `fact`, `news_item`, `run_log` + write helpers | [src/sprout/db.py](src/sprout/db.py) |
| One-command ingestion | [src/sprout/ingest.py](src/sprout/ingest.py) (`uv run python -m sprout.ingest`) |
| Config: `SPROUT_DB_PATH` (default `data/sprout.db`), `SPROUT_CACHE_DIR` (default `.cache/http`) | [src/sprout/config.py](src/sprout/config.py), [.env.example](.env.example) |
| Tests (all network mocked with `httpx.MockTransport`) | `tests/test_http.py`, `test_companies_house.py`, `test_rss.py`, `test_web.py`, `test_candidates.py`, `test_db.py`, `test_ingest.py`, shared `tests/conftest.py` |

### Acceptance criteria
- [x] `python -m sprout.ingest` populates ≥200 candidate companies and ≥100 news items. Live run on a clean DB: **876 candidate companies** (1,377 search results read, 477 rejected by the filter, 19 HTTP requests) and **115 news items** (6 requests).
- [x] Re-running creates no duplicates and makes no repeated HTTP calls for cached URLs. Verified live: second run made 0 HTTP requests (19 + 6 cache hits), added 0 rows (876 companies / 115 news items unchanged) Also covered in tests, including that fact rows are not duplicated.
- [x] Network code tested against mocked responses (93 tests pass, `ruff check` clean).
- [x] Web fetcher also checked live once (a real page fetched, second fetch from cache, `127.0.0.1`/`localhost` blocked).

### Open items (need you)
1. **Companies House key (resolved).** The first key returned `401` because it belonged to a *test* application: test keys only work on `https://api-sandbox.company-information.service.gov.uk` (which holds generated test data only and returns no real companies). Sprout needs a key from a **live** application, which is what `.env` now has. Keep the two environments apart if you create more keys.
2. **Candidate quality needs a look before Phase 2.** The filter is name-based, so the 876 candidates include many micro-companies, consultancies and shells (e.g. "Ai Personal Trainer Ltd", "Hamilton Robotics Limited") and will miss AI startups with non-descriptive names. Matched keywords: robotics 190+, artificial intelligence 150, ai 112, analytics 98, neural 81. Treat as a candidate pool; Phase 2 enrichment and Phase 5 classification decide what is a real startup. Options if the pool is too noisy: require a tech SIC code for all keywords, raise the score threshold, or seed from investor portfolio/startup lists (the plan's other discovery sources).
3. Phase 0's Anthropic-key items are unchanged by this phase.

### Decisions locked in
- **One HTTP entry point.** All network code goes through `HttpClient.get`. 2xx and 404 responses are cached (404 so a missing `robots.txt` or company is not re-requested); 3xx are returned uncached; other 4xx raise `FetchError` without retrying; 429/5xx/transport errors retry (4 retries, 1s·2ⁿ, raised to `Retry-After`, capped at 60s). `max_age` is per call: Companies House 7 days, feeds 6 hours, pages 7 days, `robots.txt` 1 day. Cache files are JSON under `.cache/http/` keyed by a hash of the URL + sorted params; **the API key lives only in the `httpx` client's auth, never in a key or file.** There is no `--refresh` flag; delete `.cache/http/` to force refetching.
- **Companies House rate limit** is 550 calls / 300 s (the API allows ~600). Web pages and feeds are limited to 1 request per host per 2 s / 1 s.
- **Discovery uses `/advanced-search/companies`** (`company_name_includes`, `company_status=active`, `incorporated_from=2012-01-01`), because its results carry SIC codes and the plain `/search/companies` results do not. A 404 from either is treated as "no results". Verified live: the parameters work (the response also carries `hits` and a `top_hit`; `top_hit` is repeated in `items`, so we ignore it).
- **Candidate filter** (`assess`): requires active status and incorporation ≥ 2012-01-01. A *strong* name keyword (artificial intelligence, machine learning, deep learning, neural, generative, computer vision, llm, robot*) is enough on its own; a *weak* one (`ai`, intelligen*, cognitive, autonomous, algorithm*, analytics) counts only together with a tech SIC code (62011/62012/62020/62090/63110/63120/63990/72190), so "Ai Restaurant Ltd" is rejected. Whole-word matching, so "Airways" ≠ "ai". Score = 2/strong + 1/weak + 1 for a tech SIC. Only the name and SIC are used; the search API has no description. `Company.candidate_score` and `matched_keywords` are stored so Phase 5 can rank or re-filter without re-querying.
- **Company identity = Companies House number** (unique index); the upsert refreshes name/status/SIC/locality. Companies found later by other means have `company_number = NULL`.
- **`fact` table** = one row per (company, field, source_url): `value_json` (via `pydantic_core.to_json`, so models like `FundingRound` serialise), `source_url`, `retrieved_at`, `confidence`, `note`. `save_fact` upserts on that key; `value_json IS NULL` represents "not disclosed". Ingestion writes two registry-sourced facts per company, both `confidence=medium` and sourced to the public Companies House page: `hq` (registered-office locality) and `founded` (incorporation year; the note says it can differ from the real founding year). `retrieved_at` is the ingest time, and a cached response can be up to 7 days old.
- **Timestamps are timezone-aware UTC** everywhere: this SQLModel version rejects naive datetimes.
- **Table class names** are `Company`, `Fact`, `NewsItemRow` (avoids clashing with the Pydantic `NewsItem`) and `RunLog`; table names match the plan. `run_log` stores `kind`, status (`running|ok|error`), start/finish, `stats` JSON (including `http_requests`/`cache_hits`) and the error. Cost logging is still Phase 2 (`costs.py`).
- **News feeds (6):** Sifted, UKTN, Tech.eu, The Guardian (AI), TechCrunch (Startups), BBC (Technology). One more than the plan's 3–5, to clear 100 items from a single fetch. They are not filtered for UK relevance (Tech.eu/TechCrunch are broader); Phase 5's news agent should match items to companies. A broken feed is reported in `failed_feeds` and exit code 1, but does not stop the others. Only title, link, a ≤500-char plain-text summary and date are stored; URLs are deduped after stripping `utm_*`/`fbclid`/fragments.
- **Web fetcher safety** (URLs will come from untrusted scraped content): only `http(s)`; every host (and every redirect hop, which is followed manually) must resolve to a public IP, so loopback/private/link-local/metadata addresses are refused; `robots.txt` is honoured (4xx other than 429 → allow all, unreachable/5xx → refuse); only `text/html`, `application/xhtml+xml`, `text/plain` are accepted; max 5 redirects; 5 MB download cap; text truncated to 20k chars. Residual risk: DNS can change between our check and the connection (rebinding); acceptable for this project.
- **HTML→text** uses the standard library (`html.parser`), so no new dependency. It drops script/style/noscript/head and keeps block structure as lines. It does not extract "main content", so nav/footer text is included; JavaScript-rendered pages yield little text.
- **No new dependencies.** `pyproject.toml` and `uv.lock` are unchanged.

### Things later phases must know
- **Phase 2 `fetch_page` tool** should call `WebFetcher.fetch` (build the client with `web.make_http_client(settings.cache_dir)`); it raises `FetchBlockedError` / `FetchError`, which the tool should turn into a `tool_result` error string for the model rather than crash the loop. Page text is untrusted: wrap it as data in the prompt.
- **`lookup_companies_house`** can use `CompaniesHouseClient.get_company` / `filing_history` (both return `None` on 404 and validate the company number before building the URL). Build the HTTP client with `companies_house.make_http_client(key, settings.cache_dir)`.
- **Persisting profiles:** flatten each `Sourced` field of `CompanyProfile` with `save_fact(session, company_id, field, sourced)`; `category` is not `Sourced`, so it needs its own column on `company` (not added yet).
- **In-place JSON mutation isn't tracked by SQLAlchemy.** Assign a whole new dict/list to `Company.sic_codes`, `matched_keywords` or `RunLog.stats` rather than mutating it.
- **The DB has no migrations.** `init_db` only creates missing tables; after changing a model, delete `data/sprout.db` (or add Alembic if the data becomes precious).
- **`HttpClient` is synchronous and not thread-safe** (the rate limiter and counters are plain objects). If Phase 5 parallelises, use one client per worker or add locking.

## Streamlit progress & test console (ahead of Phase 6)

**Status: done.** A working UI to check progress and exercise ingestion; it is not the Phase 6 dashboard (no funding/stage views, because no enrichment data exists yet).

### What was built
| Item | Where |
|---|---|
| Streamlit entry point (adds `src/` to `sys.path`; this is the file to deploy) | [streamlit_app.py](streamlit_app.py) |
| App: Progress, Companies, News, Run & test tabs | [src/sprout/app/dashboard.py](src/sprout/app/dashboard.py) |
| Streamlit-free queries and docs parsing | [src/sprout/app/queries.py](src/sprout/app/queries.py) |
| Tests (queries on a temp DB; `AppTest` smoke tests of the whole app) | [tests/test_app_queries.py](tests/test_app_queries.py), [tests/test_dashboard.py](tests/test_dashboard.py) |
| New direct dependency `pandas` (already pulled in by Streamlit) | [pyproject.toml](pyproject.toml) |

### Acceptance (no plan criteria; own checks)
- [x] `uv run pytest` 110 passed, `uv run ruff check` clean.
- [x] App renders with an empty database and with seeded data (`AppTest`), and `streamlit run` serves HTTP 200 / health `ok` headless.
- [ ] **Not verified:** clicking the ingestion/hello buttons against the live APIs, and a Streamlit Community Cloud deployment. Neither was run here.

### Decisions locked in
- **Progress tab is driven by these docs**: phases come from the plan's timeline table; state from each `## Phase N - …` section of this file (`**Status: done…**` and no unticked `- [ ]` → Done, otherwise In progress, no section → Not started). Keep that heading/status/checkbox format or the tab misreports.
- **Run & test is opt-in**: it writes the database and spends API quota, so it only appears when `SPROUT_ENABLE_ACTIONS=1`. A public deployment is read-only by default.
- Ingestion is run by calling `sprout.ingest.main` in-process and showing its output.

### Things later phases must know
- **Streamlit Community Cloud has no persistent disk and `*.db` is gitignored**, so a deployed copy starts empty. Phase 6/8's bundled snapshot DB is still needed for a populated public app. Secrets go in the app's Secrets settings (exposed as environment variables), never in the repo.
- Phase 6 should extend `queries.py` (shared service layer) rather than add SQL in the page code.


## Phase 2 - First agent: single-company enrichment

**Status: done, with caveats.** The agent was built, covered by mocked tests (169 tests pass in total, `ruff check` clean) and run live on 10 companies. The tiny-cap criterion is verified with mocks only, not live. The pilot is a manual review by the implementing agent; the user's own review of the profiles is still open (see "Open items").

### What was built
| Item | Where |
|---|---|
| Price table, `Usage`, `cost_usd`, `UnknownModelError` (pure, no I/O) | [src/sprout/costs.py](src/sprout/costs.py) |
| Hand-written tool loop: `run_agent`, `Budget`, `Tool`, `ToolOutcome`, `LoopResult`, `make_client`. The only pipeline module importing `anthropic` | [src/sprout/agents/loop.py](src/sprout/agents/loop.py) |
| Tools: `web_search` (server-side), `fetch_page` (wraps `WebFetcher`), `lookup_companies_house`; `SourceLedger`, `wrap_untrusted` | [src/sprout/agents/tools.py](src/sprout/agents/tools.py) |
| System prompt, `submit_profile` schema derived from `CompanyProfile`, submission validation, `enrich_company` / `enrich_with_result`, `resolve_company`, CLI | [src/sprout/agents/enrich.py](src/sprout/agents/enrich.py) |
| `python -m sprout.enrich` shim | [src/sprout/enrich.py](src/sprout/enrich.py) |
| `Company.category` column, additive `ALTER TABLE` migration in `init_db`, `save_profile`, `add_company_by_number` | [src/sprout/db.py](src/sprout/db.py) |
| Scripted fake Anthropic client | [tests/fakes.py](tests/fakes.py) |
| Tests | `tests/test_costs.py`, `test_agent_loop.py`, `test_agent_tools.py`, `test_enrich.py`, additions to `test_db.py` |

CLI: `uv run python -m sprout.enrich NAME_OR_NUMBER ... [--strong] [--max-usd] [--max-iterations] [--effort] [--show] [--reset]`. A company number (8 digits, or 2 letters plus 6 digits) is told apart from a name; a number not yet in the DB is fetched from Companies House and added. `--reset` deletes a company's existing facts and category (`db.delete_profile`) before re-enriching, for picking up a changed schema instead of accumulating facts under both old and new field names.

### Acceptance criteria
- [x] **For 10 known companies, >=8 produce a schema-valid profile with a category and at least one sourced fact; no fabricated funding when the source is silent.** The final live run of 10 companies (Wayve, PolyAI, Synthesia, Graphcore, Cleo AI, Faculty Science, Tractable, Peak AI, Quantexa, Airborne Robotics): 10/10 `submitted`, each with a category and 4 to 9 sourced facts. The implementing agent reviewed the output: no invented funding where sources were silent (Graphcore, Peak AI and Airborne Robotics have all funding null), no surviving wrong-company confusion, original currency and rate in the `note` for every non-USD round. Spot-checks against its own knowledge matched (Faculty US$39.5m, Tractable US$65m Series E, Quantexa EUR 163m, Cleo US$80m Series C). **Caveats:** this is the 10/10 of the *final* pass; earlier passes were worse (first pass 6/10, see "Live testing history"). The review is the agent's, not yours. Aggregator totals for Wayve (US$2.5bn) and Tractable (US$182.5m) could not be independently checked (search results arrive encrypted); both are flagged in the note, Tractable's at medium confidence.
- [x] **The loop never exceeds its iteration or USD cap (tested with a deliberately tiny cap).** Verified by mocked tests: a tiny USD cap gives exactly 1 call and an exact iteration cap gives exactly that many calls, both with `run_log` stats still recorded and nothing written to `fact`. **Not stress-tested live.** The USD cap is soft by at most one response (cost is known only after a call).
- [x] **Cost per company is logged and averages below ~USD 0.07.** Logged in `run_log.stats.cost_usd` (kind `enrich`). Final 10-company run: total USD 0.1387, **average USD 0.0138**. All 30 `enrich` runs made while iterating: USD 0.4205 total, average over the 22 successful ones USD 0.0134. About 70 percent of each run is the single web search (US$0.01). Total live spend about USD 0.42 against a roughly USD 4 ceiling.

Verification run by the orchestrator after the implementer finished: `uv run pytest` 169 passed, `uv run ruff check` all checks passed.

### Live testing history
1. First call: 400 on `strict: true` (see decisions), cost USD 0.
2. First pass over the 10: 6 succeeded. Cleo, Tractable and Quantexa ended `invalid`: the model cited homepages no tool had returned, and repeated them after the repair message.
3. Fixes: the repair message now lists the citable ledger URLs, and the prompt says to fetch a page before citing it.
4. Second pass: Cleo and Quantexa still `invalid`. Cause was a real ledger bug (`https://x.com` vs `https://x.com/`); fixed by ignoring trailing slash and host case.
5. During re-runs: one Tractable run had web search unavailable and handled it honestly with nulls; one Cleo run ended `refused` (transient), then succeeded on retry.
6. The model left funding amounts null for non-USD rounds, so approximate conversion rates into USD were added to the prompt.
7. Final pass on unchanged code: 10/10. The `run_log` still holds 8 `error` rows (invalid/refused/error) from the iteration phase.

### Open items (need you)
1. **Review the 10 profiles yourself** (`uv run python -m sprout.enrich <number> --show`, or query the `fact` table). The pilot companies are not in the name-matched candidate pool; their numbers: Wayve 10924127, PolyAI 11048129, Synthesia 10933652, Graphcore 10185006, Cleo AI 09864205, Faculty Science 08873131, Tractable 09315523, Peak AI 09307701, Quantexa 10045407, Airborne Robotics 10889295.
2. **Check the first console invoice** against the assumption of US$10 per 1,000 web searches (taken from the `claude-api` skill, where it appears only in a Managed Agents note). Cost per company is dominated by this number.
3. ~~`data/sprout.db` is tracked by git~~ **Resolved (2026-10-25):** untracked `sprout.db`; committed `data/sprout_snapshot.db` instead (refresh by hand: `cp data/sprout.db data/sprout_snapshot.db`). `config.get_settings()` falls back to the snapshot when the live db is missing (Streamlit Cloud has no persistent disk). The DB the agent found held 305 companies, not the 876 recorded in the Phase 1 notes (that was probably a different local DB); it now holds 314.
4. Some already-enriched companies have facts stored under stage labels or field names that predate the current schema; re-run `sprout.enrich` on them to refresh.
5. Not tested live: `--strong` (Sonnet 5.5), forced `tool_choice` on Haiku 5.5, the over-100K-token price tier, and the 512-token minimum cacheable prefix.

### Decisions locked in
- **Models and call shape (verified live on Haiku 5.5):** `tool_choice: {"type": "auto"}`, `output_config={"effort": "low"}` (default effort `low`), top-level `cache_control={"type": "ephemeral"}` on `messages.create` plus a marker on the system block. Caching works: `cache_creation_input_tokens > 0`; across all runs about 455k tokens were written and 1.50M read. Haiku 5.5 is the default; `--strong` selects Sonnet 5.5.
- **`submit_profile` is not `strict`.** The API rejected the derived schema ("29 parameters with type arrays or anyOf, limit 16"). Pydantic validation plus one repair attempt is the enforcement, as the architecture's fallback allowed. The schema is derived from `CompanyProfile` with `$ref`s inlined, `retrieved_at`, `news` and `name` removed and all fields required.
- **Search is server-side `web_search_20260209`** with `max_uses` (start 3); `fetch_page` and `lookup_companies_house` are custom, so robots, SSRF, size and cache protections apply to every page read. Server-side `web_fetch` is not used. The SDK also ships `web_search_20260318`, not documented in the skill; not adopted. Because average cost is far below target, there is no reason to fall back to a custom search backend (D13 holds).
- **Provenance is enforced in code:** a non-null value needs `source_url` (existing `Sourced` validator); `source_url` must be a URL a tool actually returned (the seen-URL ledger; matching ignores trailing slash and host case); `retrieved_at` is stamped by code; funding fields need a `note` with the original figure and currency; a source seen only in a search snippet is capped at `medium` confidence. Rejections go back to the model as a compact error (which does not echo input) that lists up to 25 citable URLs, then one repair attempt, then `invalid`.
- **Nothing is persisted unless a validated `submit_profile` arrived.** On any other stop (`invalid`, `max_iter`, `budget`, `no_submit`, `truncated`, `refused`, `error`) no facts or category are written, but the `enrich` `run_log` row keeps the stats and cost. `enrich_company` assigns stats and commits before raising, because `logged_run` rolls back on exceptions.
- **`run_log` kind `enrich`, one row per attempt;** stats: company_id, company_number, model, effort, stop, iterations, repairs (0 or 1 allowed, not failures), token counts (thinking is inside `output_tokens`), web_searches, tool_calls, cost_usd, budget, error.
- **Defaults:** 12 iterations and USD 0.13 per company. The wrap-up notice ("call submit_profile now") appears on the last iteration or at 80 percent of the USD cap. An unpriced model fails before any call.
- **`company.category`** is a nullable plain string: NULL means "not yet classified", distinct from `Other`.
- **Additive migration in `init_db`:** missing nullable columns are added with `ALTER TABLE`; anything else raises an error telling you to delete the DB. Existing data is preserved.
- **Prompt content:** fetched text is data not instructions, fetch before citing, disambiguate same-name companies by Companies House number, approximate FX rates for converting, "unknown is acceptable".
- **No `-m live` enrichment test** was added, so a plain `pytest` with a key never spends money.
- **Deviations from the architecture doc:** `build_tools(fetcher, ch, ledger)` instead of `build_tools(deps, ledger)` (to avoid `tools.py` importing `Deps`); `Deps` gained `model` and `effort`; `enrich_with_result` added; extra CLI flags and a Companies House name-search for suggestions. No new dependencies or env vars.

### Things later phases must know
- **Model output is non-deterministic.** Even with the ledger, an occasional run ends `invalid` (invented homepage URL, correctly rejected) or `refused` (transient). Phase 5 needs retries and `enrichment_state`.
- **The model sometimes leaves funding amounts null for non-USD figures** despite the prompt. Null is the safe failure, but it understates funding coverage.
- **Search results arrive encrypted**, so snippet-only claims cannot be audited from our side. The code caps them at `medium` and the model records the source in the note. A verifier (Phase 5) is the real check.
- **A claimed website can be wrong** (Airborne Robotics' site returned another company's content); it is stored at medium confidence with a caveat in the note.
- **Cost is dominated by the web search** (about 70 percent); lowering `max_uses` or caching search results is the lever, not the model.
- Use `enrich_with_result` when the caller needs the `LoopResult`; `enrich_company` returns only the profile.
- `LoopResult.stop` values: `submitted`, `invalid`, `max_iter`, `budget`, `no_submit`, `truncated`, `refused`, `error`.
- `Company.category` exists now, so Phase 5's classifier can write it; `save_profile` writes the enrichment agent's category.
- **Found by the end-of-phase architecture review (not fixed):**
  - There is no per-run fetch cap (fetches are bounded only by the iteration cap) and no batch-level USD cap: the CLI applies `--max-usd` per company, so a long target list can spend `n * 0.13`. The Phase 5 orchestrator needs a batch cap.
  - A capped or failed run persists no partial facts (the older architecture text promised "partial"; it was corrected). Its `run_log.status` is `error` with the reason in `stats.stop`.
  - Re-running a company can leave both a valued fact and a NULL-value fact for the same field (valued in run 1, null in run 2, same source or different). The winning-value rule must exist before any front end reads facts.
  - Latent nit in `_simplify` ([src/sprout/agents/enrich.py](src/sprout/agents/enrich.py)): it drops any dict key named `title`, `default`, `minimum`, `format` etc., including a `CompanyProfile` field with such a name inside `properties`. No current field collides.
  - `agents/enrich.py` is about 460 lines (agent, schema, CLI, `resolve_company`); split it when Phase 5 adds an orchestrator.
  - `pause_turn` has only mock evidence; `--strong` and forced `tool_choice` were never run live.

---

## Phase 3 - Observability

**Status: done, with one live check not achieved.** Steps and failure categories are recorded for every enrichment run, queryable through five new functions and shown in a new Observability tab. `uv run pytest -q` passes (229 tests) and `uv run ruff check` is clean. The live check wrote steps and categories end to end against the real API, but the planned deterministic `iteration_cap` run could not be produced because `--max-iterations 2` hits an API 400 (see "Open items"). The step 3c findings batch (20 live runs) is recorded under "Step 3c findings" at the end of this section.

### What was built
| Item | Where |
|---|---|
| `FailureCategory` (8 values) and `failure_category_for(stop)`, pure | [src/sprout/failures.py](src/sprout/failures.py) |
| `RunLog.failure_category` (nullable), `RunLogStep` table, idempotent backfill in `init_db`, `logged_run` fallback to `internal_error` | [src/sprout/db.py](src/sprout/db.py) |
| `StepRecord`, `LOGGED_TEXT_MAX_CHARS` (1,000), `LoopResult.steps` and `LoopResult.error_type`; one record per model response, no new parameter, loop still DB-free | [src/sprout/agents/loop.py](src/sprout/agents/loop.py) |
| `_stats` gains `error_type`; `failure_category` set and `RunLogStep` rows added before the existing pre-raise commit (`_step_row`) | [src/sprout/agents/enrich.py](src/sprout/agents/enrich.py) |
| `EnrichRun`, `TrendPoint`, `DayOutcome`; `enrich_runs`, `failure_counts`, `cost_trend`, `daily_outcomes`, `run_steps` | [src/sprout/app/queries.py](src/sprout/app/queries.py) |
| `render_observability` and the Observability tab (after News, before gated Run & test); tabs now built from `(name, renderer)` pairs | [src/sprout/app/dashboard.py](src/sprout/app/dashboard.py) |
| Tests | `tests/test_failures.py` (new), additions to `test_agent_loop.py`, `test_db.py`, `test_enrich.py`, `test_app_queries.py`, `test_dashboard.py` |

Commits: step 1 `3ba922b` (taxonomy and schema), step 2 `b93c0bb` (loop step records), step 3 `1082259` (enrich wiring), step 4 `8738494` (queries), step 5 `e500f56` (dashboard), fix `6ac974c` (see below).

Found while analysing the findings batch: the 40 oldest `enrich` rows (ids 3 to 42) store `cost_gbp` and `usd_to_gbp` and no `cost_usd`, so the first version of the queries showed their cost as 0. `queries.py` now converts `cost_gbp / usd_to_gbp` when `cost_usd` is absent (commit `6ac974c`, one new test). Across all 64 enrich rows the tab now reports about US$0.91 in total.

### Acceptance criteria
- [x] **Every enrichment run's steps are inspectable after the fact without rerunning it.** Verified live on runs 43, 44, 45 and 46 (`SELECT ... FROM run_log_step WHERE run_log_id = ?`), including the multi-tool pattern (`lookup_companies_house,web_search`), `tool_errors` (run 44 step 3, a rejected submission) and a failed run (run 45 and 46, `api_error`, one step each). Step costs sum exactly to `stats.cost_usd` for runs 43 (0.024016) and 44 (0.013197). Scripted tests cover `len(steps) == iterations`, summed step usage equal to `result.usage`, truncation at the cap, and steps plus category committed for every non-success stop (invalid, max_iter, budget, no_submit, truncated, refused, api error). **Caveat:** runs made before this phase have no steps (by design; the tab says so).
- [x] **The dashboard groups failures by category and shows cost/error trends across runs.** Verified by `streamlit.testing` smoke tests only (empty, populated, no failures, no steps with iterations > 0 and == 0, `stats == {}`, and untrusted-markdown rendering). I did not open the tab in a browser.
- [x] **Step logging does not change enrichment behaviour or materially slow down a run.** The loop never reads `steps`, makes no extra call and does no I/O; the persistence is one small INSERT batch inside a commit that already existed. All pre-existing loop and enrich tests pass unchanged. No timing was measured; "materially slower" is argued from the design, not benchmarked.
- [ ] **Step 3a live check: `--max-iterations 2` gives a deterministic `iteration_cap` row with its two steps.** Not achieved. The `iteration_cap` path with its two committed steps is covered by a scripted test only. Live, the run ended `error` (see Open items). A live failed run (`api_error` with `error_type`, one step) was confirmed instead.

Live spend for the check: four calls, about US$0.0376 (runs 43 to 46: 0.0240, 0.0132, 0.0002, 0.0002).

### Open items (need you)
1. **Pre-existing loop bug, reproducible with `--max-iterations 2`.** Both attempts (run 45, DYAD ARTIFICIAL INTELLIGENCE LIMITED 12626064, and run 46, ARTIFICIAL INTELLIGENCE CONSULTING LTD 14925003) ended `stop=error`, `BadRequestError` 400 on the second request: "`web_search` tool use ... was found without a corresponding `web_search_tool_result` block" (`messages.1`). Iteration 1 of every run emitted a server-side `web_search` together with a custom `lookup_companies_house` tool_use (`stop_reason=tool_use`, step `web_searches=0`, no search result block in the response). Runs 43 and 44 (default 12 iterations) accepted the same pattern. The two failures both had the cap of 2, where the second request also carries the wrap-up notice, so the cap may be a trigger, but this was not isolated. This is not caused by step logging (steps are read only after the loop ends), but it may affect real runs intermittently, and it blocks the planned live `iteration_cap` check. Needs a decision: fix in the loop (for example handle a response whose `server_tool_use` has no result, or avoid mixing server and custom tool calls) in a later phase or a fix-up. Not changed here (out of scope).
2. `docs/architecture.md` shows as modified in the working tree (it reflows the `StepRecord` code block in 3.6; the cause, probably a formatter or hook, was not identified). It was left uncommitted and untouched by the implementers; a `git checkout` to discard it was blocked by the permission system, so decide whether to commit or discard it.
3. The Phase 4 proposals under "Step 3c findings" need your decision. No Phase 4 number has been changed.

### Decisions locked in
- **A27/A28/A29 kept as designed:** no tool-input or URL logging; `invalid_output` is not split into sub-reasons; one step row per model response, not per tool call (tool names comma-joined in `tool_name`).
- Failure category is a real nullable column; NULL means success. Eight values; `internal_error` is set by `logged_run` when an exception escapes after the category is still NULL.
- Steps and category are committed in the commit that already precedes `EnrichmentFailed`, so failed runs keep them.
- `response_text` is capped at 1,000 characters plus a ` [truncated]` marker, rendered only via `st.code`/dataframes, never markdown. Run detail labels never include logged text.
- Tabs are now a list of `(name, renderer)` pairs, zipped with `strict=True`.
- `enrich_runs` with both a category and `failures_only=True` applies the category filter only.

### Things later phases must know
- `RunLogStep.created_at` must be timezone-aware on insert (a naive value raises in SQLModel); `started_at` reads back naive UTC.
- `init_db` backfills `failure_category` from `stats.stop` for historic enrich rows, so a hand-seeded success row needs `stop: "submitted"`.
- The Observability metrics and charts use the newest 200 runs (`enrich_runs` default limit); the failure-category chart covers all runs. Irrelevant at about 45 runs, relevant in Phase 5.
- Steps are persisted at the end of a run, so a hard process kill loses that run's steps (as it already loses its stats). Live per-worker progress in Phase 5 needs the optional `on_step` callback described in the architecture (A29 notes).
- Queries live in `app/queries.py`, Streamlit-free, reusable by the Phase 6 service layer; the file moves to `sprout/queries.py` before Phase 7.

### Step 3c findings (live batch, 2026-10-28)
Batch: the 20 highest-`candidate_score` companies that were not pilots and not yet enriched (run ids 47 to 66, Haiku 5.5, defaults, `uv run python -m sprout.enrich <20 numbers>`). Total cost **US$0.3049**. All figures come from `run_log`, `run_log_step` and the new query functions; the 20 runs are all name-matched "artificial intelligence" shell-style companies, not the pilot set, so they are not representative of real startups.

| Finding | Measured |
|---|---|
| Outcome of the batch | 20/20 `submitted`, 0 failures, 0 `failure_category` values |
| Cost per run | min 0.0117, mean 0.0152, max 0.0242 USD; steps sum exactly to the run cost on all 20 |
| Cost driver | 1 web search: mean about 0.0126 (15 runs); 2 searches: about 0.0232 (5 runs). The search is the cost, as in Phase 2 |
| Iterations | 2 (6 runs), 3 (13 runs), 5 (1 run); mean 2.8 |
| Repairs | 7 of 20 runs needed one repair (35%); all 7 succeeded, so they are invisible in `failure_category` |
| Tool errors (56 steps) | 11: 7 in steps containing `submit_profile` (5 of 22 submit-only steps, 1 of 4 search+submit, 1 of 1 double-search+submit; matches the 7 repairs) and 4 of 7 `fetch_page`-only steps (blocked pages). No errors in the Companies House lookup steps |
| Categories assigned | `Other` 12, AI models and applications 6, Enterprise software and cloud 2; the `Other` runs had only 2 to 3 sourced facts |
| `response_text` | Empty on all 64 logged steps (the model returns no text blocks at `low` effort and with tool calls); the truncation path was never hit live |
| Historical failure mix (all 64 enrich rows) | 53 success, 7 `invalid_output`, 3 `api_error`, 1 `refusal`. All 7 `invalid_output` and the refusal are from the 2026-10-19 prompt iteration (before the ledger fixes). Since then the only failures are the two `--max-iterations 2` 400s |
| Cost by day (queries) | 10-19: 30 runs, 8 failures, US$0.42; 10-25: 10 runs, 1 failure, US$0.15; 10-28: 24 runs, 2 failures, US$0.34 |

What the observability data says about failure causes: under current code the dominant risk is not the model but the loop's handling of mixed server and custom tool calls (the 400 in Open items 1). Everything else is a rare, transient event (one refusal in 64).

**Proposed Phase 4 adjustments (for you to approve, none applied):**
1. **Golden-set stratification.** The candidate pool's top scores are mostly shell-style names whose real category is `Other`. A golden set drawn only from famous startups would flatter category accuracy; include a deliberate share of `Other`/non-startup names (for example 6 to 8 of 30) and label them honestly, so the metric tests the "unknown is acceptable" behaviour.
2. **Profiles-submitted floor.** Current code submitted 20/20 on the batch and 10/10 on the pilot. A floor set near the old 85% target would not catch a regression such as 2 failures in 24; consider raising it (for example to 95%) after Phase 4's first baseline run, and count `api_error` separately from model failures so an infrastructure 400 does not look like a quality regression.
3. **Add a repair-rate metric** (runs with `stats.repairs > 0` over runs). 35% of runs needed a repair yet all count as success; a prompt change that doubles the rate would be invisible to the current gates.
4. **Cost gate.** Observed max per company is US$0.0265 against the US$0.13 cap and the mean is US$0.0146. A cost-per-company gate in the region of US$0.03 to 0.04 would catch a doubling without false alarms; set it from the baseline run, not from this batch alone.
5. **Judge input.** `response_text` is empty, so the Phase 4 judge and failure diagnosis cannot rely on it. Diagnosis depends on `tool_name` sequences, `tool_errors` and `stop_reason`; if text is wanted, the tool error message (not logged by decision A27) would be the thing to reconsider.
6. **Fix or avoid the 400** (Open items 1) before the eval run, or the baseline will contain `api_error` noise whenever a low iteration cap is used (for example in the tiny-cap guardrail tests).
