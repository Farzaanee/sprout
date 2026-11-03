# Sprout Architecture

Sources: `docs/project-plan.md`, `docs/implementation-notes.md`, and the code under `src/sprout/`.

This architecture is split by concern; each page below is self-contained except for the cross-references it makes to the others.

## Current state

- [Overview and modules](1.%20overview.md): goals, legend, dependency direction, and the end-to-end composite diagram.
- [Data model](2.%20data-model.md): the SQLite schema (built through Phase 3) and the Phase 5 additions.
- [Built modules](3.%20built-modules.md): the built-module table, with the phase each module was added in.
- [Cross-cutting concerns](4.%20cross-cutting.md): sourcing/provenance, agent orchestration, error handling, testing, configuration, security, cost and observability.
- [Risks and open questions](5.%20risks.md): the risk register, open ambiguities A1-A32, and what's left to resolve by phase.
- [Key design decisions](6.%20decisions.md): the numbered decision log, D1-D33.
- [Status per phase](7.%20status.md): status and outstanding items for each phase.

## Diagrams and designs by phase

Phase pages live under `docs/architecture/phases/`.

| Phase | Page | Status |
|---|---|---|
| 0 | [Setup and definitions](phases/phase-0-setup.md) | Done |
| 1 | [Data foundations](phases/phase-1-data-foundations.md) | Done |
| 2 | [Single-company enrichment agent](phases/phase-2-enrichment.md) | Done, with caveats |
| 3 | [Observability](phases/phase-3-observability.md) | Done, one live check not achieved |
| 4 | [Evals and guardrails](phases/phase-4-evals-guardrails.md) | Design only |
| 5 | [Multi-agent pipeline](phases/phase-5-multi-agent.md) | Planned |
| 6 | [Dashboard](phases/phase-6-dashboard.md) | Partial |
| 7 | [MCP server](phases/phase-7-mcp-server.md) | Planned |
| 8 | [Automation, deployment, docs](phases/phase-8-automation.md) | Planned |

Legend (used across the phase diagrams): cylinders are storage, rounded boxes are external services, dotted arrows are planned (in the Phase 2 diagram only, a dotted arrow instead marks work done by Anthropic's servers rather than our code).
