# Phase 6: Dashboard

Status: partial (progress and test console exists). Part of the [Sprout architecture docs](README.md).

```mermaid
flowchart TB
    ENTRY["streamlit_app.py"]
    subgraph UI["app/dashboard.py"]
        T1["Built: Progress, Companies, News, Run and test. Planned (Phase 3): Observability"]
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

The Observability tab planned for Phase 3 is specified on the [Phase 3 page](phase-3-observability.md#dashboard-tab-appdashboardpy). The winning-value rule (needed before this phase reads facts) and the service-layer extraction are D10 and A17 in the [overview](overview.md).
