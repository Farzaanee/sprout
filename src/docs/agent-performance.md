# Sprout — Agent Performance Log

One row per phase (or per notable task), newest last. Maintained by the Senior Developer agent at the end of each phase; see "How to fill this in" below. Companion to [implementation-notes.md](implementation-notes.md), which records *what* was built; this file records *how many tokens it took*.

Only **measured token counts** are logged. Cost and wall time are not recorded: prices change and idle time (waiting on keys, the user) distorts the clock, whereas the counts are read straight from the session transcripts.

| Date | Task | Model | Requests | Input (fresh) | Output | Cache read | Cache write |
|---|---|---|---|---|---|---|---|
| 2026-10-11 | Phase 0 — Setup & definitions | n/a | n/a | n/a | n/a | n/a | n/a |
| 2026-10-14 | Phase 1 — Data foundations | n/a | n/a | n/a | n/a | n/a | n/a |
| 2026-10-15 | Streamlit progress & test console (ahead of Phase 6) | claude-sonnet-5-5 | 9 | 18 | 12,053 | 596,142 | 21,827 |

Both rows were written before this log existed, and usage for those sessions was not captured, so they are `n/a` rather than estimates.

## How to fill this in

1. **At the start of the phase**, record the UTC time: `date -u +%Y-%m-%dT%H:%M:%SZ`.
2. **At the end**, run `uv run python -m sprout.session_usage --since <that timestamp>`. It reads this project's Claude Code transcripts (`~/.claude/projects/<project>/*.jsonl`) and prints one Markdown row per model. No manual input is needed.
3. Copy each model's counts into one row of the table (one row per model if several were used).

Columns:

- **Date**: ISO date the phase was finished.
- **Task**: phase number and title from [project-plan.md](project-plan.md), plus any notable follow-up (e.g. "Phase 1 — key fix").
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
