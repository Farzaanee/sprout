# Sprout Architecture

Sources: `docs/project-plan.md`, `docs/implementation-notes.md`, and the code under `src/sprout/`. Test counts: the end-of-phase sync of 2026-10-19 re-ran the suite (169 passed, `ruff check` clean). **[AMBIGUOUS]** marks unclear or conflicting parts of the plan. **[DRIFT]** marks places where code differs from the plan or from an earlier version of this document.

Phase 2 (single-company enrichment) was synced against the code on 2026-10-19: the Phase 2 page, module boundaries, cross-cutting concerns, decisions D13-D18, risks and status describe built code unless marked otherwise. Phase 4 (evals and guardrails) was designed on 2026-10-25, before any of its code exists: see the Phase 4 page, decisions D19-D26 and ambiguities A18-A26; everything marked planned there is design, not built. Phase 3 (observability) was designed on 2026-10-28, also before any of its code exists: see the Phase 3 page, decisions D27-D31 and ambiguities A27-A31; everything marked planned there is design, not built. Phases 5 onward are still outline. The phases were renumbered to match the current plan (0 Setup, 1 Data foundations, 2 Single-company enrichment, 3 Observability, 4 Evals and guardrails, 5 Multi-agent pipeline, 6 Dashboard, 7 MCP server, 8 Automation).

This document used to be one file (`docs/architecture.md`). It is now split by concern, each page below is self-contained except for the cross-references it makes to the others.

## Current state

- [Overview, modules, data model](overview.md) — goals, legend, dependency direction, built-module table, and the data model.
- [Cross-cutting concerns](cross-cutting.md) — sourcing/provenance, agent orchestration, error handling, testing, configuration, security, cost and observability (4.1-4.8).
- [Key design decisions](decisions.md) — the numbered decision log, D1-D31.
- [Risks and open questions](risks.md) — the risk register, plan ambiguities A1-A31, and what's left to resolve by phase.
- [Status per phase](status.md) — hours, status and outstanding items for each phase.

## Diagrams and designs by phase

| Phase | Page | Status |
|---|---|---|
| 0 | [Setup and definitions](phase-0-setup.md) | Done, one criterion unverified |
| 1 | [Data foundations](phase-1-data-foundations.md) | Done |
| 2 | [Single-company enrichment agent](phase-2-enrichment.md) | Done, with caveats |
| 3 | [Observability](phase-3-observability.md) | Design, not built |
| 4 | [Evals and guardrails](phase-4-evals-guardrails.md) | Design, not built |
| 5 | [Multi-agent pipeline](phase-5-multi-agent.md) | Planned |
| 6 | [Dashboard](phase-6-dashboard.md) | Partial: progress and test console exists |
| 7 | [MCP server](phase-7-mcp-server.md) | Planned |
| 8 | [Automation, deployment, docs](phase-8-automation.md) | Planned |
| - | [End to end](end-to-end.md) | Composite diagram across all phases |

Legend (used across the phase diagrams): cylinders are storage, rounded boxes are external services, dotted arrows are planned (in the Phase 2 diagram only, a dotted arrow instead marks work done by Anthropic's servers rather than our code).
