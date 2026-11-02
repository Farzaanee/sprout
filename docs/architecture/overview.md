# Overview and modules

Part of the [Sprout architecture docs](README.md). See the [data model](data-model.md), [cross-cutting concerns](cross-cutting.md), [decisions](decisions.md), [risks](risks.md) and [status](status.md) for the rest of what used to be this one page, and the phase pages for diagrams and phase-specific design detail.

## 1. Overview and goals

Sprout discovers UK AI startups, groups them by a fixed 16-category taxonomy, and keeps a sourced profile for each (funding, stage, business model, target user, recent news). Data is served through a Streamlit dashboard and an MCP server.

**Goals**
- About 150 sourced profiles; quality measured on a hand-labelled golden set of 30.
- Provenance over completeness. Every fact carries `source_url`, `retrieved_at`, `confidence`. "Not disclosed" is valid. A non-null value without a source is rejected in code.
- Budgeted: about US$26 total API spend, under about US$13 per full run, a cap on every run, cost logged.
- Scraped content is untrusted data, never instructions.

**Non-goals:** embeddings / "similar companies"; complete UK coverage; real-time freshness (weekly refresh).

**Constraints:** one developer, about 45 hours, Python 3.12, SQLite, no paid data vendors. Never cut provenance, evals, or the MCP server.

**Style:** a batch pipeline writes to one SQLite file; two read-mostly front ends (Streamlit, MCP) sit on top. All external I/O goes through narrow adapters (`sources/*`, later the LLM client) so tests mock at those seams.

## 3.1 Dependency direction

```mermaid
flowchart BT
    models["models.py: no I/O"]
    config["config.py"]
    costs["costs.py: no I/O, no sprout imports"]
    failures["failures.py (Phase 3, planned): no I/O, no sprout imports"]
    candidates["candidates.py"]
    http["sources/http.py"]
    sources["sources/companies_house, rss, web"]
    db["db.py"]
    ingest["ingest.py"]
    queries["queries (now app/queries.py)"]
    agents["agents/* (Phase 2 built: loop, tools, enrich)"]
    mcp["mcp_server.py (planned)"]
    dash["app/dashboard.py"]
    evals["evals/* (Phase 4, planned; repo root, not in the package)"]
    candidates --> models
    sources --> http
    sources --> models
    db --> models
    db --> candidates
    db --> sources
    db --> failures
    ingest --> db
    ingest --> sources
    ingest --> candidates
    ingest --> config
    queries --> db
    agents --> db
    agents --> sources
    agents --> models
    agents --> config
    agents --> costs
    agents --> failures
    mcp --> queries
    mcp --> agents
    dash --> queries
    evals --> agents
    evals --> db
    evals --> sources
    evals --> costs
    evals --> config
```

Rules: `models.py` and `costs.py` depend on nothing in `sprout`; `sources/*` never import `db`; front ends never import agents except via the single `refresh_company` path. Within `agents/`, only `loop.py` imports `anthropic` (so SDK drift, error types and the test seam live in one file); `tools.py` builds plain-dict tool specs and never imports the SDK; `agents/enrich.py` is the only agent module that touches `db`. `tools.py` imports `loop.py` only for the `Tool` and `ToolOutcome` types. `hello.py` is a standalone smoke script that also imports `anthropic`; the rule covers pipeline code. `src/sprout/enrich.py` is a three-line `python -m sprout.enrich` shim over `agents.enrich.main`. `failures.py` (Phase 3, planned) is a pure leaf module imported by `db.py` and `agents/enrich.py`; `loop.py` stays DB-free. `evals/` (Phase 4, planned) sits above the package and may import any `sprout` module; nothing in `sprout` imports `evals` (tests do).

`db.py` imports `CompanyHit`, `parse_hit`, `Assessment` and `assess` (`src/sprout/db.py:17-19`), tying storage to one source and filter: `add_company_by_number` turns a Companies House response into a row through `parse_hit` and `assess`. `save_profile` stays clean (models only).

## 3.2 Built modules

See the [built modules page](built-modules.md) for the full table (responsibility, interface, errors, and the phase each module was added in).
