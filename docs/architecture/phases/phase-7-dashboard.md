# Phase 7: Dashboard

Part of the [Sprout architecture docs](../README.md). Status: see [7. status.md](../7.%20status.md).

```mermaid
flowchart TB
    ENTRY["streamlit_app.py"]
    subgraph UI["app/dashboard.py"]
        T1["Built: Progress, Companies, News, Observability (Phase 4), Run and test"]
        T2["Planned: filters, charts, company detail with sources, big-news feed, Ask box"]
    end
    Q["app/queries.py"]
    SVC["Planned shared service layer"]
    SQL[("SQLite, read-only")]
    DOCS["Plan and notes parsed for Progress tab"]
    ACT["Run and test actions, SPROUT_ENABLE_ACTIONS=1"]
    ING["sprout.ingest.main"]
    LLM(["Anthropic API, Ask box only"])
    ENTRY --> UI
    T1 --> Q
    T2 -.-> SVC
    Q --> SQL
    Q --> DOCS
    SVC -.-> SQL
    T1 --> ACT
    ACT --> ING
    ING --> SQL
    T2 -.-> LLM
```

The Observability tab (built in Phase 4) is specified on the [Phase 4 page](phase-4-observability.md#dashboard-tab-appdashboardpy). The winning-value rule (needed before this phase reads facts) and the service-layer extraction are A6 in [risks and ambiguities](../5.%20risks.md) and D29 in [decisions](../6.%20decisions.md).

What Phase 6 leaves for this phase (read-side only, no schema work): the news timeline and big-news feed need a `company_news` join (`search_news` returns bare `news_item` rows today; `news_kind IS NOT NULL` is big news); a displayed confidence must be the post-verification one, and the verdict comes from the winning `verification:<claim>` fact (`company_facts` lists those rows today, so the fact table must label or filter them); the category rationale is on the classification run's `stats` (D41, no column); per-company status and cost come from `status.py`, not from new SQL; the category chart reads `company.category`, which the classifier now owns. `get_engine` turns on WAL for every caller, and it must tolerate a read-only snapshot file. The Run and test tab is a second spender next to the orchestrator CLI (A29). The live 50-company data is thin (most companies `Other`, few with funding rounds or news; see the [notes](../../implementation-notes.md#phase-6---multi-agent-pipeline-at-scale)), so the coverage indicators the plan asks for matter, and "not disclosed" must render as such.
