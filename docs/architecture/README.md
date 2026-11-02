# Sprout Architecture

Sources: `docs/project-plan.md`, `docs/implementation-notes.md`, and the code under `src/sprout/`.

This architecture is split by concern; each page below is self-contained except for the cross-references it makes to the others.

## Current state

- [Overview and modules](overview.md) — goals, legend, and dependency direction.
- [Built modules](built-modules.md) — the built-module table, with the phase each module was added in.
- [Data model](data-model.md) — the SQLite schema, built and planned.
- [Cross-cutting concerns](cross-cutting.md) — sourcing/provenance, agent orchestration, error handling, testing, configuration, security, cost and observability.
- [Key design decisions](decisions.md) — the numbered decision log, D1-D31.
- [Risks and open questions](risks.md) — the risk register, open ambiguities A1-A31, and what's left to resolve by phase.
- [Status per phase](status.md) — status and outstanding items for each phase.

## Diagrams and designs by phase

| Phase | Page | Status |
|---|---|---|
| 0 | [Setup and definitions](phase-0-setup.md) | Done |
| 1 | [Data foundations](phase-1-data-foundations.md) | Done |
| 2 | [Single-company enrichment agent](phase-2-enrichment.md) | Built |
| 3 | [Observability](phase-3-observability.md) | Design only |
| 4 | [Evals and guardrails](phase-4-evals-guardrails.md) | Design only |
| 5 | [Multi-agent pipeline](phase-5-multi-agent.md) | Planned |
| 6 | [Dashboard](phase-6-dashboard.md) | Partial |
| 7 | [MCP server](phase-7-mcp-server.md) | Planned |
| 8 | [Automation, deployment, docs](phase-8-automation.md) | Planned |
| - | [End to end](end-to-end.md) | Composite diagram across all phases |

Legend (used across the phase diagrams): cylinders are storage, rounded boxes are external services, dotted arrows are planned (in the Phase 2 diagram only, a dotted arrow instead marks work done by Anthropic's servers rather than our code).
