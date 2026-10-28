# Sprout: Agent Performance Log

One row per phase (or per notable task), newest last. Maintained by the Senior Developer agent at the end of each phase; see "How to fill this in" below. Companion to [implementation-notes.md](implementation-notes.md), which records *what* was built; this file records *how many tokens it took*.

Token counts are **measured**; Complexity and Effort are recorded labels (see below). Cost and wall time are not recorded: prices change and idle time (waiting on keys, the user) distorts the clock, whereas the counts are read straight from the session transcripts.

| Date | Task | Complexity | Effort | Model | Requests | Input (fresh) | Output | Cache read | Cache write |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-11 | Phase 0 - Setup & definitions | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| 2026-10-14 | Phase 1 - Data foundations | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| 2026-10-15 | Streamlit progress & test console (ahead of Phase 6) | Medium | n/a | claude-sonnet-5-5 | 9 | 18 | 12,053 | 596,142 | 21,827 |
| 2026-10-17 | Create architect and full-stack-developer agents; set model/effort; reverse agent symlink | Low | low | claude-sonnet-5-5 | 53 | 106 | 34,340 | 3,140,375 | 151,708 |
| 2026-10-17 | Architecture doc via architect agent; move docs/ and scripts/; README; architect agent permissions and end-of-phase sync | Medium | low (main), medium (architect) | claude-sonnet-5-5 | 42 | 84 | 27,700 | 2,826,142 | 132,531 |
| 2026-10-19 | Walkthrough of Phase 1 ingestion code (ingest.py, candidates.py, sources/*.py); update learnings.md Phase 1 with learnings and key-files tables; create learning-transcripter agent | Low | medium | claude-sonnet-5 | 51 | 102 | 26,825 | 3,743,113 | 98,129 |
| 2026-10-19 | Phase 2 - Stage 1: architecture update (architect agent, forward-looking, docs/architecture.md) | Medium | high (architect frontmatter) | claude-sonnet-5-5 | 19 | 38 | 157 | 953,134 | 131,083 |
| 2026-10-19 | Phase 2 - Stage 2: implementation (senior-developer agent: costs, loop, tools, enrich, db, tests, live 10-company run; plus orchestration and implementation-notes) | High | high (senior-developer frontmatter) | claude-sonnet-5-5 | 57 | 114 | 496 | 8,792,997 | 444,484 |
| 2026-10-19 | Phase 2 - Stage 3: end-of-phase architecture sync (architect) and learnings.md (learning-transcripter); plus orchestration | Medium | high (architect), medium (learning-transcripter), per frontmatter | claude-sonnet-5-5 | 38 | 76 | 453 | 2,609,887 | 262,034 |
| 2026-10-25 | Phase 3 - Stage 1: architecture update (architect agent, forward-looking, docs/architecture.md; phase renumbering) | Medium | high (architect frontmatter) | claude-sonnet-5 | 13 | 26 | 3,509 | 711,816 | 13,040 |
| 2026-10-25 | Phase 3 - Stage 1: architecture update (architect agent, forward-looking, docs/architecture.md; phase renumbering) | Medium | high (architect frontmatter) | claude-sonnet-5-5 | 21 | 46 | 324 | 1,906,260 | 255,142 |
| 2026-10-28 | Phase 3 (Observability) - Stage 1: architecture update (architect agent, forward-looking, docs/architecture.md), plus review and orchestration | Medium | high (architect frontmatter) | claude-sonnet-5 | 13 | 26 | 3,688 | 1,056,592 | 11,723 |
| 2026-10-28 | Phase 3 (Observability) - Stage 1: architecture update (architect agent, forward-looking, docs/architecture.md), plus review and orchestration | Medium | high (architect frontmatter) | claude-sonnet-5-5 | 20 | 42 | 322 | 1,252,693 | 159,886 |
| 2026-10-28 | Phase 3 (Observability) - Stage 2: implementation (senior-developer: loop steps, enrich wiring, live check; full-stack-developer: queries and dashboard tab; plus orchestration, implementation notes, step 3c findings batch and a legacy-cost fix) | High | high (agent frontmatter) | claude-sonnet-5 | 12 | 24 | 6,356 | 1,177,686 | 17,755 |
| 2026-10-28 | Phase 3 (Observability) - Stage 2: implementation (senior-developer: loop steps, enrich wiring, live check; full-stack-developer: queries and dashboard tab; plus orchestration, implementation notes, step 3c findings batch and a legacy-cost fix) | High | high (agent frontmatter) | claude-sonnet-5-5 | 118 | 240 | 2,083 | 5,622,218 | 389,488 |

Phase 0 and Phase 1 were written before this log existed, and usage for those sessions was not captured, so they are `n/a` rather than estimates.

## How to fill this in

1. **At the start of the phase**, record the UTC time: `date -u +%Y-%m-%dT%H:%M:%SZ`.
2. **At the end**, run `uv run python scripts/session_usage.py --since <that timestamp>`. It reads this project's Claude Code transcripts (`~/.claude/projects/<project>/*.jsonl`) and prints one Markdown row per model. No manual input is needed.
3. Copy each model's counts into one row of the table (one row per model if several were used), and fill in Complexity and Effort.

Columns:

- **Date**: ISO date the phase was finished.
- **Task**: phase number and title from [project-plan.md](project-plan.md), plus any notable follow-up (e.g. "Phase 1 - key fix").
- **Complexity**: the agent's own assessment of how hard the task was, on a fixed scale: `Low` (small, well-understood change), `Medium` (several files or some design choices), `High` (cross-cutting, new design, or significant unknowns). Judge it from the task, not from the token count.
- **Effort**: the reasoning effort the agent ran with (`low`, `medium`, `high`, `xhigh`, `max`), read from the agent's frontmatter `effort:` or the session setting. Write `n/a` if it cannot be determined; never guess.
- **Model**: model ID as reported by the transcript.
- **Requests**: number of model API calls (unique messages).
- **Input (fresh)**: input tokens *not* served from cache (uncached input only).
- **Output**: output tokens, including thinking tokens.
- **Cache read / Cache write**: prompt-cache tokens read and written.

If the script cannot be run or finds no transcripts, write `n/a` and say why in the notes. Never estimate or round counts.

## Notes per row

Add a short entry per phase below: the `--since` timestamp used, and anything that distorts the counts.

Known limits of the script: it counts only what is in the transcripts (usage of a model called by a tool, such as an advisor model, is not included unless it is written there), and a window that overlaps unrelated work in the same session includes that work too.

- **Phase 0 / Phase 1**: usage not captured (see above).
- **Streamlit progress & test console**: `--since 2026-10-15T19:30:45Z`, measured just before the final docs edit, so the last few requests are not included. Not a numbered phase.
- **Agent setup (2026-10-17)**: `--since 2026-10-17T07:25:00Z` (the session's first message). Not a numbered phase. Includes the general Q&A turns in the same session (sessions, subagent models) and this logging step up to the moment the script ran.
- **Architecture & repo restructure (2026-10-17)**: `--since 2026-10-17T07:55:47Z` (this session's first message). Not a numbered phase. Includes the architect subagent run only if its usage is written in the main transcript (not verified), plus this logging step up to the moment the script ran.
- **Complexity / Effort columns (added 2026-10-17)**: Complexity for earlier rows is a retrospective judgment; Effort was not recorded for them, so it is `n/a`.
- **Effort for the agent-setup row**: `low`, read from the `effort`/`perTurnEffort` metadata on the session transcript's assistant turns (39 of 39). No subagents were run in that session, so the `medium` set in the agent frontmatter did not apply.
- **Effort for the architecture row**: read from the transcript metadata: `effort: low` on the main session turns and `effort: medium` on the architect subagent (which matches its frontmatter).
- **Walkthrough / learnings update (2026-10-19)**: `--since 2026-10-19T17:46:14Z` (this session's first message). Not a numbered phase; no code changed, only explanation, docs edits, and one new agent file. Includes one Explore subagent run (its transcript is nested under the session folder and picked up by the script's recursive search). Effort read from transcript metadata: `medium` on all 67 assistant turns found in the window.
- **Phase 2 Stage 1, architecture (2026-10-19)**: `--since 2026-10-19T19:37:27Z`, measured right after the architect agent finished. Effort is the architect frontmatter value (`high`); not confirmed from transcript metadata. The log file is `docs/performance-log.md` (there is no `docs/agent-performance.md`). The output count looks low for the amount of text written; it is reported as the script measured it.
- **Phase 2 Stage 2, implementation (2026-10-19)**: `--since 2026-10-19T19:47:10Z`, run after the notes were written. Covers the senior-developer subagent (which included live API calls to Anthropic, whose usage is billed separately and is not in these Claude Code transcripts; the agent measured USD 0.42 for its 30 `enrich` runs in `run_log`) and the orchestrator's own polling and docs work. Effort is the agent frontmatter value, not confirmed from transcript metadata. The output count looks low for the amount of work done; it is reported as the script measured it.
- **Phase 2 Stage 3, sync and learnings (2026-10-19)**: `--since 2026-10-19T20:16:51Z`, measured right after the learning-transcripter finished. Includes the end-of-phase architecture sync, the learnings update and the orchestrator's notes edit. Effort values are agent frontmatter, not confirmed from transcript metadata. No git commits were made, at the caller's instruction, so the three planned stage commits are not part of this run.
- **Phase 3 Stage 1, architecture (2026-10-25)**: `--since 2026-10-25T19:55:18Z`, measured after the architect agent finished. Two model rows (claude-sonnet-5 and claude-sonnet-5-5) are the same stage; which model served which agent is not broken out by the script. Includes the phase-implementer's own reading of the docs before the architect ran. The log file is `docs/performance-log.md` (there is no `docs/agent-performance.md`). Effort is the architect frontmatter value, not confirmed from transcript metadata. The output count looks low for the amount of text written; it is reported as the script measured it.
- **Phase 3 (Observability) Stage 1, architecture (2026-10-28)**: `--since 2026-10-28T18:39:31Z`, the timestamp recorded before the architect ran. The first run of the orchestration was interrupted; a fresh orchestrator then reviewed the architect's output and committed it. The two rows replace the architect's own earlier measurement of the same window with one measurement taken after the review, so the counts include the architect agent plus the orchestrator's review and the log edit itself; they are not additive with any earlier row for this stage. Which model served which agent is not broken out by the script. Effort is the architect frontmatter value, not confirmed from transcript metadata.
- **Phase 3 (Observability) Stage 2, implementation (2026-10-28)**: `--since 2026-10-28T18:46:49Z`, re-measured after the step 3c findings batch, the legacy-cost fix and the findings write-up (the first measurement, taken after the implementation notes commit, had 96 sonnet-5-5 requests; this replaces it). The window overlaps the end of the Stage 1 measurement window (that row was taken after review at an unrecorded later time), so some Stage 1 review usage may appear in both rows; the rows are not additive across stages. Covers steps 1 to 5 across several orchestrator instances and the senior-developer and full-stack-developer subagents. The live enrichment calls (about US$0.0376 for four calls in the step 3 check, plus US$0.3049 for the 20-run findings batch) are billed by Anthropic separately and are not in these transcripts. Effort is the agent frontmatter value, not confirmed from transcript metadata. The Output counts look low for the amount of code written; they are reported as the script measured them.
