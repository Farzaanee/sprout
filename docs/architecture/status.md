# Status per phase

Part of the [Sprout architecture docs](README.md).

## 7. Status per phase

| Phase | Hours | Status | Outstanding |
|---|---|---|---|
| 0 Setup and definitions | 2 | Done, one criterion unverified | Claude hello call unverified (no key then); CI action versions unconfirmed |
| 1 Data foundations | 6 | Done (876 candidates, 115 news items, second run made 0 HTTP requests; the DB inspected in Phase 2 held 305 companies, now 314, see notes) | Candidate quality review |
| 2 Single-company enrichment | 8 | Done, with caveats (169 tests pass, ruff clean; 10/10 live pilot, average USD 0.0138 per company) | Your review of the 10 profiles; invoice check of the search price; tiny-cap criterion verified with mocks only; no `-m live` enrichment test; `--strong` untested live; no batch-level cap; `data/sprout.db` tracked by git |
| 3 Observability | 5 | Design done; not started | `failures.py`, `run_log_step` and `failure_category` migration, `StepRecord` in the loop, enrich wiring, five query functions, Observability tab, a fresh logged batch and the findings note for Phase 4 |
| 4 Evals and guardrails | 6 | Design done; not started; may be adjusted by the Phase 3 findings (A30) | Golden set labels (you), `run_evals`, metrics, judge and calibration, injection suite, `UrlPolicy`, fetch deadline, `BatchBudget`, baseline run and gates; partial SSRF protections already in `web.py` |
| 5 Multi-agent pipeline | 8 | Not started | Everything plus schema additions; `loop.py`, `tools.py` pattern and `costs.py` are ready to reuse (D17); the orchestrator must reuse `BatchBudget` |
| 6 Dashboard | 5 | Partial: progress and test console only | Filters, charts, company detail, big-news feed, snapshot DB, deployment |
| 7 MCP server | 6 | Not started (`mcp` dependency installed) | Everything; `app/queries.py` seeds the shared layer |
| 8 Automation, deployment, docs | 4 | Not started (basic CI exists) | `refresh.yml`, eval job, Dockerfile, README, hosting, demo |
