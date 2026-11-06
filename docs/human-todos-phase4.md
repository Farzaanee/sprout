# Human to-dos, Phase 4 (Evals & guardrails)

Source: `docs/architecture/5. risks.md` section 6.3 and the Phase 4 row in `docs/architecture/7. status.md`, cross-checked against `docs/project-plan.md`. Phase 4 is design-only right now, nothing in `evals/` is built yet.

## Actions that need you specifically (not just review)

- [ ] **Explicitly opt in to live injection testing** by setting `SPROUT_RUN_LIVE_INJECTION=1` when you want the live canary tier to run (it spends money and is skipped otherwise).
- [ ] **Review false positives** from the number-match fabrication check after the first real run (it can miss spelled-out or summed figures, so expect some to review manually).
- [ ] **Run the baseline eval**, then set `GATES` one company below the result, and record the run's table in `evals/results.md`. Repeat as you iterate on prompts.

## Carried over from earlier phases, still outstanding

See `docs/implementation-notes.md` Phase 2 open items: pilot profile review, and the search-price invoice check against `costs.py` (D9).

## Final acceptance check (do this last)

- [ ] `uv run python -m evals.run_evals` runs in one command and prints the metrics table.
- [ ] Baseline thresholds hold: category accuracy ≥ 85%, fabricated-funding rate ≤ 3% (adjust once, after the first run, then treat as a regression gate).
- [ ] All injection fixtures leave the output unchanged, each as a pytest case.
- [ ] SSRF/private-IP URLs are rejected in tests.
