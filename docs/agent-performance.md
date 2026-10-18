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
