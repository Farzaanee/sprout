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

What Phase 6 gives the weekly job: `python -m sprout.orchestrator --limit N --max-total-usd X` with exit codes 0 (at least 85 percent of the working set ok), 1 (below that, or stopped by budget, rate limit or interrupt) and 2 (configuration, or another run in progress), plus a `pipeline_run` row to report. Cost needs a decision then: at the planned per-company means (about US$0.077 for a full pass, news refreshed every 7 days at about US$0.014) a weekly pass over about 150 companies costs roughly US$2 for news alone plus the enrichment, verification and retry share, which does not fit a US$26 total across several weeks, so lengthen `--news-stale-days` or `--stale-days`, or run `--steps` selectively. `--limit` is a stable working set, so a dataset larger than N is never refreshed beyond its first N. Snapshot the WAL database with `VACUUM INTO` or the backup API, not `cp`, and keep `*.db-wal` and `*.db-shm` out of git and artifacts. The eval job can also take `--agents` (see the [Phase 6 page](phase-6-multi-agent.md#evaluating-each-agent-d28-d41)).
