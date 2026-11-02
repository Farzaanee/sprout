# Sprout: Learnings - Phase 1 - Data foundations

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
| Taxonomy locked, left unwired | The `Category` taxonomy is deliberately locked in Phase 0 but left unwired through all of Phase 1: `ingest.py`/`candidates.py`/`db.py` never reference it, since the heuristic filter only produces uncategorised candidates by design. Assigning a category is Phase 5's job (a dedicated classifier agent), not something to bolt onto discovery early. |

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
