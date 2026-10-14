# Sprout — Implementation Notes

Running log of what was built, why, and what later phases need to know. Newest phase last.

## Phase 0 — Setup & definitions

**Status: done except two items that need your credentials** (see "Open items").

### What was built
| Item | Where |
|---|---|
| uv project, Python 3.12 (`requires-python >=3.12`, `.python-version` = 3.12) | [pyproject.toml](pyproject.toml) |
| Runtime deps: `anthropic`, `pydantic`, `sqlmodel`, `httpx`, `feedparser`, `mcp`, `streamlit`, `python-dotenv` · dev: `pytest`, `ruff` | [pyproject.toml](pyproject.toml), `uv.lock` |
| Settings loader (keys, model IDs) | [src/sprout/config.py](src/sprout/config.py) |
| Taxonomy, stage enum, `Sourced[T]`, `CompanyProfile` | [src/sprout/models.py](src/sprout/models.py) |
| "hello" smoke call to Claude | [src/sprout/hello.py](src/sprout/hello.py) — `uv run python -m sprout.hello` |
| Tests (valid + invalid examples) | [tests/](tests/) |
| Lint/test config, CI | `[tool.ruff]`/`[tool.pytest]` in pyproject, [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Secrets hygiene | `.env` and `*.db` in [.gitignore](.gitignore); template in [.env.example](.env.example) |

### Acceptance criteria
- [x] `uv run pytest` and `uv run ruff check` pass — 24 passed, 1 skipped. Also verified on a copy of the project without `.venv`/`.git`/`.env` (`uv sync --locked` → ruff → pytest). A true `git clone` check will happen once CI runs.
- [ ] Claude "hello" call — **not verified**: no `ANTHROPIC_API_KEY` in this environment, and the `ant` CLI isn't installed. `python -m sprout.hello` fails with a clear `MissingConfigError` until `.env` exists. The live test in `tests/test_live_api.py` is skipped without a key and will run automatically once there is one.
- [x] Taxonomy and schemas in code with unit tests ([tests/test_models.py](tests/test_models.py)).

### Open items (need you)
1. **Anthropic API key**: create one, set a monthly spend limit in the console *before* the first paid call, `cp .env.example .env`, fill in `ANTHROPIC_API_KEY`, then run `uv run python -m sprout.hello` and `uv run pytest -m live`.
2. **Companies House API key**: register at developer.company-information.service.gov.uk and put it in `COMPANIES_HOUSE_API_KEY` (needed from Phase 1).
3. **Git**: `uv init` ran `git init` on branch `main`; there are no commits yet (nothing was committed). `.gitignore` is in place, so `git add .` will not pick up `.env`.

### Decisions locked in
- **Taxonomy (16, frozen):** AI models and applications, Fintech and payments, Enterprise software and cloud, Cybersecurity, Biotech and pharma, Healthtech and digital health, Electric vehicles and batteries, Climate, energy and cleantech, Semiconductors and AI hardware, Robotics and automation, Defense, aerospace and space, E-commerce and retail tech, Consumer social, media and gaming, Logistics and supply chain, Deep tech and quantum, Other. Changing it later invalidates classifications and the golden set. Tests assert the exact 16 labels and contain `Other`.
- **Stage enum:** Pre-seed, Seed, Series A, Series B, Series C+, Growth, Unknown.
- **`Sourced[T]`** = `value`, `source_url`, `retrieved_at`, `confidence` (low/medium/high), plus an optional `note` (added for Phase 2's "funding in GBP with original currency noted", e.g. `"US$12m converted"`).
  - `value=None` means "not disclosed" and is always valid.
  - **A non-null value without `source_url` fails validation.** This enforces "provenance over completeness" in code, not only in the prompt.
  - `source_url` must be `http(s)`; `retrieved_at` must be timezone-aware; unknown fields are rejected (`extra="forbid"` on every model).
- **Every `Sourced` field defaults to "not disclosed"** (via `default_factory`, so profiles never share an instance). An empty `CompanyProfile(name=...)` is valid and states nothing it cannot back up.
- **`category` is a plain `Category`, not `Sourced`.** It is a classification against our own taxonomy, not a fact found at a URL. Defaults to `Other`. If you'd rather have a per-profile rationale/confidence, add it in Phase 3 with the classifier.
- **Money is whole GBP as `int` (`>= 0`)**, not float, to avoid rounding artefacts; original-currency detail goes in `note`.
- **`last_round`** is `Sourced[FundingRound]` (stage, amount_gbp, announced date, investors): one source covers the whole round. `founded` is bounded to 1800–2100.
- **Models:** `claude-haiku-5-5` (bulk) and `claude-sonnet-5-5` (strong), overridable via `SPROUT_BULK_MODEL` / `SPROUT_STRONG_MODEL`. IDs are taken from the `claude-api` skill's model table (dated 2026-10-06); re-check before Phase 2.
- **Deviation from the plan's layout:** `costs.py` and `db.py` are not created yet (they belong to Phases 1–2). No console-script entry point (`uv init` adds one; removed as unused).

### Things later phases must know
- **`anthropic` is now 1.x, built on `httpx2`, not `httpx`.** Don't pass a plain `httpx.Client` to the SDK; use `DefaultHttpxClient`. Our own code (Companies House, fetcher) can keep using `httpx` — they are separate libraries.
- **Phase 2 (`submit_profile`):** the plan's "final tool whose input schema is the Pydantic model" still works, but on Claude Sonnet 5.5 / Opus 5.5 / Fable 5.1 *forced* `tool_choice` (`any`/`tool`) returns a 400. Use `tool_choice: auto` + a prompt instruction naming the tool, with `strict: true` on the tool, or `output_config.format` structured outputs. Haiku 5.5 should be checked against `shared/model-migration.md` in the skill.
- **Thinking tokens count against `max_tokens`** on the 5.x models (adaptive thinking is on by default; effort defaults to `medium` on Haiku 5.5). `hello.py` uses `effort: low` and `max_tokens=1024` for that reason. Set effort explicitly per agent; budget caps in Phase 2 must count thinking tokens.
- **Server-side web tools** are now `web_search_20260209` / `web_fetch_20260209` on Sonnet 5.5; older type strings in the plan's era are stale. Don't declare `code_execution` alongside them.
- **Pydantic generics:** `Sourced[T]` uses PEP 695 syntax; instantiate as `Sourced[int](...)`. Bare `Sourced(...)` has an unparameterised `T` and won't validate the value type. Database storage (Phase 1) will need to flatten `Sourced` into `fact` rows (value serialised as JSON).
- **Ruff rules:** `E, F, I, UP, B`, line length 100. CI runs `ruff check` and `pytest` only; `ruff format` is applied locally but not enforced.
- **CI action versions** (`actions/checkout@v4`, `astral-sh/setup-uv@v6`) were written from memory and haven't been run yet; confirm the first run is green.

## Phase 1 — Data foundations

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
| One-command ingestion | [src/sprout/ingest.py](src/sprout/ingest.py) — `uv run python -m sprout.ingest` |
| Config: `SPROUT_DB_PATH` (default `data/sprout.db`), `SPROUT_CACHE_DIR` (default `.cache/http`) | [src/sprout/config.py](src/sprout/config.py), [.env.example](.env.example) |
| Tests (all network mocked with `httpx.MockTransport`) | `tests/test_http.py`, `test_companies_house.py`, `test_rss.py`, `test_web.py`, `test_candidates.py`, `test_db.py`, `test_ingest.py`, shared `tests/conftest.py` |

### Acceptance criteria
- [x] `python -m sprout.ingest` populates ≥200 candidate companies and ≥100 news items. Live run on a clean DB: **876 candidate companies** (1,377 search results read, 477 rejected by the filter, 19 HTTP requests) and **115 news items** (6 requests).
- [x] Re-running creates no duplicates and makes no repeated HTTP calls for cached URLs. Verified live: second run made 0 HTTP requests (19 + 6 cache hits), added 0 rows (876 companies / 115 news items unchanged) Also covered in tests, including that fact rows are not duplicated.
- [x] Network code tested against mocked responses (93 tests pass, `ruff check` clean).
- [x] Web fetcher also checked live once (a real page fetched, second fetch from cache, `127.0.0.1`/`localhost` blocked).

### Open items (need you)
1. **Companies House key (resolved).** The first key returned `401` because it belonged to a *test* application: test keys only work on `https://api-sandbox.company-information.service.gov.uk` (which holds generated test data only and returns no real companies). Sprout needs a key from a **live** application, which is what `.env` now has. Keep the two environments apart if you create more keys.
2. **Candidate quality needs a look before Phase 2.** The filter is name-based, so the 876 candidates include many micro-companies, consultancies and shells (e.g. "Ai Personal Trainer Ltd", "Hamilton Robotics Limited") and will miss AI startups with non-descriptive names. Matched keywords: robotics 190+, artificial intelligence 150, ai 112, analytics 98, neural 81. Treat as a candidate pool; Phase 2/3 enrichment and classification decide what is a real startup. Options if the pool is too noisy: require a tech SIC code for all keywords, raise the score threshold, or seed from investor portfolio/startup lists (the plan's other discovery sources).
3. Phase 0's Anthropic-key items are unchanged by this phase.

### Decisions locked in
- **One HTTP entry point.** All network code goes through `HttpClient.get`. 2xx and 404 responses are cached (404 so a missing `robots.txt` or company is not re-requested); 3xx are returned uncached; other 4xx raise `FetchError` without retrying; 429/5xx/transport errors retry (4 retries, 1s·2ⁿ, raised to `Retry-After`, capped at 60s). `max_age` is per call: Companies House 7 days, feeds 6 hours, pages 7 days, `robots.txt` 1 day. Cache files are JSON under `.cache/http/` keyed by a hash of the URL + sorted params; **the API key lives only in the `httpx` client's auth, never in a key or file.** There is no `--refresh` flag; delete `.cache/http/` to force refetching.
- **Companies House rate limit** is 550 calls / 300 s (the API allows ~600). Web pages and feeds are limited to 1 request per host per 2 s / 1 s.
- **Discovery uses `/advanced-search/companies`** (`company_name_includes`, `company_status=active`, `incorporated_from=2012-01-01`), because its results carry SIC codes and the plain `/search/companies` results do not. A 404 from either is treated as "no results". Verified live: the parameters work (the response also carries `hits` and a `top_hit`; `top_hit` is repeated in `items`, so we ignore it).
- **Candidate filter** (`assess`): requires active status and incorporation ≥ 2012-01-01. A *strong* name keyword (artificial intelligence, machine learning, deep learning, neural, generative, computer vision, llm, robot*) is enough on its own; a *weak* one (`ai`, intelligen*, cognitive, autonomous, algorithm*, analytics) counts only together with a tech SIC code (62011/62012/62020/62090/63110/63120/63990/72190), so "Ai Restaurant Ltd" is rejected. Whole-word matching, so "Airways" ≠ "ai". Score = 2/strong + 1/weak + 1 for a tech SIC. Only the name and SIC are used; the search API has no description. `Company.candidate_score` and `matched_keywords` are stored so Phase 3 can rank or re-filter without re-querying.
- **Company identity = Companies House number** (unique index); the upsert refreshes name/status/SIC/locality. Companies found later by other means have `company_number = NULL`.
- **`fact` table** = one row per (company, field, source_url): `value_json` (via `pydantic_core.to_json`, so models like `FundingRound` serialise), `source_url`, `retrieved_at`, `confidence`, `note`. `save_fact` upserts on that key; `value_json IS NULL` represents "not disclosed". Ingestion writes two registry-sourced facts per company, both `confidence=medium` and sourced to the public Companies House page: `hq` (registered-office locality) and `founded` (incorporation year; the note says it can differ from the real founding year). `retrieved_at` is the ingest time, and a cached response can be up to 7 days old.
- **Timestamps are timezone-aware UTC** everywhere: this SQLModel version rejects naive datetimes.
- **Table class names** are `Company`, `Fact`, `NewsItemRow` (avoids clashing with the Pydantic `NewsItem`) and `RunLog`; table names match the plan. `run_log` stores `kind`, status (`running|ok|error`), start/finish, `stats` JSON (including `http_requests`/`cache_hits`) and the error. Cost logging is still Phase 2 (`costs.py`).
- **News feeds (6):** Sifted, UKTN, Tech.eu, The Guardian (AI), TechCrunch (Startups), BBC (Technology). One more than the plan's 3–5, to clear 100 items from a single fetch. They are not filtered for UK relevance (Tech.eu/TechCrunch are broader); Phase 3's news agent should match items to companies. A broken feed is reported in `failed_feeds` and exit code 1, but does not stop the others. Only title, link, a ≤500-char plain-text summary and date are stored; URLs are deduped after stripping `utm_*`/`fbclid`/fragments.
- **Web fetcher safety** (URLs will come from untrusted scraped content): only `http(s)`; every host (and every redirect hop, which is followed manually) must resolve to a public IP, so loopback/private/link-local/metadata addresses are refused; `robots.txt` is honoured (4xx other than 429 → allow all, unreachable/5xx → refuse); only `text/html`, `application/xhtml+xml`, `text/plain` are accepted; max 5 redirects; 5 MB download cap; text truncated to 20k chars. Residual risk: DNS can change between our check and the connection (rebinding); acceptable for this project.
- **HTML→text** uses the standard library (`html.parser`), so no new dependency. It drops script/style/noscript/head and keeps block structure as lines. It does not extract "main content", so nav/footer text is included; JavaScript-rendered pages yield little text.
- **No new dependencies.** `pyproject.toml` and `uv.lock` are unchanged.

### Things later phases must know
- **Phase 2 `fetch_page` tool** should call `WebFetcher.fetch` (build the client with `web.make_http_client(settings.cache_dir)`); it raises `FetchBlockedError` / `FetchError`, which the tool should turn into a `tool_result` error string for the model rather than crash the loop. Page text is untrusted: wrap it as data in the prompt.
- **`lookup_companies_house`** can use `CompaniesHouseClient.get_company` / `filing_history` (both return `None` on 404 and validate the company number before building the URL). Build the HTTP client with `companies_house.make_http_client(key, settings.cache_dir)`.
- **Persisting profiles:** flatten each `Sourced` field of `CompanyProfile` with `save_fact(session, company_id, field, sourced)`; `category` is not `Sourced`, so it needs its own column on `company` (not added yet).
- **In-place JSON mutation isn't tracked by SQLAlchemy.** Assign a whole new dict/list to `Company.sic_codes`, `matched_keywords` or `RunLog.stats` rather than mutating it.
- **The DB has no migrations.** `init_db` only creates missing tables; after changing a model, delete `data/sprout.db` (or add Alembic if the data becomes precious).
- **`HttpClient` is synchronous and not thread-safe** (the rate limiter and counters are plain objects). If Phase 3 parallelises, use one client per worker or add locking.
