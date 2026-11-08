# Phase 8: MCP server (planned)

Part of the [Sprout architecture docs](../README.md).

```mermaid
flowchart TB
    subgraph CLIENTS["MCP clients"]
        CD(["Claude Desktop"])
        CC(["Claude Code"])
        INSP(["MCP Inspector"])
    end
    subgraph SRV["mcp_server.py: FastMCP, stdio, optional HTTP"]
        RO["Read-only tools: search_companies, get_company, list_categories, latest_news"]
        RW["refresh_company, cost cap server-side"]
        RES["Resource sprout://categories"]
        PRM["Prompt market_brief"]
    end
    SVC["Shared service layer: sprout.queries"]
    SQL[("SQLite")]
    ORCH["Orchestrator: single-company run"]
    MCPC["Enrichment agent MCP client"]
    EXTMCP(["Off-the-shelf fetch MCP server"])
    CD -- stdio --> SRV
    CC -- stdio --> SRV
    INSP --> SRV
    RO --> SVC
    RES --> SVC
    PRM --> SVC
    SVC --> SQL
    RW --> ORCH
    ORCH --> SQL
    ORCH --> MCPC
    MCPC --> EXTMCP
```

- A stdio server must never write to stdout; log to stderr.
- **[AMBIGUOUS]** Replacing `fetch_page` with a third-party MCP server discards `WebFetcher` protections (see A2 in [risks and ambiguities](../5.%20risks.md)). Wrap `WebFetcher`, or keep the replacement behind a flag.
- Phase 5 consequences of the `fetch_page` swap: `ALLOWED_ENRICHMENT_TOOLS` is a fail-closed name check, so a differently named MCP fetch tool must be added to it deliberately, and from Phase 6 the same goes for `ALLOWED_CLASSIFY_TOOLS`, `ALLOWED_NEWS_TOOLS` and `ALLOWED_VERIFY_TOOLS`, which all offer `fetch_page`; the MCP-backed handler must still feed `SourceLedger` (provenance, D11, and `stats["sources"]`) and apply `UrlPolicy`, size caps and robots; `fixture_fetcher` injection tests exercise only the `WebFetcher` path, so the swapped path needs its own fixture server or stays flagged; run the golden set on whichever path ships.
- **[AMBIGUOUS]** `refresh_company` is a model-invoked paid write (A31 in [risks and ambiguities](../5.%20risks.md)). Keep it on the MCP server only, never in the enrichment agent's tools (D30 in [decisions](../6.%20decisions.md)). Add a cooldown and return status plus cost rather than blocking. A server-side `BatchBudget` (Phase 5, D24, now lock-protected, D38) shared across calls is the natural cap.

`app/queries.py` moves to `sprout/queries.py` (built in [Phase 3](phase-3-enrichment.md), extended in [Phase 4](phase-4-observability.md)) before this phase, so MCP does not import from the UI package.

What Phase 6 gives `refresh_company`: the orchestrator's `process_company(worker, company_id, steps, ctx, *, force=False)` is a synchronous, non-raising, non-printing function (safe inside a stdio server, which must not write to stdout); `open_worker(settings, engine, stack)` builds the per-call `Worker` and the caller supplies a `RunContext` (Anthropic client, `BatchBudget`, `RateLimitGate`, stop event, `StatusPolicy`, `now`). `steps` is the set of allowed steps; the function works out which are due. The cooldown and the status reply come from `status.py` (`company_statuses`, `last_ok_at`, per-step state, cost), which imports only `db`, so the read-only tools need no agent import. A refresh runs the same steps and caps as the CLI ([cross-cutting 9](../4.%20cross-cutting.md#9-phase-6-agent-caps-and-run-budget)); one call can cost up to about US$0.29 at the per-run caps. `latest_news(category, days)` needs the `company_news` join and `get_company` the winning facts plus `verification:<claim>` verdicts, both in the shared layer extracted from `app/queries.py`.
