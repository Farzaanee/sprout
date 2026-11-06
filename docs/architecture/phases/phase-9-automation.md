# Phase 9: Automation, deployment, docs (planned)

Part of the [Sprout architecture docs](../README.md).

```mermaid
flowchart TB
    subgraph GH["GitHub Actions"]
        CI["ci.yml (exists)"]
        EVAL["On-demand eval job"]
        REF["refresh.yml: weekly cron, hard cost cap"]
    end
    SNAP[("Snapshot DB: release asset or artifact")]
    DOCKER["Dockerfile"]
    HOST(["Streamlit Cloud or HF Spaces, read-only"])
    README["README"]
    DEMO["Demo recording"]
    SECRETS["Repo secrets"]
    SECRETS --> EVAL
    SECRETS --> REF
    REF --> SNAP
    SNAP --> HOST
    SNAP --> DOCKER
    CI --> EVAL
    DOCKER --> HOST
    HOST --> DEMO
    README --> DEMO
```

The on-demand eval job runs `python -m evals.run_evals --max-total-usd N` (exit 0 pass, 1 gate failed, 2 incomplete or misconfigured) with the Anthropic and Companies House keys as secrets, so it cannot run for fork PRs. It starts with a cold `.cache/http`, so every cited page is refetched politely and the run is slower than a local one. Upload the table as an artifact; `evals/results.md` is committed by hand after a baseline or prompt change, and the README quotes its latest table.

Snapshot delivery (A28 in [risks and ambiguities](../5.%20risks.md)) and data licensing/robots recheck are open before this phase.
