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

Key files for this phase (`ingest.py`, `candidates.py`, `sources/*.py`) are documented in [architecture/phase-1-data-foundations.md](architecture/phase-1-data-foundations.md).

## Streamlit progress & test console (ahead of Phase 6)

Not a plan phase, but built early to check progress. **Actual learnings:**
- Streamlit Community Cloud has no persistent disk and the database is gitignored, so a deployed copy always starts empty; a bundled snapshot DB is unavoidable for a populated public demo.
- Gating state-changing UI (an "ingest now" button) behind an environment variable keeps a public deployment read-only by default without separate code paths.
- Driving a "progress" view off the shape of the docs themselves (status/checkbox conventions in this file) is cheap but brittle: the convention has to be kept consistent or the view misreports.
