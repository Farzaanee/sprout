# Status per phase

Part of the [Sprout architecture docs](README.md).

## 7. Status per phase

| Phase | Status | Outstanding |
|---|---|---|
| 0 Setup and definitions | Done | CI action versions unconfirmed |
| 1 Data foundations | Done | Candidate quality review |
| 2 Single-company enrichment | Built, pilot run reviewed by the implementer only | User review of the pilot profiles; invoice check of the search price; no live `-m live` enrichment test; `--strong` untested live; no batch-level cap; `data/sprout.db` tracked by git |
| 3 Observability | Design only | `failures.py`, `run_log_step` and `failure_category` migration, `StepRecord` in the loop, enrich wiring, five query functions, Observability tab, a fresh logged batch and findings note for Phase 4 |
| 4 Evals and guardrails | Design only, may be adjusted by Phase 3 findings | Golden set labels, `run_evals`, metrics, judge and calibration, injection suite, `UrlPolicy`, fetch deadline, `BatchBudget`, baseline run and gates |
| 5 Multi-agent pipeline | Not started | Everything, plus schema additions; `loop.py`, `tools.py` pattern and `costs.py` are ready to reuse; the orchestrator must reuse `BatchBudget` |
| 6 Dashboard | Partial: progress and test console only | Filters, charts, company detail, big-news feed, snapshot DB, deployment |
| 7 MCP server | Not started | Everything; `app/queries.py` seeds the shared layer |
| 8 Automation, deployment, docs | Not started, basic CI exists | `refresh.yml`, eval job, Dockerfile, README, hosting, demo |
