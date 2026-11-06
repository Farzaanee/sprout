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
