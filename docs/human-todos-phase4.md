# Human to-dos, Phase 4 (Evals & guardrails)

Source: `docs/architecture/risks.md` section 6.3 and the Phase 4 row in `docs/architecture/status.md`, cross-checked against `docs/project-plan.md`. Phase 4 is design-only right now, nothing in `evals/` is built yet. (Renamed from `human-todos-phase3.md`: Phase 3 is now Observability, inserted ahead of evals.)

## Decisions only you can make (before or during implementation)

- [ ] **Decide on retry and partial-facts behaviour for failed enrichment runs.** Today a failed run (`invalid`, `max_iter`, `budget`, `refused`, `error`, ...) persists nothing but the `run_log` row, costs money, and is never retried automatically; you have to rerun the CLI by hand. The docs note an earlier "keep partial facts" design was dropped (`docs/implementation-notes.md:198`), and currently scope retry plus an `enrichment_state` table to Phase 5 (`docs/architecture/risks.md`, section 6.1/6.3). Decide whether Phase 4 should pull any of this forward (even a simple retry-on-rerun) or leave it fully for Phase 5, since it affects how you read the eval numbers (repeated failures vs. one-shot).

## Actions that need you specifically (not just review)

- [ ] **Spot-check the LLM judge** against your own judgement on the free-text fields (business model, target user), not just trust the calibration percentage.
- [ ] **Explicitly opt in to live injection testing** by setting `SPROUT_RUN_LIVE_INJECTION=1` when you want the live canary tier to run (it spends money and is skipped otherwise).
- [ ] **Review false positives** from the number-match fabrication check after the first real run (it can miss spelled-out or summed figures, so expect some to review manually).
- [ ] **Run the baseline eval**, then set `GATES` one company below the result, and record the run's table in `evals/results.md`. Repeat as you iterate on prompts.
- [ ] **Confirm the eval budget cap in practice**, not just the confirmed US$1.50 default, by watching actual spend on the first few runs.

## Carried over from earlier phases, still outstanding

- [ ] **Review the 10 live pilot profiles yourself** (the existing review was done by the implementing agent, not you). (Phase 2)
- [ ] **Check the first Anthropic/search console invoice** against the assumed US$10 per 1,000 searches price used in `costs.py` (D14). (Phase 2)

## Final acceptance check (do this last)

- [ ] `uv run python -m evals.run_evals` runs in one command and prints the metrics table.
- [ ] Baseline thresholds hold: category accuracy ≥ 85%, fabricated-funding rate ≤ 3% (adjust once, after the first run, then treat as a regression gate).
- [ ] All injection fixtures leave the output unchanged, each as a pytest case.
- [ ] SSRF/private-IP URLs are rejected in tests.
