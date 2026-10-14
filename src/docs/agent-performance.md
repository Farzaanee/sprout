# Sprout — Agent Performance Log

One row per phase (or per notable task), newest last. Maintained by the Senior Developer agent at the end of each phase; see "How to fill this in" below. Companion to [implementation-notes.md](implementation-notes.md), which records *what* was built; this file records *what it cost*.

| Date | Task | Model | Effort | Requests | Input (fresh) | Output | Cache read | Cache write | Est. cost | Wall time |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-11 | Phase 0 — Setup & definitions | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| 2026-10-14 | Phase 1 — Data foundations | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

Both rows were written before this log existed, and usage for those sessions was not captured, so they are `n/a` rather than estimates.

## How to fill this in

- **Date**: ISO date the phase was finished.
- **Task**: phase number and title from [project-plan.md](project-plan.md), plus any notable follow-up (e.g. "Phase 1 — key fix and live verification").
- **Model / Effort**: the model and reasoning effort the agent ran on (as reported by the session or `/model`).
- **Requests**: number of model API calls.
- **Input (fresh)**: input tokens that were *not* served from cache (uncached input only).
- **Output**: output tokens, including thinking tokens.
- **Cache read / Cache write**: prompt-cache tokens read and written.
- **Est. cost**: estimated from the token columns and the model's current published per-token prices, in USD, with the price source and date in the notes below. If prices are unknown, write `n/a`, not a guess.
- **Wall time**: elapsed time from the start of the phase request to the final report, as `Xh Ym`.

Where the numbers come from, in order of preference: the CLI's usage summary (`/usage` or `/session`), then the `session_usage` view via `session_store_sql`, then provider billing. If none is available, write `n/a` and say why in the notes. Never fabricate values. If a phase spans several sessions or models, add one row per model and say so in the notes.

## Notes per row

Add a short entry per phase below: source of the numbers, price assumptions, and anything that distorted them (retries, blocked external steps such as waiting for an API key, a different model for part of the work).

- **Phase 0 / Phase 1**: usage not captured (see above). Phase 1 wall time also included waiting on an external Companies House key, so it would not have reflected pure agent time.
